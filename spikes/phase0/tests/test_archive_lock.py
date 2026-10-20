"""The archive lock of S2 (guest/archive-lock.sh, task T061) on the host, with stand-ins for
mount, findmnt and the SnapRAID tools (tests/guest_stubs.py). Whether the kernel really refuses
every write after the remount is measured in the VM, not here."""

import json
import subprocess
from pathlib import Path

import guest_stubs as stubs
import pytest

GUEST = Path(__file__).resolve().parents[1] / "guest"
LOCK = GUEST / "archive-lock.sh"
EFFECTS = GUEST / "mover-effects.sh"
DATA = ["/mnt/archive/arch0", "/mnt/archive/arch1", "/mnt/archive/arch2"]
REFUSED = "archive-lock: refused: {action}: the archive is locked (arch2 failed)\n"


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
    table = {"/": "rw", "/mnt/parity": "rw", **dict.fromkeys(DATA, "rw")}
    directory = stubs.install(tmp_path / "stubs", mounts=table)
    env = stubs.environment(
        directory,
        ARCHIVE_LOCK_DIR=str(tmp_path / "run"),
        SNAPRAID_CONF=str(conf),
        ARCHIVE_LOCK_SNAPRAID=str(directory / "snapraid"),
        ARCHIVE_LOCK_SNAPRAID_BTRFS=str(directory / "snapraid-btrfs"),
    )
    return {"stubs": directory, "env": env, "system": system, "tmp": tmp_path}


def run(box, *args, script=LOCK):
    return subprocess.run(
        ["bash", str(script), *args], env=box["env"], timeout=30, capture_output=True, text=True
    )


def test_lock_remounts_data_and_parity_read_only_and_binds_the_system_content_directory(box):
    result = run(box, "lock", "arch2 failed")

    assert result.returncode == 0, result.stderr
    table = stubs.mounts(box["stubs"])
    assert {t: table[t] for t in [*DATA, "/mnt/parity"]} == dict.fromkeys(
        [*DATA, "/mnt/parity"], "ro"
    )
    assert table[str(box["system"])] == "ro"  # a read-only bind of the directory itself
    assert table["/"] == "rw"  # the system disk stays writable


def test_the_content_copy_on_a_locked_parity_disk_needs_no_bind_of_its_own(box):
    run(box, "lock", "arch2 failed")

    binds = [c for c in stubs.calls(box["stubs"], "mount") if c[1] == "--bind"]
    assert binds == [["mount", "--bind", str(box["system"]), str(box["system"])]]


def test_lock_creates_the_snapraid_lock_file_before_the_location_turns_read_only(box):
    # SnapRAID opens <first content file>.lock with O_CREAT before every command, also fix and
    # status; an existing file opens on a read-only mount, a missing one cannot be created.
    run(box, "lock", "arch2 failed")

    assert (box["system"] / "snapraid.content.lock").exists()


def test_lock_leaves_an_existing_snapraid_lock_file_alone(box):
    # After a partly failed lock its location is read-only already: touching the file fails.
    (box["system"] / "snapraid.content.lock").touch()

    run(box, "lock", "arch2 failed")

    assert stubs.calls(box["stubs"], "touch") == []


def test_a_busy_filesystem_fails_the_lock_by_name_and_the_refusal_stays(box):
    stubs.configure(box["stubs"], busy=["/mnt/archive/arch1"])

    result = run(box, "lock", "arch2 failed")

    assert result.returncode == 1
    assert "/mnt/archive/arch1" in result.stderr
    assert stubs.mounts(box["stubs"])["/mnt/archive/arch0"] == "ro"  # the others are locked
    assert run(box, "snapraid", "sync").returncode == 30


def test_a_data_disk_that_is_not_mounted_is_reported_and_skipped(box):
    table = stubs.mounts(box["stubs"])
    del table["/mnt/archive/arch2"]
    (box["stubs"] / "mounts.json").write_text(json.dumps(table))

    result = run(box, "lock", "arch2 failed")

    assert result.returncode == 0, result.stderr
    assert "/mnt/archive/arch2/: not mounted, skipped" in result.stdout
    assert "/mnt/archive/arch2" not in stubs.mounts(box["stubs"])  # no mount appears


def test_status_names_the_reason_and_every_locked_location(box):
    assert run(box, "status").stdout == "unlocked\n"
    run(box, "lock", "arch2 failed")

    lines = run(box, "status").stdout.splitlines()

    assert lines[:2] == ["locked", "reason: arch2 failed"]
    assert "remount /mnt/archive/arch0 ro" in lines
    assert "remount /mnt/parity ro" in lines
    assert f"bind {box['system']} ro" in lines


def test_unlock_restores_read_write_removes_the_bind_and_lifts_the_refusal(box):
    run(box, "lock", "arch2 failed")

    result = run(box, "unlock")

    assert result.returncode == 0, result.stderr
    table = stubs.mounts(box["stubs"])
    assert {t: table[t] for t in [*DATA, "/mnt/parity"]} == dict.fromkeys(
        [*DATA, "/mnt/parity"], "rw"
    )
    assert str(box["system"]) not in table
    assert run(box, "status").stdout == "unlocked\n"
    assert run(box, "snapraid", "sync").returncode == 0


def test_unlock_stops_snapperd_after_the_filesystems_are_writable_again(box):
    # A snapperd started while locked (by fix) kept refusing with EROFS after the unlock until it
    # was restarted (guest-script probe, run 4).
    run(box, "lock", "arch2 failed")

    run(box, "unlock")

    order = [c[:2] for c in stubs.calls(box["stubs"]) if c[0] in ("mount", "umount", "systemctl")]
    assert order[-1] == ["systemctl", "stop"]
    assert stubs.calls(box["stubs"], "systemctl") == [["systemctl", "stop", "snapperd.service"]]


def test_a_failed_unlock_keeps_snapperd_and_the_refusal(box):
    run(box, "lock", "arch2 failed")
    stubs.configure(box["stubs"], busy=[])
    (box["stubs"] / "umount").write_text("#!/bin/sh\nexit 32\n")

    result = run(box, "unlock")

    assert result.returncode == 1
    assert stubs.calls(box["stubs"], "systemctl") == []
    assert run(box, "status").stdout.startswith("locked\n")


def test_locking_twice_does_not_stack_a_second_bind(box):
    run(box, "lock", "arch2 failed")
    run(box, "lock", "arch2 failed")
    run(box, "unlock")

    assert str(box["system"]) not in stubs.mounts(box["stubs"])
    binds = [c for c in stubs.calls(box["stubs"], "mount") if c[1] == "--bind"]
    assert len(binds) == 1


def test_a_wrapper_call_without_a_known_command_is_refused_while_locked(box):
    run(box, "lock", "arch2 failed")

    result = run(box, "snapraid", "--help")

    assert (result.returncode, result.stderr) == (30, REFUSED.format(action="snapraid --help"))
    assert stubs.calls(box["stubs"], "snapraid") == []


@pytest.mark.parametrize("command", ["sync", "scrub", "touch", "rehash", "pool"])
def test_the_snapraid_wrapper_refuses_writing_or_unknown_commands_while_locked(box, command):
    run(box, "lock", "arch2 failed")

    result = run(box, "snapraid", "-c", "/etc/snapraid.conf", command)

    assert (result.returncode, result.stderr) == (30, REFUSED.format(action=f"snapraid {command}"))
    assert stubs.calls(box["stubs"], "snapraid") == []


@pytest.mark.parametrize("command", ["fix", "check", "status", "diff", "list", "locate"])
def test_the_snapraid_wrapper_runs_reading_commands_and_fix_while_locked(box, command):
    run(box, "lock", "arch2 failed")

    result = run(box, "snapraid", "-c", "/etc/snapraid.conf", command, "-d", "arch2")

    assert result.returncode == 0, result.stderr
    assert stubs.calls(box["stubs"], "snapraid") == [
        ["snapraid", "-c", "/etc/snapraid.conf", command, "-d", "arch2"]
    ]


@pytest.mark.parametrize(
    "command", ["sync", "dsync", "diff", "resume", "cleanup", "cleanup-all", "scrub", "snapper"]
)
def test_the_snapraid_btrfs_wrapper_refuses_commands_that_write_or_snapshot(box, command):
    run(box, "lock", "arch2 failed")

    result = run(box, "snapraid-btrfs", "-c", "/etc/snapraid.conf", "--noninteractive", command)

    assert (result.returncode, result.stderr) == (
        30,
        REFUSED.format(action=f"snapraid-btrfs {command}"),
    )
    assert stubs.calls(box["stubs"], "snapraid-btrfs") == []


@pytest.mark.parametrize("command", ["fix", "check", "status", "ls", "list", "config"])
def test_the_snapraid_btrfs_wrapper_runs_fix_and_reading_commands_while_locked(box, command):
    run(box, "lock", "arch2 failed")

    result = run(box, "snapraid-btrfs", "-u", "sync", command)

    assert result.returncode == 0, result.stderr
    assert stubs.calls(box["stubs"], "snapraid-btrfs") == [
        ["snapraid-btrfs", "-u", "sync", command]
    ]


@pytest.mark.parametrize(
    ("args", "runs"),
    [
        (["--log", "sync", "fix", "-d", "arch2"], True),  # "sync" is the value of --log
        (["-vl", "fix", "sync"], False),  # "fix" is the value of -l in a group of short options
        (["--log=fix", "sync"], False),
        (["-d", "arch2", "--", "status"], True),
    ],
)
def test_the_command_word_is_found_past_the_values_of_options(box, args, runs):
    run(box, "lock", "arch2 failed")

    result = run(box, "snapraid", *args)

    assert result.returncode == (0 if runs else 30), result.stderr


def test_the_wrappers_pass_everything_through_when_unlocked(box):
    assert run(box, "snapraid", "sync", "-l", "/tmp/x.log").returncode == 0
    assert run(box, "snapraid-btrfs", "sync").returncode == 0
    assert stubs.calls(box["stubs"], "snapraid") == [["snapraid", "sync", "-l", "/tmp/x.log"]]


@pytest.mark.parametrize(
    "primitive", ["mover_copy_item", "mover_link_item", "mover_unlink_pool_copy"]
)
def test_every_mover_primitive_is_refused_while_locked_and_touches_nothing(box, primitive):
    src = box["tmp"] / "pool" / "a.bin"
    src.parent.mkdir()
    src.write_bytes(b"pool copy")
    dest = box["tmp"] / "base" / "a.bin"
    run(box, "lock", "arch2 failed")

    result = run(box, primitive, str(src), str(dest), script=EFFECTS)

    assert (result.returncode, result.stderr) == (30, REFUSED.format(action=primitive))
    assert src.read_bytes() == b"pool copy"
    assert not dest.parent.exists()


def test_open_for_resync_makes_everything_writable_and_lets_only_the_sync_through(box):
    run(box, "lock", "arch2 failed")

    result = run(box, "open-for-resync")

    assert result.returncode == 0, result.stderr
    table = stubs.mounts(box["stubs"])
    assert {t: table[t] for t in [*DATA, "/mnt/parity"]} == dict.fromkeys(
        [*DATA, "/mnt/parity"], "rw"
    )
    assert str(box["system"]) not in table
    assert stubs.calls(box["stubs"], "systemctl") == [["systemctl", "stop", "snapperd.service"]]
    assert run(box, "status").stdout.splitlines()[:3] == [
        "locked",
        "reason: arch2 failed",
        "stage: resync",
    ]
    assert run(box, "snapraid", "sync").returncode == 0
    assert run(box, "snapraid-btrfs", "-c", "/etc/snapraid.conf", "sync").returncode == 0
    # snapsync sync deletes the older sets once the new one is complete (probe T062 run 1)
    assert run(box, "snapraid-btrfs", "-c", "/etc/snapraid.conf", "cleanup").returncode == 0
    for refused in (
        ["snapraid", "scrub"],
        ["snapraid-btrfs", "cleanup-all"],
        ["snapraid", "touch"],
    ):
        assert run(box, *refused).returncode == 30
    mover = run(box, "mover_copy_item", "/nonexistent", "/nonexistent2", script=EFFECTS)
    assert mover.returncode == 30


def test_open_for_resync_needs_the_lock(box):
    result = run(box, "open-for-resync")

    assert result.returncode == 1
    assert "not locked" in result.stderr
    assert stubs.calls(box["stubs"], "mount") == []


def test_unlock_after_the_resync_stage_lifts_everything(box):
    run(box, "lock", "arch2 failed")
    run(box, "open-for-resync")

    result = run(box, "unlock")

    assert result.returncode == 0, result.stderr
    assert run(box, "status").stdout == "unlocked\n"
    assert run(box, "snapraid", "scrub").returncode == 0
