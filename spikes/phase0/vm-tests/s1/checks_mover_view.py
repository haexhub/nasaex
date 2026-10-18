"""Check S1.10 (task T048): a file moved the mover's way keeps the same view through the share.

A pool file with a user xattr is opened by a client reader, which reads its first half and then
waits for the host. The server moves the file with the move primitives of the mover (T030:
copy through the base pool, then unlink of the pool copy) with every hook point of criteria
tables.mover_view.points armed; at each point the mover is held while the client looks at the
file through the default mount (with attribute caching), then released. After the move the
reader finishes, and the client manifest of the file's directory is read until it equals the
manifest from before the move in path, type, size, mtime, mode and content (the "same view" of
the verdict model) or the bound of tables.bounds.view_consistency_s has passed. Past the bound,
the refresh procedure of tables.mover_view.refresh_procedure (client shell commands, run in
order; empty in the draft) is run and the view checked once more. The time to a consistent view,
or the refresh procedure, goes into the required settings (FR-013, data-model
`required_settings`). The points are fault-free observation points: every step is triggered by
the guest reaching a hook, never by a sleep (FR-010). Scaffolding, not product code."""

import json
import shlex
import time
from collections.abc import Mapping

from s1.context import Lab
from s1.fixture import POOL

from testlib import faults
from testlib.fixtures import CHUNK_SIZE, Entry, Fixture, content_sha256
from testlib.manifest import compare, describe

SEED = "S1.10"
DIRECTORY = "s1.10"
MOVED = Entry(
    f"{DIRECTORY}/moved.bin", POOL, size=3 * CHUNK_SIZE + 7, xattrs=(("user.s1", "mover"),)
)
READER = {k: f"/tmp/s1.10-reader.{k}" for k in ("ready", "go", "out", "log")}
MOVER = {k: f"/run/s1.10-mover.{k}" for k in ("rc", "log")}
VIEW_FIELDS_IGNORED = ("xattrs",)  # the same view: path, type, size, mtime, mode, content
POLL_S = 0.5

# Holds the file open across the move: reads the first half, reports ready, waits for the host
# (a file it creates, not a timer), reads the rest through the same handle.
_READER = """import hashlib, json, os, sys, time
path, ready, go, out = sys.argv[1:5]
digest, size = hashlib.sha256(), 0
with open(path, "rb") as handle:
    first = handle.read(os.fstat(handle.fileno()).st_size // 2)
    digest.update(first)
    size += len(first)
    open(ready, "w").close()
    while not os.path.exists(go):
        time.sleep(0.05)
    while chunk := handle.read(1 << 20):
        digest.update(chunk)
        size += len(chunk)
with open(out, "w") as report:
    json.dump({"sha256": digest.hexdigest(), "size": size}, report)"""


def mover_view_failures(
    *,
    steps: Mapping[str, dict],
    expected: Mapping,
    reader: Mapping,
    mover_status: int,
    holders: list[str],
    consistent_after_s: float | None,
    bound_s: float,
    refresh: Mapping | None,
    attributes_kept: bool,
) -> list[str]:
    failures = []
    for point, seen in steps.items():
        if "error" in seen:
            failures.append(f"{point}: the file could not be read: {seen['error']}")
        elif not seen.get("visible"):
            failures.append(f"{point}: the file is not visible through the share")
        elif (seen.get("size"), seen.get("sha256")) != (expected["size"], expected["sha256"]):
            failures.append(f"{point}: the file is partial or differs ({seen.get('size')} bytes)")
    if (reader.get("size"), reader.get("sha256")) != (expected["size"], expected["sha256"]):
        failures.append(f"the open reader read {reader.get('size')} bytes with another hash")
    if mover_status != 0:
        failures.append(f"the mover exited with status {mover_status}")
    if not holders or POOL in holders:
        failures.append(f"after the move the file lies on {holders}")
    in_time = consistent_after_s is not None and consistent_after_s <= bound_s
    if not in_time and not (refresh or {}).get("verified"):
        failures.append(
            f"the view differs after {bound_s:g} s and no refresh procedure is verified"
        )
    if not attributes_kept:
        failures.append("the attributes differ after the move")
    return failures


def _look(lab: Lab) -> dict:
    """The file as the client sees it through the default mount at a held point."""
    try:
        if not lab.share.exists(MOVED.path):
            return {"visible": False, "listing": lab.share.listdir(DIRECTORY)}
        size = int(lab.client.succeed(f"stat -c %s {shlex.quote(lab.share.path(MOVED.path))}"))
        return {
            "visible": True,
            "size": size,
            "sha256": lab.share.sha256(MOVED.path),
            "listing": lab.share.listdir(DIRECTORY),
        }
    except Exception as error:  # noqa: BLE001 - an unreadable file is an outcome
        return {"error": f"{type(error).__name__}: {error}"}


def _wait_for_same_view(lab: Lab, before, bound_s: float) -> tuple[float | None, list]:
    started = time.monotonic()
    while True:
        differences = compare(before, lab.share.manifest(DIRECTORY), ignore=VIEW_FIELDS_IGNORED)
        elapsed = time.monotonic() - started
        if not differences:
            return elapsed, []
        if elapsed > bound_s:
            return None, differences
        time.sleep(POLL_S)  # polling an observation, not triggering anything


def check_mover_view(lab: Lab) -> None:
    table, bound_s = lab.tables["mover_view"], lab.tables["bounds"]["view_consistency_s"]
    lab.place(Fixture(SEED, (MOVED,)))
    before = lab.share.manifest(DIRECTORY)
    expected = {"size": MOVED.size, "sha256": content_sha256(SEED, MOVED)}

    reader_args = " ".join(
        shlex.quote(a)
        for a in (lab.share.path(MOVED.path), *(READER[k] for k in ("ready", "go", "out")))
    )
    lab.client.succeed("rm -f /tmp/s1.10-reader.*")
    lab.client.succeed(f"python3 -c {shlex.quote(_READER)} {reader_args} > {READER['log']} 2>&1 &")
    lab.client.wait_for_file(READER["ready"])

    for point in table["points"]:
        faults.arm(lab.server, point)
    move = lab.move_command(MOVED.path, first=1)
    lab.server.succeed(f"({move}; echo $? > {MOVER['rc']}) > {MOVER['log']} 2>&1 &")
    steps = {}
    try:
        try:
            for point in table["points"]:
                faults.wait_reached(lab.server, point, timeout=300)
                try:
                    steps[point] = _look(lab)
                finally:
                    faults.release(lab.server, point)
            lab.server.wait_for_file(MOVER["rc"])
            mover_status = int(lab.server.succeed(f"cat {MOVER['rc']}"))

            consistent_after_s, differences = _wait_for_same_view(lab, before, bound_s)
            refresh = None
            if consistent_after_s is None and table["refresh_procedure"]:
                for command in table["refresh_procedure"]:
                    lab.client.succeed(command)
                after_refresh = compare(
                    before, lab.share.manifest(DIRECTORY), ignore=VIEW_FIELDS_IGNORED
                )
                refresh = {"commands": table["refresh_procedure"], "verified": not after_refresh}
            attributes = compare(
                before, lab.share.manifest(DIRECTORY), ignore=("size", "mtime", "mode", "sha256")
            )
        finally:
            lab.server.succeed(f"rm -rf {faults.HOOK_DIR}/armed")
    finally:
        lab.client.succeed(f"touch {READER['go']}")

    lab.client.wait_for_file(READER["out"])
    reader = json.loads(lab.client.succeed(f"cat {READER['out']}"))
    holders = lab.holders(MOVED.path)

    failures = mover_view_failures(
        steps=steps,
        expected=expected,
        reader=reader,
        mover_status=mover_status,
        holders=holders,
        consistent_after_s=consistent_after_s,
        bound_s=bound_s,
        refresh=refresh,
        attributes_kept=not attributes,
    )
    lab.required_settings["mover_view"] = {
        "consistent_after_s": consistent_after_s,
        "bound_s": bound_s,
        "refresh_procedure": refresh,
        "found_by": "S1.10",
    }
    lab.evidence(
        "S1.10-mover-view",
        {
            "points": steps,
            "expected": expected,
            "reader": reader,
            "mover_status": mover_status,
            "mover_log": lab.server.succeed(f"cat {MOVER['log']}")[-2000:],
            "holders_after": holders,
            "consistent_after_s": consistent_after_s,
            "differences_at_bound": describe(differences, limit=None),
            "refresh": refresh,
            "attribute_differences": describe(attributes, limit=None),
            "failures": failures,
        },
    )
    assert not failures, "; ".join(failures)
