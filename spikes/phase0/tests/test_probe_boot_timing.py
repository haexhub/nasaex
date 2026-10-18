"""The boot-mount probe (vm-tests/probes/probe_boot_mounts.py): parsing of the systemd and
journal output it collects per boot, and the summary over the boots. The boots themselves need
the VM."""

from probes.boot_timing import (
    boot_up,
    first_time,
    journal_lines,
    monotonic_s,
    parse_properties,
    summarize,
    window,
)

SHOW = """ActiveState=active
SubState=mounted
Result=success
ActiveEnterTimestampMonotonic=4812345
JobRunningTimeoutUSec=10s
"""

JOURNAL = """\
[    0.000000] server kernel: Linux version 6.18.56
[    4.100000] server kernel: BTRFS: device label spikepool devid 1 transid 9 /dev/vdb
[    4.102300] server kernel: BTRFS: device label spikepool devid 2 transid 9 /dev/vdc
[    4.500000] server systemd[1]: Found device /dev/disk/by-label/arch0.
[   14.900000] server systemd[1]: Job dev-disk-by\\x2dlabel-spikepool.device/start timed out.
-- Boot 123 --
not a journal line
"""


def boot(index, load, up, label_s=None):
    devices = {"arch0": {"active_s": label_s}, "spikepool": {"active_s": label_s}}
    mounts = {"mnt-pool.mount": {"ActiveState": "active" if up else "failed"}}
    return {"index": index, "host_load": [load, 0.0, 0.0], "devices": devices, "mounts": mounts}


def test_properties_are_parsed_by_key():
    props = parse_properties(SHOW)
    assert props["ActiveState"] == "active"
    assert props["JobRunningTimeoutUSec"] == "10s"
    assert parse_properties("") == {}


def test_monotonic_microseconds_become_seconds_and_zero_means_never():
    assert monotonic_s("4812345") == 4.812345
    assert monotonic_s("0") is None
    assert monotonic_s("") is None
    assert monotonic_s(None) is None


def test_journal_lines_carry_their_monotonic_time():
    lines = journal_lines(JOURNAL)
    assert [t for t, _ in lines] == [0.0, 4.1, 4.1023, 4.5, 14.9]
    assert lines[3][1].endswith("Found device /dev/disk/by-label/arch0.")


def test_window_keeps_the_lines_around_a_time_and_caps_them():
    lines = journal_lines(JOURNAL)
    assert [t for t, _ in window(lines, 4.5, before=0.5, after=0.0)] == [4.1, 4.1023, 4.5]
    assert len(window(lines, 4.5, before=10, after=20, limit=2)) == 2
    assert window(lines, None) == []


def test_first_time_finds_the_first_matching_line_at_or_after_a_time():
    lines = journal_lines(JOURNAL)
    assert first_time(lines, "BTRFS: device label spikepool") == 4.1
    assert first_time(lines, "BTRFS: device label spikepool", after=4.101) == 4.1023
    assert first_time(lines, "start timed out") == 14.9
    assert first_time(lines, "never logged") is None


def test_a_boot_is_up_only_when_every_mount_is_active():
    assert boot_up(boot(0, 1.0, True))
    assert not boot_up(boot(0, 1.0, False))
    assert not boot_up({"mounts": {}})


def test_summary_counts_failures_and_splits_times_and_load_by_outcome():
    boots = [boot(0, 1.0, True, 3.0), boot(1, 2.0, True, 5.0), boot(2, 9.0, False, None)]
    summary = summarize(boots)
    assert summary["boots"] == 3
    assert summary["failed"] == 1
    assert summary["failed_boots"] == [2]
    assert summary["failure_rate"] == 1 / 3
    assert summary["label_active_s"]["arch0"] == {
        "n": 2,
        "missing": 1,
        "min": 3.0,
        "median": 4.0,
        "max": 5.0,
    }
    assert summary["host_load1"] == {"up": [1.0, 2.0], "failed": [9.0]}


def test_summary_of_no_boot_is_empty_not_an_error():
    assert summarize([])["failure_rate"] is None
