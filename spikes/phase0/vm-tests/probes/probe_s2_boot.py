# The S2 boot-after-failure probe: what a boot between the replacement and the recovery does to
# the archive lock, the state of guest/recover.sh, the mounts and the snapshot workflow, on the
# S2 node of vm-tests/s2/default.nix. It measures, it does not judge, and it never writes a
# record. Scaffolding for the Phase 0 spikes, not product code.
#
# after-replace: base set, snapshot sync; arch0 fails (device-ioerror); detect, lock, replace by
#                spare0; graceful shutdown; the failed disk's image replaced by a blank one (it
#                was taken out); boot; then the lock, /run/recover, the mounts, the pools,
#                snapper, the content files, `recover recover`; then the lock set again
#                (archive-lock) and a second `recover replace`, the marker of step 3 set by
#                hand, and steps 4 to 7; every file compared
# before-lock:   base set, snapshot sync; arch0 fails; graceful shutdown with the failed image
#                replaced by a blank one; boot; detect, lock, replace by spare0, recover,
#                verify, resync, unlock; every file compared
# Result: probe-s2-boot.json in the working directory of the driver and PROBE lines.
import json
import time
from pathlib import Path

from s2.history import ARCH, History

from testlib import faults

STARTED = time.monotonic()
KIB = 1024
DISKS = {d["serial"]: d for d in TOPOLOGY["disks"]}
RESULT: dict = {}
lab = History(server, TOPOLOGY, QEMU_IMG)
BASE = [
    ("arch0", "a/a0.bin", 512 * KIB),
    ("arch0", "a/a1.bin", 768 * KIB - 100),
    ("arch0", "a/a2.bin", 256 * KIB),
    ("arch1", "b/b0.bin", 256 * KIB),
    ("arch1", "b/b1.bin", 512 * KIB),
    ("arch2", "c/c0.bin", 1024 * KIB),
    ("arch2", "c/c1.bin", 256 * KIB),
]


def note(phase: str, key: str, value) -> None:
    RESULT.setdefault(phase, {})[key] = value
    print(f"PROBE {phase}.{key}: {json.dumps(value, default=str)[:900]}")


def attempt(command: str, lines: int = 14) -> dict:
    rc, out = server.execute(f"{command} 2>&1", timeout=900)
    return {"rc": rc, "out": out.strip().splitlines()[-lines:]}


def state(phase: str, key: str) -> None:
    """The lock, the recover state, the mounts and their modes, the pools."""
    note(phase, f"{key}_lock", attempt("archive-lock status"))
    note(phase, f"{key}_recover_state", attempt("find /run/recover /run/archive-lock 2>&1 | sort"))
    note(
        phase,
        f"{key}_mounts",
        attempt(
            "findmnt -n -o TARGET,SOURCE,OPTIONS | grep -E '/mnt/|/var/lib/snapraid' | cut -c1-150"
        ),
    )
    note(phase, f"{key}_pools", attempt("systemctl is-active mnt-base.mount mnt-cache.mount"))


def reboot(phase: str, failed: str) -> None:
    """Graceful shutdown, the failed disk taken out (its image replaced by a blank one), boot."""
    started = time.monotonic()
    server.shutdown()
    note(phase, "shutdown_seconds", round(time.monotonic() - started, 1))
    faults.device_failed_at_boot(server, DISKS[failed], QEMU_IMG)
    started = time.monotonic()
    server.start()
    server.wait_for_unit("multi-user.target")
    server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=180)
    note(phase, "boot_seconds", round(time.monotonic() - started, 1))
    note(phase, "failed_units", attempt("systemctl --failed --no-legend --plain"))
    note(phase, "labels", attempt("ls /dev/disk/by-label"))


def compare_all(phase: str) -> None:
    note(phase, "compare", {d: lab.compare(d) for d in ARCH})


def setup(phase: str) -> None:
    lab.reset()
    lab.populate(BASE)
    rc, out, _ = lab.sync(snapshots=True)
    note(phase, "sync", {"rc": rc, "out": out.strip().splitlines()[-2:]})
    note(phase, "fault", str(lab.fail("arch0")))


# --- after-replace ---------------------------------------------------------------------------

setup("after-replace")
spare = faults.by_id("spare0")
size = DISKS["arch0"]["sizeMiB"] << 20
for step in ("detect arch0", "lock arch0", f"replace arch0 {spare} {size}"):
    note("after-replace", step.split()[0], attempt(f"recover {step}"))
state("after-replace", "before_boot")
reboot("after-replace", "arch0")
state("after-replace", "after_boot")
note(
    "after-replace", "snapper_arch0", attempt("LC_ALL=C snapper -c arch0 list --disable-used-space")
)
note("after-replace", "content_files", attempt("ls -l /var/lib/snapraid /mnt/parity"))
note("after-replace", "snapraid_status", attempt("snapraid status | tail -n 6"))
note("after-replace", "recover_after_boot", attempt("recover recover arch0"))
# What the state of steps 2 and 3 would give if it survived the boot: the lock set again
# directly (recover lock unmounts the disk first) and the marker of step 3.
note("after-replace", "archive_lock_after_boot", attempt("archive-lock lock 'arch0 failed'"))
note("after-replace", "replace_after_boot", attempt(f"recover replace arch0 {spare} {size}"))
note(
    "after-replace",
    "marker",
    attempt("mkdir -p /run/recover/arch0 && touch /run/recover/arch0/replaced"),
)
state("after-replace", "after_relock")
for step in ("recover arch0", "verify arch0", "resync arch0", "unlock arch0"):
    note("after-replace", step.split()[0] + "_with_state", attempt(f"recover {step}"))
compare_all("after-replace")

# --- before-lock -----------------------------------------------------------------------------

setup("before-lock")
reboot("before-lock", "arch0")
state("before-lock", "after_boot")
for step in ("detect arch0", "lock arch0", f"replace arch0 {spare} {size}", "recover arch0"):
    note("before-lock", step.split()[0], attempt(f"recover {step}"))
for step in ("verify arch0", "resync arch0", "unlock arch0"):
    note("before-lock", step.split()[0], attempt(f"recover {step}"))
compare_all("before-lock")

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-s2-boot.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True, default=str))
