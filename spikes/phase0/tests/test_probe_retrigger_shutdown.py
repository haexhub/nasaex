"""The shutdown and data-safety part of the retrigger variant probe (T051i,
vm-tests/probes/probe_retrigger_v2.py): Fisher's exact test, the leftover and kill-wait lines of a
shutdown, the guest capture of the mergerfs servers, the btrfs device stats and the data checks
after the next boot, and the summary over the shutdown cycles. The cycles themselves need the VM."""

import pytest
from probes.retrigger_shutdown import (
    capture_summary,
    classify_shutdown,
    compare_counts,
    fisher_two_sided,
    judge_data,
    parse_device_stats,
    summarize_shutdowns,
)

# --- Fisher's exact test (reference values: scipy.stats.fisher_exact, two-sided, 1.17.1) -------


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ((17, 14, 8, 23), 0.037367535494091844),  # T051g: 17 of 31 with the unit, 8 of 31 without
        ((10, 0, 0, 10), 1.082508822446903e-05),
        ((5, 55, 5, 55), 1.0),
        ((3, 57, 9, 51), 0.12544754736342392),
        ((0, 60, 2, 58), 0.4957983193277311),
        ((1, 30, 2, 29), 1.0),
    ],
)
def test_fisher_two_sided_matches_the_reference(table, expected):
    assert fisher_two_sided(*table) == pytest.approx(expected, rel=1e-9)


def test_fisher_two_sided_rejects_negative_counts():
    with pytest.raises(ValueError):
        fisher_two_sided(1, -1, 2, 3)


def test_compare_counts_states_both_rates_and_the_p_value():
    result = compare_counts(17, 31, 8, 31)
    assert result == {
        "x": "17 of 31",
        "y": "8 of 31",
        "p": pytest.approx(0.037367535494091844, rel=1e-9),
    }


# --- one shutdown: leftover processes and the kill wait ------------------------------------------

JOURNAL = [
    "[   40.100000] server systemd[1]: Unmounting /mnt/cache...",
    "[   40.130000] server systemd[1]: mnt-cache.mount: Unit process 627 (mount.fuse.merg) "
    "remains running after unit stopped.",
    "[   40.131000] server systemd[1]: Unmounted /mnt/cache.",
    "[   40.140000] server systemd[1]: smbd.service: Unit process 700 (smbd-notifyd) "
    "remains running after unit stopped.",
]
CONSOLE = [
    "[   40.130000] systemd[1]: mnt-cache.mount: Unit process 627 (mount.fuse.merg) remains "
    "running after unit stopped.",
    "[   41.000000] systemd-shutdown[1]: Sending SIGTERM to remaining processes...",
    "[   51.000000] systemd-shutdown[1]: Waiting for process: 627 (mount.fuse.merg), 631 (sleep)",
    "[  131.000000] systemd-shutdown[1]: Sending SIGKILL to PID 627 (mount.fuse.merg).",
]


def test_classify_shutdown_merges_the_leftovers_of_journal_and_console():
    result = classify_shutdown(CONSOLE, JOURNAL, shutdown_s=92.4)
    assert result["leftovers"] == [
        {
            "unit": "mnt-cache.mount",
            "pid": 627,
            "comm": "mount.fuse.merg",
            "console": True,
            "journal": True,
        },
        {
            "unit": "smbd.service",
            "pid": 700,
            "comm": "smbd-notifyd",
            "console": False,
            "journal": True,
        },
    ]
    assert result["outlived"] is True
    assert result["outlived_units"] == ["mnt-cache.mount"]


def test_classify_shutdown_counts_a_kill_wait_of_a_mergerfs_server():
    result = classify_shutdown(CONSOLE, JOURNAL, shutdown_s=92.4)
    assert result["waiting"] == [
        {"pid": 627, "comm": "mount.fuse.merg"},
        {"pid": 631, "comm": "sleep"},
    ]
    assert result["sigkill"] == [{"pid": 627, "comm": "mount.fuse.merg"}]
    assert result["kill_wait"] is True
    assert result["long_shutdown"] is True


def test_classify_shutdown_ignores_other_processes_for_the_flags():
    console = [
        "[ 9.0] systemd[1]: smbd.service: Unit process 700 (smbd) remains running after unit "
        "stopped.",
        "[ 19.0] systemd-shutdown[1]: Waiting for process: 700 (smbd)",
    ]
    result = classify_shutdown(console, [], shutdown_s=12.0)
    assert result["outlived"] is False
    assert result["outlived_units"] == []
    assert result["kill_wait"] is False
    assert result["long_shutdown"] is False
    assert result["waiting"] == [{"pid": 700, "comm": "smbd"}]


def test_classify_shutdown_of_a_clean_shutdown():
    result = classify_shutdown(["[ 3.0] systemd[1]: Unmounted /mnt/cache."], [], shutdown_s=4.1)
    assert result["leftovers"] == []
    assert not result["outlived"] and not result["kill_wait"]


# --- the guest capture after the mergerfs mounts stopped -----------------------------------------

CAPTURE = {
    "boot_id": "b1",
    "t": 40.5,
    "servers": [
        {
            "pid": 627,
            "comm": "mount.fuse.merg",
            "cgroup": "0::/system.slice/mnt-cache.mount",
            "threads": [
                {"tid": 627, "state": "S", "wchan": "futex_wait_queue"},
                {"tid": 628, "state": "S", "wchan": "fuse_dev_do_read"},
            ],
        }
    ],
    "connections": {"40": {"waiting": 0}},
    "holders": [{"pid": 1, "comm": "systemd", "mounts": ["/mnt/cache"]}],
    "gone_after_s": {"627": 0.05},
}


def test_capture_summary_of_a_server_that_exits_within_the_poll():
    summary = capture_summary(CAPTURE)
    assert summary == {
        "servers": 1,
        "units": ["mnt-cache.mount"],
        "still_alive": [],
        "gone_after_s_max": 0.05,
        "connections": 1,
        "waiting_requests": 0,
        "holders": ["systemd:/mnt/cache"],
        "wchans": {"fuse_dev_do_read": 1, "futex_wait_queue": 1},
    }


def test_capture_summary_lists_a_server_still_alive_after_the_poll():
    capture = CAPTURE | {"gone_after_s": {"627": None}, "connections": {}, "holders": []}
    summary = capture_summary(capture)
    assert summary["still_alive"] == [627]
    assert summary["gone_after_s_max"] is None
    assert summary["connections"] == 0


def test_capture_summary_without_any_server():
    summary = capture_summary({"servers": [], "connections": {}, "holders": [], "gone_after_s": {}})
    assert summary["servers"] == 0
    assert summary["units"] == []
    assert summary["still_alive"] == []


# --- data safety after the next boot -------------------------------------------------------------

STATS = """\
[/dev/vdb].write_io_errs    0
[/dev/vdb].read_io_errs     0
[/dev/vdb].flush_io_errs    0
[/dev/vdb].corruption_errs  0
[/dev/vdb].generation_errs  0
[/dev/vdc].write_io_errs    0
[/dev/vdc].read_io_errs     0
[/dev/vdc].flush_io_errs    0
[/dev/vdc].corruption_errs  0
[/dev/vdc].generation_errs  0
"""
SCRUB_OK = "Scrub started:    ...\nStatus:           finished\nError summary:    no errors found\n"
FILES = {
    "c3-fsync.bin": {"expected": "aa", "cache": "aa", "pool": "aa"},
    "c3-nofsync.bin": {"expected": "bb", "cache": "bb", "pool": "bb"},
}
MOUNTS = {"/mnt/pool": "btrfs", "/mnt/base": "fuse.mergerfs", "/mnt/cache": "fuse.mergerfs"}


def test_parse_device_stats_reads_every_counter():
    stats = parse_device_stats(STATS + "garbage\n")
    assert len(stats) == 10
    assert stats["[/dev/vdc].corruption_errs"] == 0


def test_judge_data_passes_a_clean_check():
    result = judge_data(FILES, (0, SCRUB_OK), (0, STATS), [], MOUNTS)
    assert result == {"ok": True, "failed": [], "files": 2}


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"files": FILES | {"x": {"expected": "aa", "cache": "ab", "pool": "aa"}}}, "file x"),
        ({"files": FILES | {"x": {"expected": "aa", "cache": None, "pool": None}}}, "file x"),
        ({"scrub": (3, "Error summary:    csum=2\n")}, "scrub"),
        ({"scrub": (0, "Status: aborted\n")}, "scrub"),
        ({"stats": (64, STATS.replace("generation_errs  0", "generation_errs  1", 1))}, "stats"),
        ({"stats": (0, "")}, "stats"),
        ({"dmesg": ["[ 9.1] BTRFS error (device vdb): bdev /dev/vdb errs: wr 0, rd 1"]}, "dmesg"),
        ({"mounts": MOUNTS | {"/mnt/cache": None}}, "mount /mnt/cache"),
    ],
)
def test_judge_data_fails_each_finding(change, reason):
    args = {
        "files": FILES,
        "scrub": (0, SCRUB_OK),
        "stats": (0, STATS),
        "dmesg": [],
        "mounts": MOUNTS,
    } | change
    result = judge_data(**args)
    assert result["ok"] is False
    assert any(f.startswith(reason) for f in result["failed"]), result["failed"]


# --- summary over the shutdown cycles ------------------------------------------------------------


def _cycle(index, outlived, kill_wait, data_ok=True, units=("mnt-cache.mount",)):
    return {
        "index": index,
        "shutdown": {
            "outlived": outlived,
            "outlived_units": list(units) if outlived else [],
            "kill_wait": kill_wait,
            "long_shutdown": kill_wait,
            "shutdown_s": 91.0 if kill_wait else 5.0,
        },
        "data": {"ok": data_ok, "failed": [] if data_ok else ["scrub"], "files": 2},
    }


def test_summarize_shutdowns_counts_cycles_and_data_checks():
    cycles = [
        _cycle(0, True, False),
        _cycle(1, True, True, units=("mnt-base.mount", "mnt-cache.mount")),
        _cycle(2, False, False),
        _cycle(3, False, False, data_ok=False),
        {"index": 4, "shutdown": _cycle(4, False, False)["shutdown"], "data": None},
    ]
    summary = summarize_shutdowns(cycles)
    assert summary["cycles"] == 5
    assert summary["outlived"] == 2
    assert summary["outlived_cycles"] == [0, 1]
    assert summary["outlived_units"] == {"mnt-base.mount": 1, "mnt-cache.mount": 2}
    assert summary["kill_wait"] == 1
    assert summary["kill_wait_cycles"] == [1]
    assert summary["long_shutdown"] == 1
    assert summary["data_checked"] == 4
    assert summary["data_failed_cycles"] == [3]
    assert summary["data_unchecked_cycles"] == [4]
