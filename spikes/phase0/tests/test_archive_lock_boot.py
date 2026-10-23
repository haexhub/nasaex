"""The archive lock of S2 across a boot (guest/archive-lock.sh, task T066a) on the host, with
the stand-ins of tests/guest_stubs.py. A boot is the mount table of the fstab again (every
location mounted read-write, no bind) with the state directory kept, as on the system disk.
probe-s2-boot measured why: the state in /run was gone after a boot, every location was
read-write, and a lock set again made the replacement read-only too. What the kernel and
systemd do at a real boot is measured in the VM (probe-s2-boot), not here."""

import json
import subprocess
from pathlib import Path

import guest_stubs as stubs
import pytest

LOCK = Path(__file__).resolve().parents[1] / "guest" / "archive-lock.sh"
DATA = ["/mnt/archive/arch0", "/mnt/archive/arch1", "/mnt/archive/arch2"]
FSTAB = {"/": "rw", "/mnt/parity": "rw", **dict.fromkeys(DATA, "rw")}


@pytest.fixture
def box(tmp_path):
    system = tmp_path / "var-lib-snapraid"
    system.mkdir()
    conf = tmp_path / "snapraid.conf"
    conf.write_text(
        "parity /mnt/parity/snapraid.parity\n"
        f"content {system}/snapraid.content\n"
        "content /mnt/parity/snapraid.content\n"
        + "".join(f"data arch{i} {d}/\n" for i, d in enumerate(DATA))
        + "exclude /.snapshots/\n"
    )
    directory = stubs.install(tmp_path / "stubs", mounts=dict(FSTAB))
    env = stubs.environment(
        directory,
        ARCHIVE_LOCK_DIR=str(tmp_path / "var-lib-archive-lock"),
        SNAPRAID_CONF=str(conf),
        ARCHIVE_LOCK_SNAPRAID=str(directory / "snapraid"),
        ARCHIVE_LOCK_SNAPRAID_BTRFS=str(directory / "snapraid-btrfs"),
    )
    return {"stubs": directory, "env": env, "system": system, "tmp": tmp_path}


def run(box, *args):
    return subprocess.run(
        ["bash", str(LOCK), *args], env=box["env"], timeout=30, capture_output=True, text=True
    )


def table(box, mounts):
    (box["stubs"] / "mounts.json").write_text(json.dumps(mounts))


def boot(box, missing=()):
    """The fstab mounts again (missing: data paths whose disk is gone); the state is kept."""
    table(box, {t: m for t, m in FSTAB.items() if t not in missing})


def modes(box):
    mounts = stubs.mounts(box["stubs"])
    return {t: mounts.get(t) for t in [*DATA, "/mnt/parity", str(box["system"])]}


def synced(box):
    (box["system"] / "snapraid.content").write_text("content of a sync\n")


def another_filesystem(box, target):
    """The failed disk back with the same label: mounted at `target` instead of the replacement."""
    path = box["stubs"] / "state.json"
    state = json.loads(path.read_text()) if path.exists() else {"sources": {}, "labels": {}}
    state.setdefault("uuids", {})[target] = "uuid:the-failed-disk"
    path.write_text(json.dumps(state))


def test_the_state_lives_on_the_system_disk_by_default():
    out = subprocess.run(
        ["bash", "-c", f"unset ARCHIVE_LOCK_DIR; source {LOCK}; _al_dir"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert out.stdout == "/var/lib/archive-lock\n"


def test_restore_after_a_boot_locks_every_location_again(box):
    run(box, "lock", "arch2 failed")
    boot(box)
    assert modes(box)["/mnt/parity"] == "rw"  # what the boot left

    result = run(box, "restore")

    assert result.returncode == 0, result.stderr
    assert set(modes(box).values()) == {"ro"}
    assert run(box, "status").stdout.splitlines()[:2] == ["locked", "reason: arch2 failed"]
    assert run(box, "snapraid", "sync").returncode == 30


def test_restore_ends_the_resync_stage(box):
    run(box, "lock", "arch2 failed")
    run(box, "open-for-resync")
    boot(box)

    run(box, "restore")

    assert "stage: resync" not in run(box, "status").stdout
    assert set(modes(box).values()) == {"ro"}
    assert run(box, "snapraid", "sync").returncode == 30


def test_the_exempt_replacement_stays_writable_through_lock_and_restore(box):
    boot(box, missing=["/mnt/archive/arch2"])
    run(box, "lock", "arch2 failed")
    table(box, {**stubs.mounts(box["stubs"]), "/mnt/archive/arch2": "rw"})  # the replacement
    assert run(box, "exempt", "arch2").returncode == 0

    run(box, "lock", "arch2 failed")  # a second lock keeps it writable
    assert stubs.mounts(box["stubs"])["/mnt/archive/arch2"] == "rw"
    boot(box)
    run(box, "restore")

    assert modes(box) == {
        **dict.fromkeys(["/mnt/archive/arch0", "/mnt/archive/arch1", "/mnt/parity"], "ro"),
        "/mnt/archive/arch2": "rw",
        str(box["system"]): "ro",
    }
    assert "remount /mnt/archive/arch2" not in run(box, "status").stdout
    assert "exempt: arch2 uuid:/mnt/archive/arch2" in run(box, "status").stdout.splitlines()


def test_another_filesystem_at_the_exempt_path_is_locked(box):
    # probe-s2-boot, failed-disk-back: with the failed disk back beside the replacement, the boot
    # mounted the failed one (same label) at the data path, and it was left writable.
    run(box, "lock", "arch2 failed")
    run(box, "exempt", "arch2")
    another_filesystem(box, "/mnt/archive/arch2")
    boot(box)

    result = run(box, "restore")

    assert result.returncode == 0, result.stderr
    assert "/mnt/archive/arch2/: not the filesystem exempt as the replacement of arch2, locked" in (
        result.stdout.splitlines()
    )
    assert set(modes(box).values()) == {"ro"}


def test_exempt_needs_the_replacement_mounted(box):
    run(box, "lock", "arch2 failed")
    boot(box, missing=["/mnt/archive/arch2"])

    result = run(box, "exempt", "arch2")

    assert result.returncode == 1
    assert "nothing is mounted at /mnt/archive/arch2/" in result.stderr


def test_exempt_is_synced_to_the_disk(box):
    # A power loss right after the replacement must not make it read-only at the next boot.
    run(box, "lock", "arch2 failed")
    state = str(box["tmp"] / "var-lib-archive-lock")

    run(box, "exempt", "arch2")

    assert stubs.calls(box["stubs"], "sync")[-1] == ["sync", f"{state}/exempt", state]


def test_unlock_forgets_the_exempt_disks(box):
    run(box, "lock", "arch2 failed")
    run(box, "exempt", "arch2")
    run(box, "unlock")

    run(box, "lock", "arch0 failed")

    assert stubs.mounts(box["stubs"])["/mnt/archive/arch2"] == "ro"


@pytest.mark.parametrize(
    ("locked", "disk", "error"),
    [(False, "arch2", "not locked"), (True, "arch9", "no data disk arch9")],
)
def test_exempt_needs_the_lock_and_a_data_disk_of_the_config(box, locked, disk, error):
    if locked:
        run(box, "lock", "arch2 failed")

    result = run(box, "exempt", disk)

    assert result.returncode == 1
    assert error in result.stderr


def test_restore_without_a_lock_and_with_every_disk_mounted_changes_nothing(box):
    synced(box)

    result = run(box, "restore")

    assert result.returncode == 0, result.stderr
    assert run(box, "status").stdout == "unlocked\n"
    assert stubs.calls(box["stubs"], "mount") == []


def test_restore_locks_a_synced_array_whose_data_disk_is_missing_at_boot(box):
    synced(box)
    boot(box, missing=["/mnt/archive/arch1"])

    result = run(box, "restore")

    assert result.returncode == 0, result.stderr
    status = run(box, "status").stdout.splitlines()
    assert status[:2] == ["locked", "reason: boot: arch1 is not mounted"]
    assert modes(box)["/mnt/archive/arch0"] == "ro" and modes(box)["/mnt/parity"] == "ro"
    assert run(box, "snapraid", "sync").returncode == 30


def test_restore_locks_when_the_parity_disk_is_missing_too(box):
    # probe-s2-boot: at a boot with every disk blank, the parity file did not exist, findmnt
    # exited 1 without a word, and the restore failed silently.
    synced(box)
    boot(box, missing=[*DATA, "/mnt/parity"])
    stubs.configure(box["stubs"], absent=["/mnt/parity/snapraid.parity"])

    result = run(box, "restore")

    assert result.returncode == 0, result.stderr
    assert "/mnt/parity/snapraid.parity: not mounted, skipped" in result.stdout.splitlines()
    status = run(box, "status").stdout.splitlines()
    assert status[1] == "reason: boot: arch0 arch1 arch2 are not mounted"
    assert run(box, "snapraid", "sync").returncode == 30


def test_restore_leaves_an_array_that_was_never_synced_unlocked(box):
    boot(box, missing=["/mnt/archive/arch1"])  # blank disks before the first sync

    result = run(box, "restore")

    assert result.returncode == 0, result.stderr
    assert run(box, "status").stdout == "unlocked\n"


def test_a_restore_that_cannot_lock_everything_exits_1_and_keeps_the_refusal(box):
    run(box, "lock", "arch2 failed")
    boot(box)
    stubs.configure(box["stubs"], busy=["/mnt/archive/arch1"])

    result = run(box, "restore")

    assert result.returncode == 1
    assert "/mnt/archive/arch1" in result.stderr
    assert run(box, "snapraid", "sync").returncode == 30
