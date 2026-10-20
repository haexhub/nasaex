"""The SnapRAID probe (vm-tests/probes/probe_snapraid.py, task T057): reading SnapRAID's `-l` log
tags, its `locate` report and `filefrag` output, the first-fit hypothesis checked against them,
snapper's JSON listing, and the two guest scripts (seeded files written in a given order, and the
parity file compared with the XOR of the data blocks at each position). The measurements
themselves need the VM."""

import json
import subprocess
import sys

from probes.snapraid_observe import (
    BLOCK,
    FILL,
    XOR,
    allocate,
    blocks,
    extent,
    first_fit,
    first_physical,
    fix_report,
    parity_states,
    parse_locate,
    ranges,
    run_positions,
    snapper_userdata,
    step_stats,
    summary,
    tag_fields,
)

from testlib.fixtures import Entry, content_chunks, content_sha256

SYNC_LOG = """version:14.4
run:begin:0:40:40
run:pos:3:4:3145728:10::::0::
run:pos:17:18:14155776:45::::1::
run:pos:30:31:24379392:77:2:9:12:2::
run:end
summary:exit:ok
"""

LOCATE = """SnapRAID locate report:

Locate all files

Located data in this range: 2621 k

       Offset         Span    Frags
           0      1048576        1 branch/a/z0.bin
     1048576       786432        1 branch/a/m0.bin
     1835008      1310720        2 branch/b/q0.bin
"""

FILEFRAG = """Filesystem type is: 9123683e
File size of /mnt/archive/arch0/branch/a/z0.bin is 1048576 (256 blocks of 4096 bytes)
 ext:     logical_offset:        physical_offset: length:   expected: flags:
   0:        0..     127:       3328..      3455:    128:
   1:      128..     255:       9000..      9127:    128:       3456: last,eof
/mnt/archive/arch0/branch/a/z0.bin: 2 extents found
"""

FIX_LOG = """status:recovered:arch2:branch/x2.bin
fixed:5:arch2:branch/x2.bin: Fixed data error at position 0
fixed:6:arch2:branch/x2.bin: Fixed data error at position 1
missing:7:arch2:branch/w\\d2.bin: Open error at position 0. No such file or directory.
unrecoverable:7:arch2:branch/w\\d2.bin: Unrecoverable error at position 0
unrecoverable:9:arch2:branch/w\\d2.bin: Unrecoverable unsynced error at position 2
status:unrecoverable:arch2:branch/w\\d2.bin
summary:exit:unrecoverable
"""

SNAPPER_JSON = json.dumps(
    {
        "arch0": [
            {"number": 0, "type": "single", "userdata": None},
            {"number": 1, "type": "single", "userdata": {"snapraid-btrfs": "synced"}},
            {"number": 2, "type": "single", "userdata": {"other": "x"}},
            {"number": 3, "type": "single", "userdata": {"snapraid-btrfs": "syncing", "a": "b"}},
        ]
    }
)


def test_tag_fields_selects_lines_by_prefix_and_unescapes_the_fields():
    log = "fixed:5:arch2:a\\db\\\\c: Fixed\nrun:end\nfixedx:1\n"
    assert tag_fields(log, "fixed") == [["5", "arch2", "a:b\\c", " Fixed"]]
    assert tag_fields(log, "run", "end") == [[]]


def test_summary_reads_summary_and_content_info_tags_keyed_by_all_but_the_value():
    log = (
        "summary:exit:unsynced\ncontent_info:block_unsynced:12\n"
        "summary:disk_file_count:arch0:5\nrun:end\n"
    )
    assert summary(log) == {
        "exit": "unsynced",
        "block_unsynced": "12",
        "disk_file_count:arch0": "5",
    }


def test_run_positions_are_block_position_and_processed_count_pairs():
    assert run_positions(SYNC_LOG) == [(3, 4), (17, 18), (30, 31)]
    assert run_positions("run:begin:0:1:1\n") == []


def test_step_stats_describe_the_differences_between_consecutive_values():
    assert step_stats([4, 18, 31]) == {"lines": 3, "min": 13, "median": 13.5, "max": 14}
    assert step_stats([7]) == {"lines": 1, "min": None, "median": None, "max": None}


def test_locate_lines_give_first_last_and_fragment_count_per_path():
    assert parse_locate(LOCATE) == {
        "branch/a/z0.bin": (0, 3, 1),
        "branch/a/m0.bin": (4, 6, 1),
        "branch/b/q0.bin": (7, 11, 2),
    }
    assert parse_locate("SnapRAID locate report:\n\nNo files located.\n") == {}


def test_blocks_round_a_file_size_up_to_whole_blocks():
    assert [blocks(s) for s in (0, 1, BLOCK, BLOCK + 1, 3 * BLOCK)] == [0, 1, 1, 2, 3]


def test_extent_is_what_locate_reports_for_a_position_list():
    assert extent([4, 5, 6]) == (4, 6, 1)
    assert extent([0, 1, 7, 8, 9]) == (0, 9, 2)
    assert extent([]) is None


def test_first_fit_takes_the_lowest_free_positions_in_ascending_order():
    assert first_fit(set(), 3) == [0, 1, 2]
    assert first_fit({0, 1, 4}, 4) == [2, 3, 5, 6]


def test_allocate_keeps_kept_files_and_places_added_files_first_fit_in_the_given_order():
    kept = {"m": [4, 5, 6], "q": [7, 8]}
    got = allocate(kept, [("c", 2), ("f", 3)])
    assert got == {"m": [4, 5, 6], "q": [7, 8], "c": [0, 1], "f": [2, 3, 9]}


def test_first_physical_reads_the_physical_start_of_the_first_extent():
    assert first_physical(FILEFRAG) == 3328
    empty = "File size of x is 0 (0 blocks of 4096 bytes)\nx: 0 extents found\n"
    assert first_physical(empty) is None


def test_fix_report_collects_status_and_the_parity_positions_of_fixed_and_lost_blocks():
    report = fix_report(FIX_LOG)
    assert report["status"] == {
        "arch2:branch/x2.bin": "recovered",
        "arch2:branch/w:2.bin": "unrecoverable",
    }
    assert report["fixed"] == {"arch2:branch/x2.bin": [5, 6]}
    assert report["unrecoverable"] == {"arch2:branch/w:2.bin": [7, 9]}
    assert report["exit"] == "unrecoverable"


def test_snapper_userdata_lists_snapshot_numbers_with_the_value_of_one_key():
    assert snapper_userdata(SNAPPER_JSON, "snapraid-btrfs") == [
        (0, None),
        (1, "synced"),
        (2, None),
        (3, "syncing"),
    ]


def test_ranges_compress_sorted_positions():
    assert ranges([1, 2, 3, 7, 9, 10]) == [[1, 3], [7, 7], [9, 10]]
    assert ranges([]) == []


def test_parity_states_label_each_position_by_the_maps_it_matches():
    got = parity_states(
        {
            "new": {"1": True, "2": True, "3": False, "4": False},
            "old": {"1": True, "2": False, "3": True, "4": False},
        }
    )
    assert got == {"both": [1], "new": [2], "old": [3], "neither": [4]}


def run_script(script, *args):
    done = subprocess.run(
        [sys.executable, "-c", script, *map(str, args)], capture_output=True, text=True, check=True
    )
    return done.stdout


def test_the_fill_script_writes_seeded_files_in_order_with_their_mtimes(tmp_path):
    spec = [
        [str(tmp_path / "d" / "b.bin"), "key-b", BLOCK + 5, 1_700_000_100_123_456_789],
        [str(tmp_path / "a.bin"), "key-a", 10, 1_700_000_200_000_000_001],
    ]
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    run_script(FILL, "seed", tmp_path / "spec.json")
    b = tmp_path / "d" / "b.bin"
    assert b.read_bytes() == b"".join(content_chunks("seed", Entry("key-b", "x", size=BLOCK + 5)))
    assert b.stat().st_mtime_ns == 1_700_000_100_123_456_789
    import hashlib

    digest = hashlib.sha256((tmp_path / "a.bin").read_bytes()).hexdigest()
    assert digest == content_sha256("seed", Entry("key-a", "x", size=10))


def test_the_xor_script_compares_each_parity_block_with_the_xor_of_its_data_blocks(tmp_path):
    size = 8
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.write_bytes(bytes(range(1, 17)))  # two blocks
    two.write_bytes(bytes([0xF0] * 5))  # one short block, zero padded
    first = bytes(a ^ b for a, b in zip(range(1, 9), [0xF0] * 5 + [0] * 3, strict=True))
    second = bytes(range(9, 17))
    parity = tmp_path / "parity"
    parity.write_bytes(first + second + bytes(size) + b"\x01" * size)
    spec = {
        "block_size": size,
        "parity": str(parity),
        "maps": {
            "new": {
                "0": [[str(one), 0], [str(two), 0]],
                "1": [[str(one), 1]],
                "2": [],
                "3": [],
                "4": [],
            },
            "old": {"0": [[str(one), 0]], "1": [[str(one), 1]]},
        },
    }
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    got = json.loads(run_script(XOR, tmp_path / "spec.json"))
    assert got == {
        "new": {"0": True, "1": True, "2": True, "3": False, "4": True},
        "old": {"0": False, "1": True},
    }
