# The S2 boot-after-failure probe (tasks T066 review, T066a): what a boot between the failure,
# the replacement and the recovery does to the archive lock, the state of guest/recover.sh, the
# mounts and the snapshot workflow, on the S2 node of vm-tests/s2/default.nix, whose lock is
# set again at boot by archive-lock-restore (nix/archive-lock.nix). It measures, it does not
# judge, and it never writes a record. Scaffolding for the Phase 0 spikes, not product code.
#
# Each phase: base set, snapshot sync, then
# after-replace:     arch0 fails (device-ioerror); detect, lock, replace by spare0; graceful
#                    shutdown, the failed disk's image replaced by a blank one (taken out); boot;
#                    the state; recover, verify, resync, unlock; every file compared
# power-off-failure: graceful shutdown, arch0's image replaced by a blank one (it failed while
#                    the machine was off, no lock was set); boot; the state; the whole sequence
# crash-after-replace: as after-replace with QEMU process termination instead of the shutdown
# failed-disk-back:  as after-replace, but the failed disk's image is kept, so the old disk
#                    comes back beside the replacement with the same label; boot; the state;
#                    recover; then (failed-disk-taken-out) shutdown, the failed disk's image
#                    replaced by a blank one; boot; the state; recover, verify, resync, unlock
# Result: probe-s2-boot.json in the working directory of the driver and PROBE lines.
import json
import time
from pathlib import Path

from s2.history import ARCH, History

from testlib import faults

STARTED = time.monotonic()
KIB = 1024
DISKS = {d["serial"]: d for d in TOPOLOGY["disks"]}
SPARE = faults.by_id("spare0")
SIZE = DISKS["arch0"]["sizeMiB"] << 20
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
    """The lock, the recovery state, the mounts and their modes, the pools, the restore unit."""
    note(phase, f"{key}_lock", attempt("archive-lock status"))
    note(phase, f"{key}_recover_state", attempt("find /var/lib/recover 2>&1 | sort"))
    mounts = "findmnt -n -o TARGET,SOURCE,OPTIONS | grep -E '/mnt/|/var/lib/snapraid' | cut -c1-150"
    note(phase, f"{key}_mounts", attempt(mounts))
    note(phase, f"{key}_pools", attempt("systemctl is-active mnt-base.mount mnt-cache.mount"))
    note(
        phase,
        f"{key}_arch0_fs",
        attempt("findmnt -n -o SOURCE,UUID --mountpoint /mnt/archive/arch0"),
    )
    note(
        phase,
        f"{key}_restore_unit",
        attempt(
            "systemctl show -p ActiveState,Result,ExecMainStatus archive-lock-restore; "
            "journalctl -b -u archive-lock-restore -o cat | tail -n 8"
        ),
    )


def boot(phase: str, failed: str | None, crash: bool = False) -> None:
    """A shutdown (or a crash); with `failed` its image is replaced by a blank one; a boot."""
    if crash:
        server.crash()
    else:
        server.shutdown()
    if failed:
        faults.device_failed_at_boot(server, DISKS[failed], QEMU_IMG)
    started = time.monotonic()
    server.start()
    server.wait_for_unit("multi-user.target")
    server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=180)
    note(phase, "boot_seconds", round(time.monotonic() - started, 1))
    note(phase, "failed_units", attempt("systemctl --failed --no-legend --plain"))
    note(phase, "labels", attempt("ls -l /dev/disk/by-label | cut -c40-"))
    note(
        phase, "order", attempt("systemctl show -p After,Before archive-lock-restore | cut -c1-600")
    )


def setup(phase: str) -> None:
    lab.reset()
    lab.populate(BASE)
    rc, out, _ = lab.sync(snapshots=True)
    note(phase, "sync", {"rc": rc, "out": out.strip().splitlines()[-2:]})


def steps(phase: str, names: tuple[str, ...]) -> None:
    commands = {
        "detect": "detect arch0",
        "lock": "lock arch0",
        "replace": f"replace arch0 {SPARE} {SIZE}",
        "recover": "recover arch0",
        "verify": "verify arch0",
        "resync": "resync arch0",
        "unlock": "unlock arch0",
    }
    for name in names:
        note(phase, name, attempt(f"recover {commands[name]}"))


def finish(phase: str) -> None:
    note(phase, "compare", {d: lab.compare(d) for d in ARCH})
    note(phase, "lock_at_end", attempt("archive-lock status"))


BEFORE = ("detect", "lock", "replace")
AFTER = ("recover", "verify", "resync", "unlock")

for phase, crash in (("after-replace", False), ("crash-after-replace", True)):
    setup(phase)
    note(phase, "fault", str(lab.fail("arch0")))
    steps(phase, BEFORE)
    state(phase, "before_boot")
    boot(phase, "arch0", crash=crash)
    state(phase, "after_boot")
    steps(phase, AFTER)
    finish(phase)

phase = "power-off-failure"
setup(phase)
boot(phase, "arch0")
state(phase, "after_boot")
steps(phase, BEFORE + AFTER)
finish(phase)

phase = "failed-disk-back"
setup(phase)
note(phase, "fault", str(lab.fail("arch0")))
steps(phase, BEFORE)
boot(phase, None)
state(phase, "after_boot")
steps(phase, ("recover",))
phase = "failed-disk-taken-out"
boot(phase, "arch0")
state(phase, "after_boot")
steps(phase, AFTER)
finish(phase)

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-s2-boot.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True, default=str))
