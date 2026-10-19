"""The S1.9 retry timeline probe (vm-tests/probes/probe_retry_timeline.py): parsing the btrfs
commit counter and the `btrfs filesystem usage` lines it keeps, and the summary of a timeline
(when a write first succeeds, when the pool branch shows the freed space, when btrfs commits).
The timeline itself needs the VMs; so do the append retries of probe_append_retry.py, whose rows
are summarized here."""

from probes.retry_timeline import (
    SAMPLER,
    append_gap,
    parse_commit_stats,
    summarize,
    summarize_appends,
    usage_lines,
)

COMMIT_STATS = """commits 17
cur_commit_ms 0
last_commit_ms 12
max_commit_ms 40
total_commit_ms 310
"""

USAGE = """Overall:
    Device size:                  17179869184
    Device allocated:              5402263552
    Device unallocated:           11777605632
    Device missing:                         0
    Device slack:                           0
    Used:                          4831838208
    Free (estimated):              6145949696      (min: 6145949696)
    Free (statfs, df):             6145949696
    Data ratio:                          2.00
    Metadata ratio:                      2.00
    Global reserve:                   5767168      (used: 0)
    Multiple profiles:                     no

Data,RAID1: Size:2684354560, Used:2415919104 (90.00%)
   /dev/vdb     2684354560
   /dev/vdc     2684354560
"""

MIB = 1 << 20


def sample(t, avail, commits, errno=None):
    """One timeline sample: seconds since t0, pool branch avail, commit count, write errno."""
    return {
        "t": t,
        "pool_avail": avail,
        "commits": commits,
        "write": {"errno": errno, "step": "open" if errno else None},
    }


def test_the_guest_sampler_is_valid_python():
    compile(SAMPLER, "sampler", "exec")


def test_the_commit_count_is_read_from_commit_stats():
    assert parse_commit_stats(COMMIT_STATS) == 17


def test_usage_keeps_the_space_lines_and_the_data_profile_only():
    assert usage_lines(USAGE) == [
        "Device allocated:              5402263552",
        "Device unallocated:           11777605632",
        "Used:                          4831838208",
        "Free (estimated):              6145949696      (min: 6145949696)",
        "Free (statfs, df):             6145949696",
        "Data,RAID1: Size:2684354560, Used:2415919104 (90.00%)",
    ]


def test_a_timeline_reports_when_the_first_write_succeeds_and_what_failed_before():
    timeline = [
        sample(0.1, 0, 5, "EROFS"),
        sample(0.6, 0, 5, "EROFS"),
        sample(1.1, 900 * MIB, 6),
        sample(1.6, 899 * MIB, 6),
    ]
    got = summarize(timeline, need=800 * MIB, minfree=64 * MIB)
    assert got["first_write_ok_s"] == 1.1
    assert got["errors_before_ok"] == ["EROFS at open"]
    assert got["failed_after_ok"] == 0
    assert got["first_free_need_s"] == 1.1
    assert got["first_free_minfree_s"] == 1.1
    assert got["first_commit_s"] == 1.1
    assert got["pool_avail_at_first_ok"] == 900 * MIB
    assert (got["pool_avail_first"], got["pool_avail_last"]) == (0, 899 * MIB)


def test_a_timeline_without_success_commit_or_freed_space_reports_none():
    timeline = [sample(0.1, 10 * MIB, 3, "EROFS"), sample(0.6, 10 * MIB, 3, "EROFS")]
    got = summarize(timeline, need=800 * MIB, minfree=64 * MIB)
    assert got["first_write_ok_s"] is None
    assert got["errors_before_ok"] == ["EROFS at open"]
    assert got["first_free_need_s"] is None
    assert got["first_free_minfree_s"] is None
    assert got["first_commit_s"] is None
    assert got["pool_avail_at_first_ok"] is None


def test_a_write_that_fails_again_after_the_first_success_is_counted():
    timeline = [sample(0.1, 900 * MIB, 3), sample(0.6, 900 * MIB, 3, "ENOSPC")]
    got = summarize(timeline, need=800 * MIB, minfree=64 * MIB)
    assert got["first_write_ok_s"] == 0.1
    assert got["errors_before_ok"] == []
    assert got["failed_after_ok"] == 1


def test_space_above_minfreespace_but_below_the_need_is_reported_apart():
    timeline = [sample(0.1, 100 * MIB, 3), sample(0.6, 900 * MIB, 3)]
    got = summarize(timeline, need=800 * MIB, minfree=64 * MIB)
    assert got["first_free_minfree_s"] == 0.1
    assert got["first_free_need_s"] == 0.6


# The append retry rows of probe_append_retry.py (T051h follow-up, variants Vr and V5 to V8).


def append_row(variant, gap, client_size, read_back=True):
    """One append retry: the bytes between leftover and source, the client's size before it."""
    return {
        "variant": variant,
        "gap_bytes": gap,
        "client_before": {"size": client_size},
        "read_back": read_back,
        "unallocated_bytes": gap,
    }


def test_append_retries_are_summarized_per_variant():
    rows = [
        append_row("Vr", 167772160, 268439552, read_back=False),
        append_row("Vr", 0, 100667392),
        append_row("V5", 0, 100667392),
        append_row("V5", 0, 100667392),
    ]
    got = summarize_appends(rows)
    assert got["Vr"] == {
        "n": 2,
        "with_gap": 1,
        "gap_bytes": [0, 167772160],
        "client_size_before": [100667392, 268439552],
        "read_back_ok": 1,
        "unallocated_bytes": [0, 167772160],
    }
    assert got["V5"]["n"] == 2
    assert got["V5"]["with_gap"] == 0


def test_a_failed_client_stat_is_kept_as_its_error():
    rows = [{**append_row("V8", 0, 0), "client_before": {"error": "ENOENT"}}]
    assert summarize_appends(rows)["V8"]["client_size_before"] == ["ENOENT"]


def test_append_gap_is_what_lies_between_leftover_and_source():
    assert append_gap(size=536875008, head=4096, kept=100659424, written=268435456) == 167776032
    assert append_gap(size=369102704, head=4096, kept=100663152, written=268435456) == 0
