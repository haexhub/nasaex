# The S2 history probe (task T065): vm-tests/s2/history.py on the S2 node of
# vm-tests/s2/default.nix against the real kernel, mergerfs, the mover primitives, SnapRAID 14.4,
# snapper and snapraid-btrfs. It measures, it does not judge, and it never writes a record.
# Scaffolding for the Phase 0 spikes, not product code.
#
# first:   reset (first boot); populate the three disks; plain sync; the model's layout against
#          `snapraid locate`; compare every disk
# history: two files added the mover's way (not on arch2); a synced arch2 file changed in place
#          and a synced arch1 file deleted through the share; a file written to arch0; snapshot
#          sync; layout against locate; then changes after the sync; arch2 fails
#          (device-ioerror), replace (steps 1 to 3), `recover recover arch2`, compare arch2
# again:   reset after the failure: blank array, every device readable, spares blank; arch0
#          fails by device-unplug, replaced by the larger spare
# last:    reset after the unplug: arch0 is back
# Result: probe-s2-history.json in the working directory of the driver and PROBE lines.
import json
import time
from pathlib import Path

from s2.history import ARCH, DATA, History

from testlib.parity_model import after_sync

STARTED = time.monotonic()
KIB = 1024
RESULT: dict = {}
lab = History(server, TOPOLOGY, QEMU_IMG)


def note(phase: str, key: str, value) -> None:
    RESULT.setdefault(phase, {})[key] = value
    print(f"PROBE {phase}.{key}: {json.dumps(value, default=str)[:800]}")


def attempt(command: str) -> dict:
    rc, out = server.execute(f"{command} 2>&1", timeout=900)
    return {"rc": rc, "out": out.strip().splitlines()[-12:]}


def reset(phase: str) -> None:
    started = time.monotonic()
    lab.reset()
    note(phase, "reset_seconds", round(time.monotonic() - started, 1))


def blank_check(phase: str) -> None:
    """Every archive and spare device readable, the archive branches empty, the spares blank."""
    devices = {d["serial"]: d for d in TOPOLOGY["disks"]}
    note(phase, "branches", attempt("find /mnt/archive/*/branch -mindepth 1 | wc -l"))
    for serial in [*ARCH, *(s for s, d in devices.items() if d["role"] == "replacement")]:
        path = f"/dev/disk/by-id/virtio-{serial}"
        read = attempt(f"dd if={path} of=/dev/null bs=1M count=1 iflag=direct status=none")
        note(phase, f"read_{serial}", read["rc"])
    for serial in (s for s, d in devices.items() if d["role"] == "replacement"):
        note(phase, f"blkid_{serial}", attempt(f"blkid -p /dev/disk/by-id/virtio-{serial}")["rc"])


def sync(phase: str, snapshots: bool) -> None:
    rc, out, log = lab.sync(snapshots)
    note(phase, "sync", {"rc": rc, "out": out.strip().splitlines()[-3:], "log": log})
    note(phase, "layout_differences", lab.layout_differences())
    note(phase, "synced_layout", lab.synced)


# --- first -----------------------------------------------------------------------------------

reset("first")
lab.populate(
    [
        ("arch0", "a/one.bin", 600 * KIB),
        ("arch0", "a/two.bin", 300 * KIB),
        ("arch1", "b/one.bin", 900 * KIB),
        ("arch1", "b/two.bin", 256 * KIB),
        ("arch2", "c/one.bin", 512 * KIB),
        ("arch2", "c/two.bin", 700 * KIB),
        ("arch2", "c/empty.bin", 0),
    ]
)
sync("first", snapshots=False)
note("first", "compare", {d: lab.compare(d) for d in ARCH})

# --- history ---------------------------------------------------------------------------------

note("history", "add_1", lab.add("new/one.bin", 400 * KIB, not_on="arch2"))
note("history", "add_2", lab.add("new/two.bin", 200 * KIB, not_on="arch2"))
lab.modify("c/two.bin", [10, 300 * KIB])
lab.delete("b/two.bin")
lab.populate([("arch0", "a/three.bin", 128 * KIB)])
note("history", "changed_before_sync", lab.changed)
note("history", "unsynced_before_sync", lab.unsynced)
note("history", "share_view", attempt("find /mnt/cache -type f | sort"))
sync("history", snapshots=True)
note("history", "complete", attempt("snapsync complete"))
lab.modify("c/one.bin", [1])
lab.delete("a/two.bin")
note("history", "changed_after_sync", lab.changed)
note("history", "unsynced_after_sync", lab.unsynced)
prediction = after_sync(lab.synced, ["arch2"], lab.changed, lab.unsynced, snapshot=True)
note(
    "history", "prediction", [(f.path, f.version, f.recoverable, f.rule) for f in prediction.files]
)
note("history", "fault", str(lab.fail("arch2")))
note("history", "replace", lab.replace("arch2"))
note("history", "recover", attempt("recover recover arch2"))
note("history", "compare_arch2", lab.compare("arch2"))
note(
    "history",
    "unrecoverable_names",
    attempt(f"find {DATA['arch2']}/branch -name '*.unrecoverable'"),
)

# --- again -----------------------------------------------------------------------------------

reset("again")
blank_check("again")
lab.populate([("arch0", "x.bin", 300 * KIB), ("arch1", "y.bin", 300 * KIB)])
sync("again", snapshots=False)
note("again", "fault", str(lab.fail("arch0", "device-unplug")))
note("again", "replace", lab.replace("arch0", "larger"))
note("again", "mounted_from", attempt(f"findmnt -n -o SOURCE {DATA['arch0']}"))

# --- last ------------------------------------------------------------------------------------

reset("last")
blank_check("last")

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-s2-history.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True, default=str))
