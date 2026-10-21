# The corrupted-parity probe of S2 (task T064): does the recovery sequence of guest/recover.sh
# fail to restore the failed disk's files bit-exact after controls/corrupted-parity.sh, so that
# S2.1 flags the control as FAIL? It measures, it does not judge, and it never writes a record.
# Profile `small`, `server` only (vm-tests/probes/guest_s2.nix). Scaffolding for the Phase 0
# spikes, not product code. The same setup without the control restored every file (probe T062).
#
# snap:   snapshot workflow. `snapsync sync`, `snapraid check` before and after the control,
#         arch0 fails (device-ioerror); detect, lock, replace by spare3, recover, verify; every
#         file compared; the loss accepted, the resync step (it refuses when every file of
#         the disk was lost, run 1), unlock
# plain:  `snapraid -E --force-full sync` rebuilds every parity block (-E: every file of arch0
#         was lost, so the sync refuses without it), `snapraid check`; the control
#         while the archive is locked (refused, parity unchanged), then unlocked; arch2 fails;
#         the steps with --plain; every file compared
# Result: probe-corrupted-parity.json in the working directory of the driver and PROBE lines.
import json
import time
from pathlib import Path

from testlib import faults

STARTED = time.monotonic()
ARCH = ["arch0", "arch1", "arch2"]
DATA = {s: f"/mnt/archive/{s}" for s in ARCH}
PARITY = "/mnt/parity/snapraid.parity"
MIB = 1 << 20
SIZES = {"arch0": 640 * MIB, "arch2": 512 * MIB}  # the disk inventory (nix/profiles.nix, small)
# The kinds of error tags in a SnapRAID log, with their counts.
TAGS = "grep -o -E '^[a-z_]*error[a-z_]*:' {log} | sort | uniq -c"
RESULT: dict = {}


def attempt(command: str, timeout: int = 900) -> tuple[int, str]:
    return server.execute(f"{command} 2>&1", timeout=timeout)


def run(command: str, timeout: int = 900) -> str:
    return server.succeed(command, timeout=timeout)


def note(phase: str, key: str, value) -> None:
    RESULT.setdefault(phase, {})[key] = value
    print(f"PROBE {phase}.{key}: {json.dumps(value)[:800]}")


def measure(phase: str, key: str, command: str) -> int:
    rc, out = attempt(command)
    note(phase, key, {"rc": rc, "out": out.strip().splitlines()[-30:]})
    return rc


def tool(command: str, lines: int = 6) -> str:
    """The last lines of a command's output, stderr included; the driver runs every command with
    `set -euo pipefail`, so the exit status is the command's own."""
    return f"{command} 2>&1 | tail -n {lines}"


def hashes() -> dict[str, str]:
    out = run("find /mnt/archive/*/branch -type f | sort | xargs -r sha256sum")
    return {line.split(None, 1)[1].strip(): line.split()[0] for line in out.splitlines()}


def compare(expected: dict[str, str], disk: str) -> dict:
    now = hashes()
    mine = {p: s for p, s in expected.items() if p.startswith(DATA[disk] + "/")}
    return {
        "bit_exact": sorted(p for p in mine if now.get(p) == mine[p]),
        "differs_or_missing": sorted(p for p in mine if now.get(p) != mine[p]),
        "others_unchanged": all(
            now.get(p) == s for p, s in expected.items() if not p.startswith(DATA[disk] + "/")
        ),
        "unrecoverable_names": sorted(p for p in now if p.endswith(".unrecoverable")),
    }


def parity_sha() -> str:
    return run(f"sha256sum {PARITY}").split()[0]


# --- setup -----------------------------------------------------------------------------------

server.wait_for_unit("multi-user.target")
run("spike-mkfs all")
for mount in [*DATA.values(), *[f"/mnt/archive-content/{s}" for s in ARCH], "/mnt/parity"]:
    run(f"findmnt -n {mount} >/dev/null || mount {mount}")
run("spike-snapshots-init arch0 arch1 arch2 && mkdir -p /root/logs")
for serial, count in (("arch0", 3), ("arch1", 3), ("arch2", 4)):
    for i in range(count):
        target = f"{DATA[serial]}/branch/f{i}.bin"
        run(f"dd if=/dev/urandom of={target} bs=64K count={6 + 4 * i} status=none")
run("sync")

# --- snap ------------------------------------------------------------------------------------

measure("snap", "sync", "snapsync sync")
expected = hashes()
measure("snap", "check_before", tool("snapraid check"))
note("snap", "parity_before", parity_sha())
measure("snap", "control", "corrupted-parity")
note("snap", "parity_after", parity_sha())
measure("snap", "check_after", tool("snapraid -l /root/logs/snap-check.log check"))
measure("snap", "check_tags", TAGS.format(log="/root/logs/snap-check.log"))
event = faults.device_ioerror(server, "arch0")
note("snap", "fault", [event.kind, event.detail])
measure("snap", "detect", "recover detect arch0")
measure("snap", "lock", "recover lock arch0")
measure("snap", "replace", f"recover replace arch0 {faults.by_id('spare3')} {SIZES['arch0']}")
measure("snap", "recover", "recover recover arch0")
measure("snap", "fix_status_tags", "grep -h '^status:' /var/log/recover/fix-arch0-*.log | sort")
measure("snap", "verify", "recover verify arch0")
note("snap", "compare", compare(expected, "arch0"))
measure("snap", "verify_accept", "recover verify arch0 --accept-unrecoverable")
measure("snap", "resync", "recover resync arch0 --accept-unrecoverable")
measure("snap", "unlock", "recover unlock arch0")

# --- plain -----------------------------------------------------------------------------------

measure("plain", "sync", tool("snapraid -E --force-full sync"))
expected = hashes()
measure("plain", "check_before", tool("snapraid check"))
note("plain", "parity_before", parity_sha())
run("archive-lock lock probe")
measure("plain", "control_locked", "corrupted-parity")
note("plain", "parity_locked", parity_sha())
run("archive-lock unlock")
measure("plain", "control", "corrupted-parity")
note("plain", "parity_after", parity_sha())
event = faults.device_ioerror(server, "arch2")
note("plain", "fault", [event.kind, event.detail])
measure("plain", "detect", "recover detect arch2")
measure("plain", "lock", "recover lock arch2")
measure("plain", "replace", f"recover replace arch2 {faults.by_id('spare2')} {SIZES['arch2']}")
measure("plain", "recover", "recover recover arch2 --plain")
measure("plain", "fix_status_tags", "grep -h '^status:' /var/log/recover/fix-arch2-*.log | sort")
measure("plain", "verify", "recover verify arch2")
note("plain", "compare", compare(expected, "arch2"))

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-corrupted-parity.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
