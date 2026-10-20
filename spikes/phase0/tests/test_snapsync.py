"""The snapshot-supported sync and fix wrapper of S2 (guest/snapsync.sh, task T060) on the host,
with stand-ins for snapraid-btrfs, snapraid, snapper, systemctl, btrfs and df
(tests/guest_stubs.py). The tools' real behaviour is measured in the VM (probe T057 and the
guest-script probe), not here."""

import subprocess
from pathlib import Path

import guest_stubs as stubs
import pytest

SNAPSYNC = Path(__file__).resolve().parents[1] / "guest" / "snapsync.sh"
CONFIGS = ["arch0", "arch1", "arch2"]
SRB = ["snapraid-btrfs", "-c", "/etc/snapraid.conf", "--noninteractive"]
EQUAL = "summary:exit:equal\ncontent_info:block_unsynced:0\n"


def snapshots(*values):
    """A snapper list: snapshot 0 and one snapshot per userdata value (None: no userdata)."""
    out = [{"number": 0, "userdata": None}]
    for number, value in enumerate(values, start=1):
        out.append({"number": number, "userdata": value and {"snapraid-btrfs": value}})
    return out


ALL_SYNCED = {c: snapshots("synced", "synced") for c in CONFIGS}


@pytest.fixture
def box(tmp_path):
    directory = stubs.install(tmp_path / "stubs", conf={"snapper": ALL_SYNCED})
    env = stubs.environment(
        directory,
        SNAPRAID_CONF="/etc/snapraid.conf",
        SNAPSYNC_CONFIGS=" ".join(CONFIGS),
        SNAPSYNC_LOG_DIR=str(tmp_path / "logs"),
        ARCHIVE_LOCK_DIR=str(tmp_path / "run"),
    )
    return {"stubs": directory, "env": env, "tmp": tmp_path}


def log(box, name):
    return str(box["tmp"] / name)


def run(box, *args):
    return subprocess.run(
        ["bash", str(SNAPSYNC), *args], env=box["env"], timeout=30, capture_output=True, text=True
    )


def tool_calls(box):
    return [c for c in stubs.calls(box["stubs"]) if c[0] not in ("snapper",)]


def test_sync_runs_snapraid_btrfs_in_the_c_locale_without_prompts_then_cleans_up(box):
    result = run(box, "sync", "--log", log(box, "sync.log"))

    assert result.returncode == 0, result.stderr
    assert stubs.calls(box["stubs"], "snapraid-btrfs") == [
        [*SRB, "sync", "-l", log(box, "sync.log")],
        [*SRB, "cleanup"],
    ]
    assert set(stubs.lc_all(box["stubs"], "snapraid-btrfs")) == {"C"}
    assert set(stubs.lc_all(box["stubs"], "snapper")) == {"C"}


def test_sync_passes_extra_snapraid_options_after_the_command(box):
    run(box, "sync", "--log", log(box, "s.log"), "--", "--gui", "--bw-limit=20M")

    assert stubs.calls(box["stubs"], "snapraid-btrfs")[0] == [
        *SRB,
        "sync",
        "-l",
        log(box, "s.log"),
        "--gui",
        "--bw-limit=20M",
    ]


def test_the_force_empty_override_is_passed_only_through_its_flag(box):
    run(box, "sync", "--force-empty", "--log", log(box, "s.log"))

    assert stubs.calls(box["stubs"], "snapraid-btrfs")[0] == [
        *SRB,
        "sync",
        "-E",
        "-l",
        log(box, "s.log"),
    ]


@pytest.mark.parametrize(
    "smuggled", ["-E", "--force-empty", "--force-em", "--force-em=value", "-vE"]
)
def test_force_empty_among_the_extra_options_is_refused_before_anything_runs(box, smuggled):
    result = run(box, "sync", "--", "--gui", smuggled)

    assert result.returncode == 2
    assert "--force-empty" in result.stderr
    assert stubs.calls(box["stubs"]) == []


def test_a_failed_sync_keeps_every_snapshot_and_reports_the_status(box):
    stubs.configure(box["stubs"], rc={"sync": 1}, snapper=ALL_SYNCED)

    result = run(box, "sync")

    assert result.returncode == 1
    assert [c[len(SRB)] for c in stubs.calls(box["stubs"], "snapraid-btrfs")] == ["sync"]


def test_a_sync_that_exits_0_without_a_synced_set_on_every_disk_is_not_cleaned_up(box):
    snapper = {**ALL_SYNCED, "arch1": snapshots("synced", "syncing")}
    stubs.configure(box["stubs"], snapper=snapper)

    result = run(box, "sync")

    assert result.returncode == 1
    assert "arch1: newest snapraid-btrfs snapshot 2 is syncing" in result.stderr
    assert [c[len(SRB)] for c in stubs.calls(box["stubs"], "snapraid-btrfs")] == ["sync"]


def test_a_failed_cleanup_fails_the_sync_command(box):
    stubs.configure(box["stubs"], rc={"cleanup": 3}, snapper=ALL_SYNCED)

    assert run(box, "sync").returncode == 3


def test_sync_is_refused_while_the_archive_is_locked(box):
    lock = box["tmp"] / "run"
    lock.mkdir()
    (lock / "reason").write_text("arch2 failed\n")

    result = run(box, "sync")

    assert result.returncode == 30
    assert (
        result.stderr
        == "archive-lock: refused: snapsync sync: the archive is locked (arch2 failed)\n"
    )
    assert stubs.calls(box["stubs"]) == []


def test_sync_without_a_log_option_writes_one_in_the_log_directory(box):
    run(box, "sync")

    log = Path(stubs.calls(box["stubs"], "snapraid-btrfs")[0][-1])
    assert log.parent == box["tmp"] / "logs" and log.name.startswith("sync-")


def test_fix_stops_snapperd_first_and_fixes_one_disk_from_the_live_filesystem(box):
    result = run(box, "fix", "arch2", "--log", log(box, "fix.log"))

    assert result.returncode == 0, result.stderr
    assert tool_calls(box) == [
        ["systemctl", "stop", "snapperd.service"],
        [*SRB, "fix", "-d", "arch2", "-l", log(box, "fix.log")],
    ]
    assert stubs.lc_all(box["stubs"], "snapraid-btrfs") == ["C"]


def test_fix_reports_the_exit_status_of_the_fix(box):
    stubs.configure(box["stubs"], rc={"fix": 1})

    assert run(box, "fix", "arch2").returncode == 1


def test_fix_runs_while_the_archive_is_locked(box):
    (box["tmp"] / "run").mkdir()
    (box["tmp"] / "run" / "reason").write_text("arch2 failed\n")

    assert run(box, "fix", "arch2").returncode == 0


@pytest.mark.parametrize(
    ("lists", "ok"),
    [
        (ALL_SYNCED, True),
        # pre-fix and post-fix snapshots of a fix, created after the synced set
        ({**ALL_SYNCED, "arch2": snapshots("synced", "created", "created")}, False),
        ({**ALL_SYNCED, "arch0": snapshots("synced", "syncing")}, False),
        # a snapshot without snapraid-btrfs userdata after the set does not count
        ({**ALL_SYNCED, "arch0": snapshots("synced", None)}, True),
        ({**ALL_SYNCED, "arch1": snapshots(None)}, False),
    ],
)
def test_complete_requires_the_newest_snapraid_btrfs_snapshot_of_every_config_synced(
    box, lists, ok
):
    stubs.configure(box["stubs"], snapper=lists)

    result = run(box, "complete")

    assert (result.returncode == 0) == ok, result.stderr
    assert stubs.calls(box["stubs"], "snapper") == [
        ["snapper", "--jsonout", "-c", c, "list", "--disable-used-space"] for c in CONFIGS
    ]


@pytest.mark.parametrize("configs", ["", "   "])
def test_complete_rejects_an_empty_snapper_config_list(box, configs):
    box["env"]["SNAPSYNC_CONFIGS"] = configs

    result = run(box, "complete")

    assert result.returncode == 1
    assert "snapsync: no snapper config; the set is not complete\n" in result.stderr
    assert stubs.calls(box["stubs"], "snapper") == []


def test_covered_diffs_the_config_of_the_synced_set(box):
    stubs.configure(box["stubs"], diff_log=EQUAL)

    result = run(box, "covered", "--log", log(box, "diff.log"))

    assert result.returncode == 0, result.stderr
    first, second = tool_calls(box)
    assert first == [*SRB, "-u", "sync", "config"]
    assert second[:2] == ["snapraid", "-c"] and second[3:] == ["-l", log(box, "diff.log"), "diff"]
    assert "data arch0 /mnt/archive/arch0/.snapshots/3/snapshot/" in Path(second[2]).read_text()


def test_covered_plain_diffs_the_live_config(box):
    stubs.configure(box["stubs"], diff_log=EQUAL)

    result = run(box, "covered", "--plain", "--log", log(box, "diff.log"))

    assert result.returncode == 0, result.stderr
    assert tool_calls(box) == [
        ["snapraid", "-c", "/etc/snapraid.conf", "-l", log(box, "diff.log"), "diff"]
    ]


@pytest.mark.parametrize(
    ("rc", "log"),
    [
        (2, "summary:exit:diff\ncontent_info:block_unsynced:0\n"),
        (2, "summary:exit:unsynced\ncontent_info:block_unsynced:600\n"),
        (0, "summary:exit:equal\ncontent_info:block_unsynced:5\n"),
        (0, "summary:exit:equal\n"),
        (0, ""),
        (1, EQUAL),
    ],
)
def test_covered_requires_exit_0_equal_and_no_unsynced_block(box, rc, log):
    stubs.configure(box["stubs"], diff_rc=rc, diff_log=log)

    result = run(box, "covered", "--plain")

    assert result.returncode == 1
    assert "not covered" in result.stderr


def test_covered_fails_when_the_synced_set_has_no_config(box):
    stubs.configure(box["stubs"], rc={"config": 68}, diff_log=EQUAL)

    result = run(box, "covered")

    assert result.returncode == 1
    assert stubs.calls(box["stubs"], "snapraid") == []


def test_free_waits_for_deleted_subvolumes_before_reading_the_space(box):
    stubs.configure(box["stubs"], avail=134217728)

    result = run(box, "free", "arch2")

    assert result.stdout == "134217728\n"
    assert tool_calls(box) == [
        ["btrfs", "subvolume", "sync", "/mnt/archive/arch2"],
        ["df", "--output=avail", "-B1", "/mnt/archive/arch2"],
    ]


@pytest.mark.parametrize("command", ["resume", "dsync", "diff", "cleanup", ""])
def test_commands_outside_the_workflow_do_not_exist(box, command):
    result = run(box, command)

    assert result.returncode == 2
    assert stubs.calls(box["stubs"]) == []
