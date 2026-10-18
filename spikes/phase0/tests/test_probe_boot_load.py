"""The boot-under-load probe (vm-tests/probes/probe_boot_load.py): the host pressure and memory
readings, the guest workload that copies the IO of S1.8 and S1.11, the host IO load and its
memory guard, and the summary over the boots. The boots themselves need the VM."""

import sys
import time

import pytest
from probes.boot_load import (
    HostLoad,
    heavy_io_steps,
    memory_low,
    parse_meminfo,
    parse_pressure,
    share_between,
    stall_share,
    summarize_load,
    zero_failure_bound,
)

PRESSURE = """some avg10=1.50 avg60=0.17 avg300=3.75 total=48389559202
full avg10=0.15 avg60=0.10 avg300=3.27 total=45953735872
"""

MEMINFO = """MemTotal:       32547840 kB
MemAvailable:   16196408 kB
Dirty:               732 kB
Writeback:            12 kB
HugePages_Total:       0
"""


def test_pressure_lines_are_parsed_per_kind():
    """PSI parsing keeps separate some/full averages and cumulative microsecond totals."""
    found = parse_pressure(PRESSURE)
    assert found["some"] == {
        "avg10": 1.5,
        "avg60": 0.17,
        "avg300": 3.75,
        "total": 48389559202.0,
    }
    assert found["full"]["avg10"] == 0.15


def test_meminfo_values_are_kib_and_lines_without_a_unit_are_kept():
    """Memory parsing preserves KiB counters and unitless page counts."""
    found = parse_meminfo(MEMINFO)
    assert found["MemAvailable"] == 16196408
    assert found["Writeback"] == 12
    assert found["HugePages_Total"] == 0


def test_stall_share_is_the_stalled_fraction_of_the_window():
    """Stall totals yield window fractions; missing data or zero duration yields None."""
    before = {"some": {"total": 1_000_000.0}, "full": {"total": 0.0}}
    after = {"some": {"total": 3_000_000.0}, "full": {"total": 500_000.0}}
    assert stall_share(before, after, 4.0) == 0.5
    assert stall_share(before, after, 4.0, kind="full") == 0.125
    assert stall_share(before, after, 0.0) is None
    assert stall_share({}, after, 4.0) is None


def test_share_between_uses_the_samples_inside_the_window():
    """Window shares use only enclosed samples and need at least two distinct times."""
    # (seconds since the start, io some total in microseconds)
    series = [(0.0, 0.0), (1.0, 100_000.0), (3.0, 1_100_000.0), (5.0, 1_200_000.0)]
    assert share_between(series, 1.0, 3.0) == 0.5
    assert share_between(series, 0.5, 4.0) == 0.5  # samples at 1.0 and 3.0
    assert share_between(series, 5.0, 9.0) is None  # one sample: no interval


def test_memory_guard_trips_below_the_floor_only():
    """Low or missing available memory trips the guard; the exact floor is allowed."""
    assert memory_low({"MemAvailable": 6 * 1024 * 1024 - 1}, floor_mib=6144)
    assert not memory_low({"MemAvailable": 6 * 1024 * 1024}, floor_mib=6144)
    assert memory_low({}, floor_mib=6144)  # unreadable counts as low: stop the load


def test_zero_failure_bound_is_the_exact_binomial_bound():
    """Clean-run bounds match binomial values and reject a run with no boots."""
    assert zero_failure_bound(20) == pytest.approx(0.1391, abs=1e-4)
    assert zero_failure_bound(10) == pytest.approx(0.2589, abs=1e-4)
    with pytest.raises(ValueError):
        zero_failure_bound(0)


def test_workload_writes_above_4_gib_and_never_syncs():
    """The guest workload preserves the S1 IO order, large-file size and absence of syncs."""
    steps = heavy_io_steps(
        "/mnt/cache", "/mnt/archive/arch1/branch", "large/a.bin", 2**32 + 9, 2**32 + 1
    )
    names = [name for name, _ in steps]
    commands = " ".join(command for _, command in steps)
    assert "sync" not in commands and "fsync" not in commands and "fdatasync" not in commands
    # the copy leaves the pool before the new file is written (the pool holds one, as in S1.8)
    assert names.index("remove the copy") < names.index("write the new file")
    # the S1.11 manifest read of the whole share is the last step before the shutdown
    assert names[-1] == "read the whole share (S1.11 manifest)"
    assert "4294967305" in commands  # the new file's size reaches the guest
    assert "/mnt/archive/arch1/branch/large/a.bin" in commands


def test_summary_splits_pressure_by_outcome_and_bounds_a_clean_run():
    """Summaries group pressure by boot outcome and bound the failure rate only for clean runs."""

    def boot(index, up, share):
        """Build a minimal boot observation with a chosen outcome and host stall share."""
        state = "active" if up else "failed"
        return {
            "index": index,
            "host_load": [1.0, 0.0, 0.0],
            "devices": {"spikepool": {"active_s": 5.0 if up else None}},
            "mounts": {
                "mnt-pool.mount": {
                    "ActiveState": state,
                    "Result": "success" if up else "dependency",
                }
            },
            "smbd": "active" if up else "inactive",
            "host_window": {"io_some_share": share, "io_some_avg10_max": share * 100},
            "workload_s": 100.0,
        }

    clean = summarize_load([boot(0, True, 0.1), boot(1, True, 0.3)])
    assert clean["failed"] == 0
    assert clean["io_some_share"] == {"up": [0.1, 0.3], "failed": []}
    assert clean["zero_failure_bound_95"] == pytest.approx(1 - 0.05**0.5)

    mixed = summarize_load([boot(0, True, 0.1), boot(1, False, 0.9)])
    assert mixed["failed_boots"] == [1]
    assert mixed["io_some_share"] == {"up": [0.1], "failed": [0.9]}
    assert mixed["zero_failure_bound_95"] is None


def _wait_for(condition, seconds=5.0):
    """Poll until the condition holds or the timeout expires, without asserting success."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not condition():
        time.sleep(0.05)


def test_host_load_stops_and_reports_when_memory_runs_low(tmp_path):
    """The host workload aborts on low memory, reports the cause and leaves no target file."""
    readings = iter([{"MemAvailable": 20 * 1024 * 1024}] * 3)
    load = HostLoad(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path / "load.bin",
        meminfo=lambda: next(readings, {"MemAvailable": 1024}),
        floor_mib=6144,
        interval_s=0.02,
    )
    load.start()
    _wait_for(lambda: load.aborted is not None)
    report = load.stop()
    assert report["aborted"].startswith("MemAvailable")
    assert report["started"] >= 1
    assert not (tmp_path / "load.bin").exists()


def test_host_load_restarts_its_command_and_removes_the_file(tmp_path):
    """The host workload repeats successful commands and removes its target when stopped."""
    target = tmp_path / "load.bin"
    load = HostLoad(
        [sys.executable, "-c", f"open({str(target)!r}, 'wb').write(b'x')"],
        target,
        meminfo=lambda: {"MemAvailable": 20 * 1024 * 1024},
        interval_s=0.02,
    )
    load.start()
    _wait_for(lambda: load.completed >= 2)
    report = load.stop()
    assert report["aborted"] is None
    assert report["completed"] >= 2
    assert not target.exists()
