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

S1.9: a filler (fault kind `enospc`) leaves `leave_bytes` free on the pool, above
`minfreespace`, so the create policy still selects the pool and the write fails there
(research F10). The client creates a file and appends to a pool file through mount.cifs, and
uploads a file with smbclient; each writes `write_bytes`. Expected: the criteria's errno and
SMB status, no archive branch changed, no file at a new path visible with fewer bytes than
written, the appended file still beginning with its original bytes. The filler and every file
the check wrote are removed afterwards. Scaffolding for the spikes, not product code."""

import json
import shlex
from collections.abc import Mapping
from functools import lru_cache

from s1.checks_placement import changed_branches
from s1.context import Lab, Variant
from s1.fixture import FIXTURE, NEW_LARGE, POOL, POOL_BRANCH

from testlib import faults
from testlib.fixtures import Entry, Fixture, content_sha256, flipped_sha256
from testlib.smb import CifsMount

ARCHIVED = next(e for e in FIXTURE.entries if e.path == "large/archived.bin")
SEED = "S1.9"
APPEND = Entry("s1.9/append.bin", POOL, size=4096)
NEW_PATHS = ("s1.9/new.bin", "s1.9/new-smbclient.bin")
UPLOAD = "/tmp/s1.9-upload.bin"  # client-local source of the smbclient upload

# Writes `size` bytes in 1 MiB blocks (mode wb creates, ab appends), syncs and closes, and
# reports the errno of the first failure: a write through mount.cifs may fail at write,
# fsync or close.
_WRITE_REPORT = """import errno, json, os, sys
path, mode, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
block, written, failed = b"\\xa5" * (1 << 20), 0, None
try:
    with open(path, mode) as handle:
        while written < size:
            written += handle.write(block[: min(len(block), size - written)])
        handle.flush()
        os.fsync(handle.fileno())
except OSError as error:
    failed = errno.errorcode.get(error.errno, str(error.errno))
print(json.dumps({"errno": failed, "written": written}))"""


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


def space_failures(
    table: Mapping,
    *,
    errnos: Mapping[str, str | None],
    smbclient_output: str,
    changed_archive_branches: list[str],
    new_sizes: Mapping[str, int | None],
    append_head_intact: bool,
) -> list[str]:
    failures = [
        f"{what} returned errno {got}, expected {table['errno']}"
        for what, got in errnos.items()
        if got != table["errno"]
    ]
    if table["smb_status"] not in smbclient_output:
        failures.append(f"smbclient did not report {table['smb_status']}")
    failures += [f"archive branch {key} changed" for key in changed_archive_branches]
    failures += [
        f"{path} is visible with {size} of {table['write_bytes']} bytes"
        for path, size in new_sizes.items()
        if size is not None and size < table["write_bytes"]
    ]
    if not append_head_intact:
        failures.append("the appended file no longer begins with its original bytes")
    return failures


def _write(lab: Lab, rel: str, mode: str, size: int) -> dict:
    return json.loads(lab.python(lab.client, _WRITE_REPORT, lab.share.path(rel), mode, str(size)))


def _size(lab: Lab, mount: CifsMount, rel: str) -> int | None:
    status, out = lab.client.execute(f"stat -c %s {shlex.quote(mount.path(rel))}")
    return int(out) if status == 0 else None


def check_pool_out_of_space(lab: Lab) -> None:
    """Each write starts from the precondition: its own filler, and the file an earlier write
    left on the pool removed from the pool branch (an archive copy would stay for the tree
    hashes); otherwise the first write's partial file uses up the free bytes of the next."""
    table = lab.tables["out_of_space"]
    size = table["write_bytes"]
    lab.place(Fixture(SEED, (APPEND,)))
    before = lab.tree_hashes()
    filler, free, new_sizes = {}, {}, {}

    def filled(step: str, write, new_path: str | None = None):
        filler[step] = faults.enospc(lab.server, POOL_BRANCH, table["leave_bytes"], point="S1.9")
        try:
            free[step] = int(lab.server.succeed(f"df --output=avail -B1 {POOL_BRANCH} | tail -n1"))
            result = write()
            if new_path is not None:
                # The default mount caches attributes and may show the writer's own size; the
                # second session (actimeo=0) asks the server.
                new_sizes[new_path] = _size(lab, lab.share, new_path)
                new_sizes[f"{new_path} (second session)"] = _size(lab, lab.second, new_path)
                lab.remove(POOL, new_path)
            return result
        finally:
            lab.server.succeed(f"rm -f {POOL_BRANCH}/{faults.DEFAULT_FILLER}")

    try:
        create = filled("create", lambda: _write(lab, NEW_PATHS[0], "wb", size), NEW_PATHS[0])
        lab.client.succeed(f"truncate -s {size} {UPLOAD}")
        upload = filled("put", lambda: lab.smb.put(UPLOAD, NEW_PATHS[1]), NEW_PATHS[1])
        append = filled("append", lambda: _write(lab, APPEND.path, "ab", size))
        after = lab.tree_hashes()
        head = lab.client.succeed(
            f"head -c {APPEND.size} {shlex.quote(lab.share.path(APPEND.path))} | sha256sum"
        ).split()[0]
    finally:
        for rel in NEW_PATHS:
            _remove_through_share(lab, rel)
        lab.client.succeed(f"rm -f {UPLOAD}")
    failures = space_failures(
        table,
        errnos={"create": create["errno"], "append": append["errno"]},
        smbclient_output=upload[1],
        changed_archive_branches=changed_branches(before, after),
        new_sizes=new_sizes,
        append_head_intact=head == content_sha256(SEED, APPEND),
    )
    lab.required_settings["out_of_space"] = {
        "archive_branch_mode": "NC",
        "moveonenospc": lab.effective("moveonenospc"),
        "minfreespace": lab.effective("minfreespace"),
        "found_by": "S1.9",
    }
    lab.evidence(
        "S1.9-out-of-space",
        {
            "filler": {step: event.detail for step, event in filler.items()},
            "free_bytes_before_writes": free,
            "create": create,
            "append": append,
            "smbclient_put": list(upload),
            "new_sizes": new_sizes,
            "archive_tree_hashes": {"before": before, "after": after},
            "failures": failures,
        },
    )
    assert not failures, "; ".join(failures)
