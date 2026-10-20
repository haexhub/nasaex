# The recovery probe of S2 (task T062): guest/recover.sh step by step against the real kernel,
# SnapRAID, snapraid-btrfs and snapper. It measures, it does not judge, and it never writes a
# record. Profile `small`, `server` only (vm-tests/probes/guest_s2.nix). Scaffolding for the Phase
# 0 spikes, not product code.
#
# snap:    snapshot workflow. detect on a healthy disk; arch0 (640 MiB) fails (device-ioerror);
#          detect, lock; replace refused on the smaller spare2 (512 MiB) and on spare3 formatted
#          with ext4; spare3 wiped and used; recover, verify, resync, unlock; every file compared
# plain:   without the snapshot workflow. A plain sync, then an in-place rewrite on arch1 at the
#          positions of arch2 files; arch2 fails; the steps with --plain; recover lists the
#          unrecoverable files; verify and resync without and with --accept-unrecoverable
# unplug:  detect after device-unplug of arch1
# Result: probe-recover.json in the working directory of the driver and PROBE lines.
import json
import time
from pathlib import Path

from testlib import faults

STARTED = time.monotonic()
ARCH = ["arch0", "arch1", "arch2"]
DATA = {s: f"/mnt/archive/{s}" for s in ARCH}
MIB = 1 << 20
SIZES = {"arch0": 640 * MIB, "arch2": 512 * MIB}  # the disk inventory (nix/profiles.nix, small)
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


# --- setup -----------------------------------------------------------------------------------

server.wait_for_unit("multi-user.target")
run("spike-mkfs all")
for mount in [*DATA.values(), *[f"/mnt/archive-content/{s}" for s in ARCH], "/mnt/parity"]:
    run(f"findmnt -n {mount} >/dev/null || mount {mount}")
run("spike-snapshots-init arch0 arch1 arch2")
for serial, count in (("arch0", 3), ("arch1", 3), ("arch2", 4)):
    for i in range(count):
        target = f"{DATA[serial]}/branch/f{i}.bin"
        run(f"dd if=/dev/urandom of={target} bs=64K count={6 + 4 * i} status=none")
run("sync")

# --- snap ------------------------------------------------------------------------------------

measure("snap", "sync", "snapsync sync")
expected = hashes()
measure("snap", "detect_healthy", "recover detect arch0")
event = faults.device_ioerror(server, "arch0")
note("snap", "fault", [event.kind, event.detail])
measure("snap", "detect", "recover detect arch0")
measure("snap", "lock", "recover lock arch0")
note("snap", "status", run("archive-lock status").splitlines())
measure(
    "snap", "replace_smaller", f"recover replace arch0 {faults.by_id('spare2')} {SIZES['arch0']}"
)
measure("snap", "spare2_untouched", f"blkid -p {faults.by_id('spare2')}; echo blkid=$?")
run(f"mkfs.ext4 -q -F {faults.by_id('spare3')} && udevadm settle")
measure(
    "snap", "replace_formatted", f"recover replace arch0 {faults.by_id('spare3')} {SIZES['arch0']}"
)
measure("snap", "spare3_still_ext4", f"blkid -s TYPE -o value {faults.by_id('spare3')}")
run(f"wipefs -a -q {faults.by_id('spare3')} && udevadm settle")
measure("snap", "replace", f"recover replace arch0 {faults.by_id('spare3')} {SIZES['arch0']}")
measure("snap", "recover", "recover recover arch0")
measure("snap", "verify", "recover verify arch0")
measure("snap", "resync", "recover resync arch0")
note("snap", "status_after_resync", run("archive-lock status").splitlines())
measure("snap", "unlock", "recover unlock arch0")
note("snap", "compare", compare(expected, "arch0"))

# --- plain -----------------------------------------------------------------------------------

measure("plain", "sync", "snapraid -l /var/log/recover/plain-sync.log sync")
measure("plain", "locate", "snapraid --test-fmt disk locate")
expected = hashes()
# An in-place rewrite of an arch1 file after the sync: its positions block arch2 files there.
size = int(run(f"stat -c %s {DATA['arch1']}/branch/f0.bin"))
run(
    f"dd if=/dev/urandom of={DATA['arch1']}/branch/f0.bin bs={size} count=1 conv=notrunc "
    "status=none && sync"
)
expected.update(
    {k: v for k, v in hashes().items() if k.startswith(DATA["arch1"] + "/")}
)  # arch1 now holds the rewritten version
event = faults.device_ioerror(server, "arch2")
note("plain", "fault", [event.kind, event.detail])
measure("plain", "detect", "recover detect arch2")
measure("plain", "lock", "recover lock arch2")
measure("plain", "replace", f"recover replace arch2 {faults.by_id('spare2')} {SIZES['arch2']}")
measure("plain", "recover", "recover recover arch2 --plain")
measure("plain", "fix_status_tags", "grep -h '^status:' /var/log/recover/fix-arch2-*.log | sort")
measure("plain", "verify", "recover verify arch2")
measure(
    "plain",
    "check_tags",
    "grep -h -E '^(status|[a-z_]*error[a-z_]*):' /var/log/recover/check-arch2-*.log | head -40",
)
measure("plain", "resync_refused", "recover resync arch2 --plain")
measure("plain", "verify_accept", "recover verify arch2 --accept-unrecoverable")
measure("plain", "resync", "recover resync arch2 --plain --accept-unrecoverable")
measure("plain", "unlock", "recover unlock arch2")
note("plain", "compare", compare(expected, "arch2"))

# --- unplug ----------------------------------------------------------------------------------

faults.device_unplug(server, "arch1")
measure("unplug", "detect", "recover detect arch1")

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-recover.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
