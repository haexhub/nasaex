"""The recovery sequence of S2 (guest/recover.sh, task T062) on the host, with stand-ins for the
mount, disk and SnapRAID tools (tests/guest_stubs.py). Whether the steps restore real files is
measured in the VM (the recovery probe), not here."""

import json
import subprocess
from pathlib import Path

import guest_stubs as stubs
import pytest

RECOVER = Path(__file__).resolve().parents[1] / "guest" / "recover.sh"
SERIALS = ["arch0", "arch1", "arch2"]
EQUAL = "summary:exit:equal\ncontent_info:block_unsynced:0\n"
SYNCED = [{"number": 0, "userdata": None}, {"number": 1, "userdata": {"snapraid-btrfs": "synced"}}]
MIB = 1 << 20


@pytest.fixture
def box(tmp_path):
    archive, content = tmp_path / "mnt" / "archive", tmp_path / "mnt" / "archive-content"
    data = {s: str(archive / s) for s in SERIALS}
    system = tmp_path / "var-lib-snapraid"
    labels, devices = tmp_path / "by-label", tmp_path / "dev"
    for directory in (system, labels, devices):
        directory.mkdir()
    for serial in SERIALS:
        (labels / serial).touch()
    (devices / "spare2").touch()
    (devices / "spare-small").touch()
    conf = tmp_path / "snapraid.conf"
    conf.write_text(
        "parity /mnt/parity/snapraid.parity\n"
        f"content {system}/snapraid.content\n"
        "content /mnt/parity/snapraid.content\n"
        + "".join(f"data {s} {data[s]}/\n" for s in SERIALS)
    )
    table = {
        "/": "rw",
        "/mnt/parity": "rw",
        **dict.fromkeys(data.values(), "rw"),
        **{str(content / s): "rw" for s in SERIALS},
    }
    directory = stubs.install(tmp_path / "stubs", mounts=table)
    env = stubs.environment(
        directory,
        SNAPRAID_CONF=str(conf),
        SNAPSYNC_CONFIGS=" ".join(SERIALS),
        SNAPSYNC_LOG_DIR=str(tmp_path / "snapsync-logs"),
        ARCHIVE_LOCK_DIR=str(tmp_path / "lock"),
        RECOVER_ARCHIVE_ROOT=str(archive),
        RECOVER_CONTENT_ROOT=str(content),
        RECOVER_LABEL_DIR=str(labels),
        RECOVER_STATE_DIR=str(tmp_path / "state"),
        RECOVER_LOG_DIR=str(tmp_path / "logs"),
    )
    sizes = {str(devices / "spare2"): 512 * MIB, str(devices / "spare-small"): 256 * MIB}
    base = {"sizes": sizes, "snapper": dict.fromkeys(SERIALS, SYNCED), "diff_log": EQUAL}
    stubs.configure(directory, **base)
    return {
        "stubs": directory,
        "env": env,
        "tmp": tmp_path,
        "data": data,
        "labels": labels,
        "devices": devices,
        "base": base,
    }


def run(box, *args):
    return subprocess.run(
        ["bash", str(RECOVER), *args], env=box["env"], timeout=60, capture_output=True, text=True
    )


def configure(box, **extra):
    stubs.configure(box["stubs"], **{**box["base"], **extra})


def unmount(box, target):
    table = stubs.mounts(box["stubs"])
    del table[target]
    (box["stubs"] / "mounts.json").write_text(json.dumps(table))


def tool_words(box, tool):
    return [c for c in stubs.calls(box["stubs"], tool)]


# --- 1. detect -----------------------------------------------------------------------------------


def test_detect_stops_on_a_disk_without_failure_evidence(box):
    result = run(box, "detect", "arch2")

    assert result.returncode == 1
    assert "arch2 shows no failure" in result.stderr


@pytest.mark.parametrize(
    ("change", "evidence"),
    [
        ("unmounted", "is not mounted"),
        ("no-label", "no device carries the label arch2"),
        ("stats", "btrfs counts device errors"),
        # probe T062 run 1: right after device-ioerror the counters are 0 and reads come from the
        # page cache; only a direct read of the device fails
        ("direct-read", "a direct read of"),
    ],
)
def test_detect_names_its_evidence(box, change, evidence):
    if change == "unmounted":
        unmount(box, box["data"]["arch2"])
    elif change == "no-label":
        (box["labels"] / "arch2").unlink()
    elif change == "stats":
        configure(box, stats_rc=64)
    else:
        configure(box, dd_rc=1)

    result = run(box, "detect", "arch2")

    assert result.returncode == 0, result.stderr
    assert evidence in result.stdout


def test_detect_lists_every_piece_of_evidence_on_its_own_line(box):
    unmount(box, box["data"]["arch2"])
    (box["labels"] / "arch2").unlink()

    result = run(box, "detect", "arch2")

    assert result.stdout.splitlines() == [
        "recover.sh: arch2 failed:",
        f"  {box['data']['arch2']} is not mounted",
        "  no device carries the label arch2",
    ]


# --- 2. lock -------------------------------------------------------------------------------------


def test_lock_takes_the_failed_disk_out_and_makes_the_others_read_only(box):
    result = run(box, "lock", "arch2")

    assert result.returncode == 0, result.stderr
    table = stubs.mounts(box["stubs"])
    assert box["data"]["arch2"] not in table
    assert str(box["tmp"] / "mnt" / "archive-content" / "arch2") not in table
    assert [table[box["data"][s]] for s in ("arch0", "arch1")] == ["ro", "ro"]
    assert table["/mnt/parity"] == "ro"


def test_lock_unmounts_a_busy_failed_disk_lazily(box):
    configure(box, umount_fails=[box["data"]["arch2"]])

    result = run(box, "lock", "arch2")

    assert result.returncode == 0, result.stderr
    assert ["umount", "-l", box["data"]["arch2"]] in stubs.calls(box["stubs"], "umount")


def test_lock_fails_its_check_when_a_location_stays_writable(box):
    configure(box, busy=[box["data"]["arch1"]])

    result = run(box, "lock", "arch2")

    assert result.returncode == 1
    assert "check failed" in result.stderr


# --- 3. replace ----------------------------------------------------------------------------------


def replace(box, device="spare2", size=512 * MIB):
    return run(box, "replace", "arch2", str(box["devices"] / device), str(size))


def prepare_recovery(box):
    assert run(box, "lock", "arch2").returncode == 0
    assert replace(box).returncode == 0


def test_replace_refuses_before_the_lock(box):
    result = replace(box)

    assert result.returncode == 1
    assert "not locked" in result.stderr
    assert stubs.calls(box["stubs"], "mkfs.btrfs") == []


def test_replace_refuses_a_smaller_disk(box):
    run(box, "lock", "arch2")

    result = replace(box, "spare-small")

    assert result.returncode == 1
    assert f"holds {256 * MIB} bytes, less than the {512 * MIB}" in result.stderr
    assert stubs.calls(box["stubs"], "mkfs.btrfs") == []


def test_replace_refuses_a_disk_that_carries_a_filesystem(box):
    run(box, "lock", "arch2")
    configure(box, formatted=[str(box["devices"] / "spare2")])

    result = replace(box)

    assert result.returncode == 1
    assert "already carries a filesystem signature" in result.stderr
    assert stubs.calls(box["stubs"], "mkfs.btrfs") == []


def test_replace_builds_the_disk_layout_and_checks_it(box):
    run(box, "lock", "arch2")

    result = replace(box)

    assert result.returncode == 0, result.stderr
    device = str(box["devices"] / "spare2")
    assert stubs.calls(box["stubs"], "mkfs.btrfs") == [["mkfs.btrfs", "-q", "-L", "arch2", device]]
    assert ["mount", "-o", "subvol=data", device, box["data"]["arch2"]] in stubs.calls(
        box["stubs"], "mount"
    )
    assert sorted(p.name for p in Path(box["data"]["arch2"]).iterdir()) == ["branch", "trash"]
    assert stubs.calls(box["stubs"], "spike-snapshots-init") == [["spike-snapshots-init", "arch2"]]
    assert stubs.mounts(box["stubs"])[box["data"]["arch2"]] == "rw"


def test_replace_fails_its_check_without_a_snapshots_subvolume(box):
    run(box, "lock", "arch2")
    configure(box, not_subvolume=[box["data"]["arch2"] + "/.snapshots"])

    result = replace(box)

    assert result.returncode == 1
    assert ".snapshots is no subvolume" in result.stderr
    assert box["data"]["arch2"] not in stubs.mounts(box["stubs"])
    assert str(box["tmp"] / "mnt" / "archive-content" / "arch2") not in stubs.mounts(box["stubs"])


def test_recover_refuses_after_a_replacement_was_rejected(box):
    run(box, "lock", "arch2")

    assert replace(box, "spare-small").returncode == 1
    result = run(box, "recover", "arch2")

    assert result.returncode == 1
    assert "run step 3 first" in result.stderr
    assert stubs.calls(box["stubs"], "snapraid-btrfs") == []


# --- 4. recover ----------------------------------------------------------------------------------

RECOVERED = "summary:exit:ok\nstatus:recovered:arch2:branch/a.bin\n"
LOSS = (
    "unrecoverable:7:arch2:branch/n.bin: Unrecoverable error at position 0\n"
    "status:unrecoverable:arch2:branch/n.bin\n"
    "status:unrecoverable:arch2:branch/w.bin\n"
    "status:recovered:arch2:branch/a.bin\n"
    "summary:exit:unrecoverable\n"
)


def test_recover_fixes_the_disk_through_the_snapshot_workflow(box):
    prepare_recovery(box)
    configure(box, srb={"fix": {"rc": 0, "log": RECOVERED}})

    result = run(box, "recover", "arch2")

    assert result.returncode == 0, result.stderr
    fix = stubs.calls(box["stubs"], "snapraid-btrfs")[0]
    assert fix[fix.index("fix") :][:3] == ["fix", "-d", "arch2"]
    assert stubs.calls(box["stubs"], "systemctl") == [["systemctl", "stop", "snapperd.service"]]
    assert "every file of arch2 recovered" in result.stdout


def test_recover_plain_runs_snapraid_fix(box):
    prepare_recovery(box)
    configure(box, snapraid={"fix": {"rc": 0, "log": RECOVERED}})

    result = run(box, "recover", "arch2", "--plain")

    assert result.returncode == 0, result.stderr
    fix = stubs.calls(box["stubs"], "snapraid")[0]
    assert fix[:5] == ["snapraid", "-c", box["env"]["SNAPRAID_CONF"], "-d", "arch2"]
    assert fix[-1] == "fix"
    assert stubs.calls(box["stubs"], "snapraid-btrfs") == []


def test_recover_lists_unrecoverable_files_and_exits_3(box):
    prepare_recovery(box)
    configure(box, srb={"fix": {"rc": 1, "log": LOSS}})

    result = run(box, "recover", "arch2")

    assert result.returncode == 3
    assert "  branch/n.bin\n  branch/w.bin\n" in result.stdout
    assert "a resync makes the loss final" in result.stdout


def test_recover_fails_without_a_summary_in_the_log(box):
    prepare_recovery(box)
    configure(box, srb={"fix": {"rc": 1, "log": ""}})

    assert run(box, "recover", "arch2").returncode == 1


# --- 5. verify -----------------------------------------------------------------------------------


def test_verify_needs_step_4(box):
    result = run(box, "verify", "arch2")

    assert result.returncode == 1
    assert stubs.calls(box["stubs"], "snapraid") == []


def test_verify_runs_an_audit_only_check_of_the_disk(box):
    prepare_recovery(box)
    configure(box, srb={"fix": {"log": RECOVERED}}, snapraid={"check": {"rc": 0, "log": ""}})
    run(box, "recover", "arch2")

    result = run(box, "verify", "arch2")

    assert result.returncode == 0, result.stderr
    check = stubs.calls(box["stubs"], "snapraid")[0]
    assert check[3:6] == ["-d", "arch2", "-a"] and check[-1] == "check"


CHECK_LOSS = (
    "error:3:arch2:branch/n.bin: Open error. No such file\n"
    "status:unrecoverable:arch2:branch/w.bin\n"
)


def test_verify_accepts_listed_losses_only_when_asked(box):
    prepare_recovery(box)
    configure(
        box, srb={"fix": {"rc": 1, "log": LOSS}}, snapraid={"check": {"rc": 1, "log": CHECK_LOSS}}
    )
    run(box, "recover", "arch2")

    refused = run(box, "verify", "arch2")
    accepted = run(box, "verify", "arch2", "--accept-unrecoverable")

    assert refused.returncode == 1 and "--accept-unrecoverable" in refused.stderr
    assert accepted.returncode == 0, accepted.stderr


def test_verify_fails_on_a_file_that_step_4_did_not_list(box):
    prepare_recovery(box)
    other = "error:3:arch2:branch/a.bin: Data error at position 0\n"
    configure(box, srb={"fix": {"rc": 1, "log": LOSS}}, snapraid={"check": {"rc": 1, "log": other}})
    run(box, "recover", "arch2")

    result = run(box, "verify", "arch2", "--accept-unrecoverable")

    assert result.returncode == 1
    assert "branch/a.bin" in result.stderr


# --- 6. resync and 7. unlock ----------------------------------------------------------------------


def prepare_resync(box, fix_log=RECOVERED, fix_rc=0, check_rc=0, check_log=""):
    run(box, "lock", "arch2")
    replace(box)
    configure(
        box,
        srb={"fix": {"rc": fix_rc, "log": fix_log}},
        snapraid={
            "fix": {"rc": fix_rc, "log": fix_log},
            "check": {"rc": check_rc, "log": check_log},
        },
    )
    run(box, "recover", "arch2")
    run(box, "verify", "arch2", "--accept-unrecoverable")


def test_resync_needs_a_passed_verify(box):
    run(box, "lock", "arch2")

    result = run(box, "resync", "arch2")

    assert result.returncode == 1
    assert "step 5 has not passed" in result.stderr
    assert stubs.mounts(box["stubs"])["/mnt/parity"] == "ro"


def test_resync_refuses_to_make_a_listed_loss_final_unless_accepted(box):
    prepare_resync(box, LOSS, 1, 1, CHECK_LOSS)

    result = run(box, "resync", "arch2")

    assert result.returncode == 1
    assert "makes their loss final" in result.stderr
    assert stubs.mounts(box["stubs"])["/mnt/parity"] == "ro"


def test_resync_opens_the_filesystems_syncs_and_checks_the_coverage(box):
    prepare_resync(box)

    result = run(box, "resync", "arch2")

    assert result.returncode == 0, result.stderr
    assert stubs.mounts(box["stubs"])["/mnt/parity"] == "rw"
    srb = [c for c in stubs.calls(box["stubs"], "snapraid-btrfs") if "sync" in c or "cleanup" in c]
    assert [c[4] for c in srb] == ["sync", "cleanup", "-u"]  # sync, cleanup, the synced config
    status = subprocess.run(
        ["bash", str(RECOVER.parent / "archive-lock.sh"), "status"],
        env=box["env"],
        capture_output=True,
        text=True,
    ).stdout
    assert status.splitlines()[:3] == ["locked", "reason: arch2 failed", "stage: resync"]


def test_resync_plain_syncs_with_snapraid(box):
    prepare_resync(box)

    result = run(box, "resync", "arch2", "--plain")

    assert result.returncode == 0, result.stderr
    words = [c[-1] for c in stubs.calls(box["stubs"], "snapraid")]
    assert words[-2:] == ["sync", "diff"]
    assert not [c for c in stubs.calls(box["stubs"], "snapraid-btrfs") if "sync" in c]


def test_a_failed_resync_keeps_the_refusal(box):
    prepare_resync(box)
    configure(box, rc={"sync": 1})

    result = run(box, "resync", "arch2")

    assert result.returncode == 1
    assert run(box, "unlock", "arch2").returncode == 0  # the operator decides; unlock still works


def test_unlock_ends_with_every_location_writable(box):
    prepare_resync(box)
    run(box, "resync", "arch2")

    result = run(box, "unlock", "arch2")

    assert result.returncode == 0, result.stderr
    assert "unlocked" in result.stdout
    table = stubs.mounts(box["stubs"])
    assert set(table.values()) == {"rw"}


def test_unlock_clears_authorization_from_the_completed_recovery(box):
    prepare_resync(box)
    assert run(box, "resync", "arch2").returncode == 0
    assert run(box, "unlock", "arch2").returncode == 0

    result = run(box, "verify", "arch2")

    assert result.returncode == 1
    assert "run step 4 first" in result.stderr


def test_steps_need_a_disk_name(box):
    assert run(box, "detect").returncode == 2
    assert run(box, "fix", "arch2").returncode == 2
