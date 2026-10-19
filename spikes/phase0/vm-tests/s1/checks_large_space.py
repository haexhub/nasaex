"""Checks S1.8 and S1.9 (task T047): files above 4 GiB end to end, and a full pool.

S1.8, per mount variant (the baseline gates, `passthrough.io=rw` with `cache.files=auto-full`
is informational): the archived file above 4 GiB of the S1 data set is checksummed through
mount.cifs and smbclient, copied within the share (the copy must land on the pool), a new file
above 4 GiB is written through the share (it must land on the pool), and the case flips the
bits of its own byte above 4 GiB of the archived file (criteria tables.large_file): the change
must be in place, on arch1 under the same inode, with no copy on the pool. The expected
content after the flips of every case so far is computed on the host (testlib.fixtures). The
copy and the new file are removed through the share at the end of a case, so that the pool has
room again.

S1.9 (amended by tasks T047a and T047b, spec Clarifications 2026-10-19): a filler (fault kind
`enospc`) leaves `leave_bytes` free on the pool, above `minfreespace`, so the create policy
still selects the pool and the write fails there (research F10). The client creates a file and
appends to a pool file through mount.cifs and uploads a file with smbclient, each from its own
filler and each `write_bytes` of one seeded source that client and server generate alike
(probes.enospc_writes, the probe code of tasks T051b and T051c; a zero-filled leftover can
fail). Judged per write: the criteria's errno or SMB status; the archive branches and trash
directories unchanged (tree hashes, no inotify event on the archive data filesystems during
the write); a remaining written path on the pool branch only; its leftover a byte-exact prefix
of the source (for the append, after the head); the rest of the pool unchanged; the append
head intact. Then the client deletes the leftovers of the create and the upload through the
share, the check waits until the pool branch shows the space of the filler and of those
leftovers again (btrfs frees it only at its next transaction commit, 30 s by default, and
until then mergerfs refuses every create with EROFS; probe T051h), polling statvfs every
0.5 s for at most 60 s, and the client repeats every write without a filler: no error, on the
pool branch only, reads back with the written data. The append is repeated through a mount.cifs
session of its own, since the writer's session appends at its stale size within about half a
second of the failed append (a Linux cifs client artefact, probe T051h) and a hole of zeros
would fall between the leftover and the source; the writer session's size is recorded. The
sizes the writer's session, the second session and the server show and the seconds until the
space appeared are recorded, never judged beyond the bound. The refusal window (task T047c, spec
Clarifications 2026-10-19), the seconds from the end of the deletion until the poll saw the
space, goes beside minfreespace into the required settings, with what an empty mount.cifs create
and an empty smbclient upload right after the deletion reported (recorded, never judged).
Scaffolding for the spikes, not product code."""

import json
import posixpath
import shlex
from collections.abc import Callable, Mapping
from functools import lru_cache
from time import monotonic, sleep

from probes.enospc_writes import SOURCE_SCRIPT, WRITER, leftover
from s1.checks_placement import changed_branches
from s1.context import CACHE, Lab, Variant
from s1.fixture import ARCHIVE, FIXTURE, NEW_LARGE, POOL, POOL_BRANCH, trash

from testlib import faults
from testlib.fixtures import Entry, Fixture, content_sha256, flipped_sha256
from testlib.manifest import Manifest, compare, describe, from_json
from testlib.smb import CifsMount, nt_status

ARCHIVED = next(e for e in FIXTURE.entries if e.path == "large/archived.bin")
SEED = "S1.9"
APPEND = Entry("s1.9/append.bin", POOL, size=4096)
# The three writes of S1.9 and their paths; `put` is the smbclient upload.
WRITES = {"create": "s1.9/new.bin", "put": "s1.9/new-smbclient.bin", "append": APPEND.path}
DELETED_BEFORE_RETRY = ("create", "put")
SOURCE = "/root/s1.9-source.bin"  # the seeded source, generated alike on client and server
EMPTY = "/root/s1.9-empty.bin"  # the client's empty file of the smbclient create in the window
# The empty creates in the refusal window (task T047c), by client implementation.
WINDOW_CREATES = {"mount.cifs": "s1.9/window-create.bin", "smbclient": "s1.9/window-put.bin"}
ARCHIVE_KEYS = (*ARCHIVE, *(trash(serial) for serial in ARCHIVE))
# Classes of probes.enospc_writes.leftover that are a byte-exact prefix of the source.
PREFIX_CLASSES = ("absent", "empty", "complete", "partial, prefix intact")
# The retries wait until f_bavail of the pool branch is back (probe T051h: the freed bytes were
# the filler plus the deleted leftovers plus 0 to 200704 bytes, and 16 MiB was the epsilon used
# there; 0 would have sufficed). The space appeared 0.1 to 31.5 s after the delete, at the next
# btrfs commit (30 s default), so the bound is one commit interval plus slack.
SPACE_EPSILON = 16 * 1024 * 1024
SPACE_POLL_S = 0.5
SPACE_BOUND_S = 60
WATCH = "/run/s1.9-watch"  # inotify log, its stderr and the end-marker directory
WATCH_EVENTS = "modify,attrib,close_write,create,delete,moved_to,moved_from"

# The pool branch's manifest without the filler, as pool_rest of
# probes/enospc_probe_setup.py: every top-level name plus the manifest below each top-level
# directory, so the filler's gigabytes are never read. ponytail: a top-level file is compared
# by name only; S1.9 writes below s1.9/, a full manifest would hash the filler every time.
_POOL_REST = """import os, sys
from pathlib import Path
from testlib.manifest import Item, build_manifest, to_json
root, filler, rest = Path(sys.argv[1]), sys.argv[2], {}
for entry in os.scandir(root):
    if entry.name == filler:
        continue
    rest[entry.name] = Item("top-level", None, 0, None, (), None)
    if entry.is_dir(follow_symlinks=False):
        rest |= {f"{entry.name}/{p}": i for p, i in build_manifest(Path(entry.path)).items()}
print(to_json(rest))"""


def _remove_through_share(lab: Lab, rel: str) -> None:
    lab.client_run(f"rm -f -- {shlex.quote(lab.share.path(rel))}")


@lru_cache(maxsize=8)
def _expected(flips: tuple[int, ...]) -> str:
    """Host-side SHA-256 of the archived large file after the given flips (a few seconds per
    4 GiB, so each state is computed once)."""
    return flipped_sha256(FIXTURE.seed, ARCHIVED, flips)


def check_large_files(lab: Lab, variant: Variant) -> None:
    rel = ARCHIVED.path
    offset = lab.tables["large_file"]["flip_offset"][variant.name]
    copy = f"large/copy-{variant.name}.bin"
    failures: list[str] = []
    seen: dict = {}
    with lab.variant(variant.overrides):
        try:
            before = _expected(lab.flipped.get(rel, ()))
            seen["cifs"] = lab.share.sha256(rel)
            seen["smbclient"] = lab.smb.sha256(rel)
            failures += [
                f"{c} checksum differs" for c in ("cifs", "smbclient") if seen[c] != before
            ]

            seen["copy_status"] = lab.client_run(
                f"cp -- {shlex.quote(lab.share.path(rel))} {shlex.quote(lab.share.path(copy))}"
            )
            if seen["copy_status"][0]:
                failures.append(f"the copy failed: {seen['copy_status'][1].strip()}")
            else:
                seen["copy_sha256"] = lab.share.sha256(copy)
                seen["copy_holders"] = lab.holders(copy)
                if seen["copy_sha256"] != before:
                    failures.append("the copy reads with other content")
                if seen["copy_holders"] != [POOL]:
                    failures.append(f"the copy lies on {seen['copy_holders']}")
            # The pool (about 8 GiB) holds the copy or the new file, not both.
            _remove_through_share(lab, copy)

            lab.write_through(lab.share, NEW_LARGE.path, FIXTURE.seed, NEW_LARGE)
            seen["new_sha256"] = lab.share.sha256(NEW_LARGE.path)
            seen["new_holders"] = lab.holders(NEW_LARGE.path)
            if seen["new_sha256"] != content_sha256(FIXTURE.seed, NEW_LARGE):
                failures.append("the new large file reads with other content")
            if seen["new_holders"] != [POOL]:
                failures.append(f"the new large file lies on {seen['new_holders']}")

            inode = lab.inode(ARCHIVED.branch, rel)
            lab.flip_through(lab.share, rel, (offset,))
            lab.flipped[rel] = (*lab.flipped.get(rel, ()), offset)
            after = _expected(lab.flipped[rel])
            seen["flipped_cifs"] = lab.share.sha256(rel)
            seen["flipped_branch"] = lab.sha256(ARCHIVED.branch, rel)
            seen["flipped_holders"] = lab.holders(rel)
            if seen["flipped_cifs"] != after or seen["flipped_branch"] != after:
                failures.append("the flipped byte did not reach the archived file as expected")
            if lab.inode(ARCHIVED.branch, rel) != inode:
                failures.append("the archived file was replaced, not changed in place")
            if seen["flipped_holders"] != [ARCHIVED.branch]:
                failures.append(f"the archived file lies on {seen['flipped_holders']}")
        finally:
            for leftover in (copy, NEW_LARGE.path):
                _remove_through_share(lab, leftover)
    lab.evidence(
        f"S1.8-{variant.name}",
        {"variant": variant.name, "gating": variant.gating, "flip_offset": offset, **seen},
    )
    assert not failures, "; ".join(failures)


def _write_error(table: Mapping, step: str, report) -> list[str]:
    """(1): a mount.cifs write must fail with the criteria's errno, the smbclient upload must
    name the criteria's SMB status and must not exit 0."""
    if step != "put":
        if report["errno"] == table["errno"]:
            return []
        return [f"{step} returned errno {report['errno']}, expected {table['errno']}"]
    status, output = report
    failures = [f"{step} reported success (exit 0)"] if status == 0 else []
    if table["smb_status"] not in output:
        failures.append(f"{step} did not report {table['smb_status']}")
    return failures


def _retry_error(step: str, report) -> str | None:
    if step != "put":
        return report["errno"] and f"{report['errno']} at {report.get('step')}"
    status, output = report
    if status != 0 or "NT_STATUS_" in output:
        return f"exit {status}: {output.strip()}"
    return None


def space_failures(table: Mapping, writes: Mapping[str, Mapping]) -> list[str]:
    """Failures of S1.9 over what each write (create, put, append) and its retry left; the
    sizes the client sessions and the server show are evidence only and not read here."""
    failures = []
    for step, seen in writes.items():
        failures += _write_error(table, step, seen["report"])
        failures += [f"{step}: {key} changed" for key in seen["archive_changed"]]
        if events := seen["archive_events"]:
            shown = ", ".join(events[:5])
            failures.append(
                f"{step}: {len(events)} write event(s) on the archive filesystems: {shown}"
            )
        if seen["holders"] not in ([], [POOL]):
            failures.append(f"{step}: the written path lies on {seen['holders']}")
        kind = leftover(seen["leftover"], seen["prefix_sha256"], table["write_bytes"])
        if kind not in PREFIX_CLASSES:
            failures.append(
                f"{step}: the leftover on the pool branch is not a prefix of the source ({kind})"
            )
        if seen["pool_rest_differences"]:
            failures.append(f"{step}: other pool files changed: {seen['pool_rest_differences']}")
        if seen.get("head_intact") is False:
            failures.append("the appended file no longer begins with its original bytes")
        retry = seen.get("retry")
        if retry is None:
            failures.append(f"{step}: the retry did not run")
            continue
        if error := _retry_error(step, retry["report"]):
            failures.append(f"{step}: the retry reported an error ({error})")
        if retry["holders"] != [POOL]:
            failures.append(f"{step}: the retry lies on {retry['holders']}")
        if not retry["reads_back"]:
            failures.append(f"{step}: the retry reads back with other data")
    return failures


def freed_space_target(writes: Mapping[str, Mapping], epsilon: int = SPACE_EPSILON) -> int:
    """The bytes f_bavail of the pool branch must reach again before the retries: what the
    filler of the last write occupied (the other fillers are gone, their space is in the
    count the last filler was sized from) plus the leftovers of the writes the client deletes,
    less `epsilon`; the append's leftover stays."""
    filler = list(writes.values())[-1]["filler_bytes"]
    deleted = sum(
        (writes[step]["leftover"] or {"size": 0})["size"] for step in DELETED_BEFORE_RETRY
    )
    return filler + deleted - epsilon


def wait_for_freed_space(
    read: Callable[[], int],
    target: int,
    *,
    bound_s: float = SPACE_BOUND_S,
    interval_s: float = SPACE_POLL_S,
    clock: Callable[[], float] = monotonic,
    sleep: Callable[[float], None] = sleep,
) -> dict:
    """Sample `read` (free bytes) every `interval_s` until it reaches `target` or `bound_s`
    seconds have passed since the first sample; the seconds until the last sample, whether the
    space appeared and the last value are the answer."""
    start, samples = clock(), 0
    while True:
        avail = read()
        samples += 1
        waited = clock() - start
        if avail >= target or waited >= bound_s:
            return {
                "appeared": avail >= target,
                "waited_s": round(waited, 3),
                "avail": avail,
                "target": target,
                "samples": samples,
            }
        sleep(interval_s)


# --- S1.9 on the VMs ------------------------------------------------------------------------


def _size(machine, path: str) -> int | None:
    status, out = machine.execute(f"stat -c %s {shlex.quote(path)}")
    return int(out) if status == 0 else None


def pool_free_bytes(lab: Lab) -> int:
    """f_bavail of the pool branch in bytes (df, statvfs); S1.2 polls it too (task T044a)."""
    return int(lab.server.succeed(f"df --output=avail -B1 {POOL_BRANCH} | tail -n1"))


def _range_sha256(machine, path: str, start: int = 0, length: int | None = None) -> str | None:
    """SHA-256 of `length` bytes (None: to the end) of a guest file from byte `start`; None if
    the file cannot be read."""
    count = "" if length is None else f" count={length}"
    status, out = machine.execute(
        f"dd if={shlex.quote(path)} iflag=skip_bytes,count_bytes skip={start}{count} bs=1M"
        " status=none | sha256sum"
    )
    return out.split()[0] if status == 0 else None


def _client_write(lab: Lab, step: str, rel: str, size: int, mount: CifsMount | None = None):
    """The write of a step from the seeded source: WRITER through a mount.cifs session (the
    default one unless `mount` is given; its report), or smbclient put ([exit status, output])."""
    if step == "put":
        return list(lab.smb.put(SOURCE, rel))
    mode = "ab" if step == "append" else "wb"
    path = (mount or lab.share).path(rel)
    return json.loads(lab.python(lab.client, WRITER, path, mode, SOURCE, str(size)))


def _make_source(lab: Lab, size: int) -> dict[str, str]:
    """The seeded source on both machines; refused unless both have the same SHA-256."""
    machines = {"server": lab.server, "client": lab.client}
    for machine in machines.values():
        lab.python(machine, SOURCE_SCRIPT, SOURCE, str(size))
    shas = {name: m.succeed(f"sha256sum {SOURCE}").split()[0] for name, m in machines.items()}
    if len(set(shas.values())) != 1:
        raise RuntimeError(f"the seeded source differs between the machines: {shas}")
    return shas


def _pool_rest(lab: Lab, rel: str) -> Manifest:
    rest = from_json(lab.python(lab.server, _POOL_REST, POOL_BRANCH, faults.DEFAULT_FILLER))
    return {path: item for path, item in rest.items() if path != rel}


def _watch_start(lab: Lab) -> None:
    """inotify on the archive data filesystems (the parents of branch and trash) and on an
    end-marker directory outside them."""
    data = sorted({posixpath.dirname(lab.path(key)) for key in ARCHIVE_KEYS})
    lab.server.succeed(f"rm -rf {WATCH} && mkdir -p {WATCH}/marker")
    lab.server.succeed(
        f"(inotifywait -m -r -e {WATCH_EVENTS} --format '%e %w%f' {WATCH}/marker "
        f"{' '.join(data)} > {WATCH}/log 2> {WATCH}/err &)"
    )
    lab.server.wait_until_succeeds(f"grep -q 'Watches established' {WATCH}/err", timeout=60)


def _watch_stop(lab: Lab) -> list[str]:
    """The archive events so far. One inotify queue delivers events in order, so once the
    marker created now is in the log, every event of the write before it is too."""
    lab.server.succeed(f"touch {WATCH}/marker/end")
    lab.server.wait_until_succeeds(f"grep -q ' {WATCH}/marker/end$' {WATCH}/log", timeout=60)
    lab.server.succeed("pkill -x inotifywait || true")
    lines = lab.server.succeed(f"cat {WATCH}/log").splitlines()
    return [line for line in lines if line and f" {WATCH}/marker/" not in line]


def _failed_write(lab: Lab, step: str, rel: str, table: Mapping, hashes: dict) -> tuple:
    """One write from the precondition, its own filler removed afterwards; what it left and
    the archive tree hashes after it (the hashes before the next write: only the pool's
    filler changes in between)."""
    seen: dict = {}
    skip = APPEND.size if step == "append" else 0
    try:
        filler = faults.enospc(lab.server, POOL_BRANCH, table["leave_bytes"], point="S1.9")
        seen["filler"] = filler.detail
        seen["filler_bytes"] = int(
            lab.server.succeed(f"stat -c %s {POOL_BRANCH}/{faults.DEFAULT_FILLER}")
        )
        seen["free_before"] = pool_free_bytes(lab)
        pool_before = _pool_rest(lab, rel)
        _watch_start(lab)
        seen["report"] = _client_write(lab, step, rel, table["write_bytes"])
        seen["archive_events"] = _watch_stop(lab)
        seen["sizes_recorded"] = {
            "writer session": _size(lab.client, lab.share.path(rel)),
            "second session (actimeo=0)": _size(lab.client, lab.second.path(rel)),
            "cache pool on the server": _size(lab.server, f"{CACHE}/{rel}"),
        }
        seen["holders"] = lab.holders(rel)
        seen["leftover"], seen["prefix_sha256"] = None, None
        if POOL in seen["holders"]:
            path = lab.path(POOL, rel)
            size = max(0, (_size(lab.server, path) or 0) - skip)
            seen["leftover"] = {"size": size, "sha256": _range_sha256(lab.server, path, skip)}
            seen["prefix_sha256"] = _range_sha256(lab.client, SOURCE, 0, size)
        seen["free_after"] = pool_free_bytes(lab)
        after = lab.tree_hashes(ARCHIVE_KEYS)
        seen["archive_changed"] = changed_branches(hashes, after)
        seen["archive_tree_hashes"] = {"before": hashes, "after": after}
        differences = compare(pool_before, _pool_rest(lab, rel))
        seen["pool_rest_differences"] = describe(differences, limit=None)
        if step == "append":
            head = lab.client.succeed(
                f"head -c {APPEND.size} {shlex.quote(lab.share.path(rel))} | sha256sum"
            ).split()[0]
            seen["head_intact"] = head == content_sha256(SEED, APPEND)
    finally:
        lab.server.execute("pkill -x inotifywait")
        lab.server.succeed(f"rm -f {POOL_BRANCH}/{faults.DEFAULT_FILLER}")
    return seen, after


def _retry(lab: Lab, step: str, rel: str, seen: Mapping, table: Mapping, source: str) -> dict:
    """The same write again without a filler; read back through the second session: the
    source, or for the append the original head, the leftover, then the source. The append
    goes through a mount.cifs session of its own (probe T051h: the writer's session appends at
    its stale size within about 0.5 s of the failed append), the writer session's size before
    it is recorded."""
    retry: dict = {}
    if step == "append":
        sizes = {"writer session before the retry": _size(lab.client, lab.share.path(rel))}
        with lab.fresh_session() as session:
            sizes["fresh session before the retry"] = _size(lab.client, session.path(rel))
            retry["report"] = _client_write(lab, step, rel, table["write_bytes"], session)
        retry["sizes_recorded"] = sizes
    else:
        retry["report"] = _client_write(lab, step, rel, table["write_bytes"])
    retry["holders"] = lab.holders(rel)
    path = lab.second.path(rel)
    if step != "append":
        retry["reads_back"] = _range_sha256(lab.client, path) == source
        return retry
    kept = (seen["leftover"] or {"size": 0})["size"]
    parts = {
        "head": (_range_sha256(lab.client, path, 0, APPEND.size), content_sha256(SEED, APPEND)),
        "leftover": (
            _range_sha256(lab.client, path, APPEND.size, kept),
            _range_sha256(lab.client, SOURCE, 0, kept),
        ),
        "source": (_range_sha256(lab.client, path, APPEND.size + kept), source),
    }
    retry["reads_back"] = all(got == want for got, want in parts.values())
    retry["parts_read_back"] = {part: got == want for part, (got, want) in parts.items()}
    return retry


def _window_creates(lab: Lab) -> dict:
    """One empty create through mount.cifs and one smbclient upload of an empty file, what each
    reported and its errno or SMB status (never judged); a create that succeeded is deleted
    through the share at once, before the poll."""
    cifs = _client_write(lab, "create", WINDOW_CREATES["mount.cifs"], 0)
    put = list(lab.smb.put(EMPTY, WINDOW_CREATES["smbclient"]))
    seen = {
        "mount.cifs": {"report": cifs, "status": cifs["errno"]},
        "smbclient": {"report": put, "status": nt_status(put[1])},
    }
    for (client, rel), step in zip(WINDOW_CREATES.items(), ("create", "put"), strict=True):
        if not _retry_error(step, seen[client]["report"]):
            _remove_through_share(lab, rel)
    return seen


def check_pool_out_of_space(lab: Lab) -> None:
    """Each write starts from the precondition: its own filler, measured after the leftovers
    of earlier writes; the retries follow once every filler is gone (Clarifications
    2026-10-19, proposed (b)). The refusal window counts from the end of the deletion of the
    leftovers, so the time of the empty creates before the poll is part of it (task T047c)."""
    table = lab.tables["out_of_space"]
    lab.place(Fixture(SEED, (APPEND,)))
    writes: dict[str, dict] = {}
    try:
        source = _make_source(lab, table["write_bytes"])
        lab.client.succeed(f"truncate -s 0 {EMPTY}")
        hashes = lab.tree_hashes(ARCHIVE_KEYS)
        for step, rel in WRITES.items():
            writes[step], hashes = _failed_write(lab, step, rel, table, hashes)
        for step in DELETED_BEFORE_RETRY:
            _remove_through_share(lab, WRITES[step])
        deleted = monotonic()
        window = _window_creates(lab)
        target = freed_space_target(writes, SPACE_EPSILON)
        polled = monotonic()
        freed = wait_for_freed_space(
            lambda: pool_free_bytes(lab), target, clock=monotonic, sleep=sleep
        )
        window_s = round(polled - deleted + freed["waited_s"], 3)
        if freed["appeared"]:
            for step, rel in WRITES.items():
                retry = _retry(lab, step, rel, writes[step], table, source["client"])
                writes[step]["retry"] = retry
    finally:
        for step in DELETED_BEFORE_RETRY:
            _remove_through_share(lab, WRITES[step])
        for machine in (lab.server, lab.client):
            machine.execute(f"rm -f {SOURCE} {EMPTY}")
    failures = space_failures(table, writes)
    if not freed["appeared"]:
        failures.insert(
            0,
            f"the freed space never appeared: f_bavail of the pool branch stayed below "
            f"{freed['target']} bytes for {freed['waited_s']} s (bound {SPACE_BOUND_S} s, last "
            f"sample {freed['avail']}), so the retries did not run",
        )
    lab.required_settings["out_of_space"] = {
        "archive_branch_mode": "NC",
        "moveonenospc": lab.effective("moveonenospc"),
        "minfreespace": lab.effective("minfreespace"),
        # A finding, not judged beyond the bound of the poll (spec Clarifications 2026-10-19).
        "create_refused_window_s": window_s,
        "create_refused_window_space_appeared": freed["appeared"],
        "creates_in_window": {client: seen["status"] for client, seen in window.items()},
        "found_by": "S1.9",
    }
    lab.evidence(
        "S1.9-out-of-space",
        {
            "source_sha256": source,
            "writes": writes,
            "freed_space": freed,
            "create_refused_window_s": window_s,
            "creates_in_window": window,
            "space_epsilon": SPACE_EPSILON,
            "failures": failures,
        },
    )
    assert not failures, "; ".join(failures)
