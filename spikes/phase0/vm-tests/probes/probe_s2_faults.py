# The S2 fault-kind probe (task T063): `kill-sync-at-position` and `write-error` of testlib/faults
# against the real SnapRAID and snapraid-btrfs, with the archive-lock wrappers in PATH. It
# measures, it does not judge, and it never writes a record. Profile `small`, `server` only
# (vm-tests/probes/guest_s2.nix). Scaffolding for the Phase 0 spikes, not product code.
#
# plain:  a plain `snapraid sync --gui --bw-limit=20M` killed at sync:pos:K; the processes before
#         the kill, the exit status, `status`, then a sync to completion
# snap:   new data, `snapsync sync -- --gui --bw-limit=20M` killed the same way through the
#         wrapper chain; snapsync's exit, `snapsync complete`, the snapshot lists, a new sync
# write:  new data, a plain sync, and at sync:pos:K the disk PROBE_WRITE_ERROR (default
#         parity0) fails by write-error; the sync's exit, its log and the kernel log;
#         PROBE_DROP_CACHES=1 empties the page cache before that sync
# Result: probe-s2-faults.json in the working directory of the driver and PROBE lines.
import json
import os
import shlex
import time
from pathlib import Path

from testlib import faults

STARTED = time.monotonic()
ARCH = ["arch0", "arch1", "arch2"]
DATA = {s: f"/mnt/archive/{s}" for s in ARCH}
LOGS = "/root/logs"
K = int(os.environ.get("PROBE_K", "120"))
# K counts from the first position of each phase's new data: every phase adds 64 MiB per disk,
# 256 positions of 256 KiB, after the 256 positions of each earlier phase (run 1 used K as an
# absolute position, so the later syncs were stopped at their first logged line).
FIRST = {"plain": 0, "snap": 256, "write": 512}
WRITE_ERROR = os.environ.get("PROBE_WRITE_ERROR", "parity0")
# PROBE_DROP_CACHES=1 empties the page cache before the sync of phase `write`, so that the sync
# reads the data disks from the devices (run 3: with the cache warm, a data disk failed by
# write-error during a sync went unnoticed).
DROP_CACHES = os.environ.get("PROBE_DROP_CACHES") == "1"
THROTTLE = "--gui --bw-limit=20M"
RESULT: dict = {"k": K, "write_error_disk": WRITE_ERROR, "drop_caches": DROP_CACHES}


def attempt(command: str, timeout: int = 900) -> tuple[int, str]:
    return server.execute(f"{command} 2>&1", timeout=timeout)


def run(command: str, timeout: int = 900) -> str:
    return server.succeed(command, timeout=timeout)


def note(phase: str, key: str, value) -> None:
    RESULT.setdefault(phase, {})[key] = value
    print(f"PROBE {phase}.{key}: {json.dumps(value)[:800]}")


def measure(phase: str, key: str, command: str) -> int:
    rc, out = attempt(command)
    note(phase, key, {"rc": rc, "out": out.strip().splitlines()[-25:]})
    return rc


def in_background(name: str, command: str) -> None:
    """Start a command detached from the driver's shell; its exit status lands in <name>.rc."""
    script = f"{command} > {LOGS}/{name}.out 2>&1; echo $? > {LOGS}/{name}.rc"
    run(f"setsid -f bash -c {shlex.quote(script)} </dev/null >/dev/null 2>&1")


def finished(name: str) -> int:
    server.wait_until_succeeds(f"test -s {LOGS}/{name}.rc", timeout=600)
    return int(run(f"cat {LOGS}/{name}.rc").strip())


def add_data(tag: str, mib: int) -> None:
    for serial in ARCH:
        run(f"dd if=/dev/urandom of={DATA[serial]}/branch/{tag}.bin bs=1M count={mib} status=none")
    run("sync")


def processes() -> list[str]:
    return run("ps -eo pid,args | grep -E 'snap(raid|sync)' | grep -v grep || true").splitlines()


# --- setup -----------------------------------------------------------------------------------

server.wait_for_unit("multi-user.target")
run("spike-mkfs all")
for mount in [*DATA.values(), *[f"/mnt/archive-content/{s}" for s in ARCH], "/mnt/parity"]:
    run(f"findmnt -n {mount} >/dev/null || mount {mount}")
run(f"spike-snapshots-init arch0 arch1 arch2 && mkdir -p {LOGS}")
add_data("a", 64)

# --- plain -----------------------------------------------------------------------------------

in_background("plain", f"snapraid -l {LOGS}/plain.log {THROTTLE} sync")
faults.wait_sync_position(server, f"{LOGS}/plain.log", FIRST["plain"] + K // 2)
note("plain", "processes_before_kill", processes())
event = faults.kill_sync_at_position(server, f"{LOGS}/plain.log", FIRST["plain"] + K)
note("plain", "fault", [event.kind, event.point, event.detail])
note("plain", "exit", finished("plain"))
note("plain", "processes_after_kill", processes())
measure("plain", "covered_after_kill", "snapsync covered --plain")
measure("plain", "sync_after", f"snapraid -l {LOGS}/plain2.log sync | tail -n 3")
measure("plain", "diff_after", "snapsync covered --plain")

# --- snap ------------------------------------------------------------------------------------

measure("snap", "first_snapshot_sync", "snapsync sync")
add_data("b", 64)
in_background("snap", f"snapsync sync --log {LOGS}/snap.log -- {THROTTLE}")
faults.wait_sync_position(server, f"{LOGS}/snap.log", FIRST["snap"] + K // 2)
note("snap", "processes_before_kill", processes())
event = faults.kill_sync_at_position(server, f"{LOGS}/snap.log", FIRST["snap"] + K)
note("snap", "fault", [event.kind, event.point, event.detail])
note("snap", "exit", finished("snap"))
measure("snap", "snapsync_output", f"tail -n 6 {LOGS}/snap.out")
note("snap", "processes_after_kill", processes())
measure("snap", "complete_after_kill", "snapsync complete")
measure("snap", "sync_after", "snapsync sync | tail -n 4")
measure("snap", "complete_after", "snapsync complete")
measure("snap", "covered_after", "snapsync covered")

# --- write -----------------------------------------------------------------------------------

add_data("c", 64)
if DROP_CACHES:
    run("sync && echo 3 > /proc/sys/vm/drop_caches")
in_background("write", f"snapraid -l {LOGS}/write.log {THROTTLE} sync")
note(
    "write", "position", faults.wait_sync_position(server, f"{LOGS}/write.log", FIRST["write"] + K)
)
event = faults.write_error(server, WRITE_ERROR, point=f"sync:pos:{FIRST['write'] + K}")
note("write", "fault", [event.kind, event.point, event.detail])
note("write", "exit", finished("write"))
measure("write", "sync_output", f"tail -n 12 {LOGS}/write.out")
measure(
    "write",
    "log_errors",
    f"grep -E '^(error|msg:error|msg:fatal|summary)' {LOGS}/write.log | head -20",
)
measure(
    "write", "dmesg", "dmesg | grep -i -E 'I/O error|EXT4-fs error|BTRFS (error|info)' | tail -n 8"
)
note("write", "processes_after", processes())

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-s2-faults.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
