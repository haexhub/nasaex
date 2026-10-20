"""The SnapRAID block-allocation and recoverability model (testlib/parity_model.py, tasks T058 and
T059): allocation per disk and sync, and the per-position predictions of spec User Story 2
("Recoverability predictions" table, Clarifications 2026-10-20, S2 SnapRAID probe).

The fixtures are the cases measured by probe T057 (vm-tests/probes/snapraid.results.md, SnapRAID
14.4, runs 1 and 5 to 7): physical starts in 4 KiB filesystem blocks from `filefrag -e`, positions
from `snapraid locate` and the parity XOR, outcomes from the `status:` tags of `snapraid -d arch2
fix`. Where the spec rule and the measured outcome differ, the test asserts the spec rule and
says what was measured."""

import pytest

from testlib.parity_model import (
    NewFile,
    after_interrupted_sync,
    after_sync,
    allocate,
)

BLOCK = 256 * 1024  # SnapRAID's default block size


def span(first: int, last: int) -> tuple[int, ...]:
    return tuple(range(first, last + 1))


def outcome(prediction) -> dict[tuple[str, str], tuple[bool, str, tuple[int, ...]]]:
    """(path, version) -> (recoverable, rule, positions) of every file of the failed disks."""
    return {(f.path, f.version): (f.recoverable, f.rule, f.positions) for f in prediction.files}


def recovered(prediction, version: str = "synced") -> set[str]:
    return {f.path for f in prediction.files if f.version == version and f.recoverable}


# --- allocation (results item 4a, phase alloc, runs 1, 6, 7) ------------------------------------

# Sync 1, per disk in write order: (path, size, physical start). Write order is not the
# alphabetical order; on fresh disks it is also the physical order.
ALLOC_SYNC1 = {
    "arch0": [
        NewFile("z0", 4 * BLOCK, 3328),
        NewFile("m0", 2 * BLOCK + BLOCK // 2, 3584),
        NewFile("c0", BLOCK, 3744),
        NewFile("q0", 3 * BLOCK, 3808),
    ],
    "arch1": [
        NewFile("y1", 6 * BLOCK, 3328),
        NewFile("d1", 2 * BLOCK, 3712),
        NewFile("k1", 5 * BLOCK, 3840),
    ],
    "arch2": [
        NewFile("x2", 3 * BLOCK, 3328),
        NewFile("e2", 4 * BLOCK, 3520),
        NewFile("n2", 2 * BLOCK, 3776),
        NewFile("t2", BLOCK + BLOCK // 4, 3904),
    ],
}
# `locate` after sync 1.
LOCATED_SYNC1 = {
    "arch0": {"z0": span(0, 3), "m0": span(4, 6), "c0": (7,), "q0": span(8, 10)},
    "arch1": {"y1": span(0, 5), "d1": (6, 7), "k1": span(8, 12)},
    "arch2": {"x2": span(0, 2), "e2": span(3, 6), "n2": (7, 8), "t2": (9, 10)},
}
# `locate` and the parity XOR after sync 2 (arch0: z0 deleted, c0 rewritten, f0 added; arch1:
# d1 deleted, g1 added; arch2 unchanged). f0 is split over three holes; c0 moved from 7 to 0.
LOCATED_SYNC2 = {
    "arch0": {
        "c0": (0,),
        "f0": (1, 2, 3, 7, 11, 12),
        "m0": span(4, 6),
        "q0": span(8, 10),
    },
    "arch1": {"g1": (6,), "k1": span(8, 12), "y1": span(0, 5)},
    "arch2": LOCATED_SYNC1["arch2"],
}


def test_allocation_of_a_first_sync_matches_locate():
    """First fit from position 0 in physical order; a short last block takes a whole position
    (m0 2.5 blocks, t2 1.25 blocks)."""
    for disk, added in ALLOC_SYNC1.items():
        assert allocate({}, (), added, BLOCK) == LOCATED_SYNC1[disk], disk


def test_allocation_orders_by_physical_start_not_by_input_order():
    """The probe's alphabetical-order control mismatched 11 of 11 files; the model must sort by
    the physical start whatever order the caller lists the files in."""
    for disk, added in ALLOC_SYNC1.items():
        assert allocate({}, (), sorted(added, key=lambda f: f.path), BLOCK) == LOCATED_SYNC1[disk]


def test_allocation_removes_first_then_fills_holes_and_splits_files():
    """Sync 2 on arch0: the deleted z0 (0-3) and the rewritten c0 (7) free their positions
    before the insertions; c0 (physical 4000) takes 0, f0 (4064) takes 1-3, 7 and 11-12."""
    arch0 = allocate(
        LOCATED_SYNC1["arch0"],
        {"z0", "c0"},
        [NewFile("f0", 6 * BLOCK, 4064), NewFile("c0", BLOCK, 4000)],
        BLOCK,
    )
    assert arch0 == LOCATED_SYNC2["arch0"]
    arch1 = allocate(LOCATED_SYNC1["arch1"], {"d1"}, [NewFile("g1", BLOCK, 4160)], BLOCK)
    assert arch1 == LOCATED_SYNC2["arch1"]


def test_allocation_of_the_killed_sync_follows_physical_not_write_order():
    """Phase kill (runs 4 to 7): n2 was written after n1 but got the lower physical start (the
    extents of the deleted h), so n2 took 160-319 and n1 640-759. Only the order of the two
    physical starts was recorded; the numbers are stand-ins for that order."""
    arch2 = allocate(
        {"a": span(0, 159), "h": span(160, 319), "c": span(320, 479), "d": span(480, 639)},
        {"h"},
        [NewFile("n1", 120 * BLOCK, 90_000), NewFile("n2", 160 * BLOCK, 50_000)],
        BLOCK,
    )
    assert arch2 == KILL_SCANNED["arch2"]


def test_an_empty_file_takes_no_position():
    assert allocate({}, (), [NewFile("empty", 0, 10), NewFile("one", 1, 20)], BLOCK) == {
        "empty": (),
        "one": (0,),
    }


def test_a_rewritten_file_must_be_removed_before_it_is_added_again():
    with pytest.raises(ValueError, match="c0"):
        allocate(LOCATED_SYNC1["arch0"], (), [NewFile("c0", BLOCK, 4000)], BLOCK)


# --- after a completed sync: S2.1, S2.2, S2.3, S2.8, S2.9 --------------------------------------

# The S2.2 history after sync 2 (results "The S2.2 history", runs 1, 6, 7): arch0 f0 changed in
# place, arch1 y1 deleted, arch2 t2 rewritten, arch2 w2 written; a file added on arch0 (not an
# input: additions elsewhere do not reduce recoverability). Then arch2 fails.
S22_CHANGED = {"arch0": {"f0"}, "arch1": {"y1"}, "arch2": {"t2"}}
S22_UNSYNCED = {"arch2": {"t2", "w2"}}


def test_s2_1_every_file_of_the_failed_disk_is_recoverable_after_a_complete_sync():
    prediction = after_sync(LOCATED_SYNC2, ["arch2"])
    assert recovered(prediction) == {"x2", "e2", "n2", "t2"}
    assert {f.rule for f in prediction.files} == {"intact"}
    assert prediction.beyond_parity == ()
    assert (prediction.variant, prediction.fault, prediction.provisional) == ("plain", None, False)


def test_s2_2_changes_on_surviving_disks_block_the_shared_positions():
    """Changed positions of the surviving disks 0-5, 7, 11-12: x2, e2 and n2 are lost; the
    blocks fix restored anyway (`fixed:` e2 6, n2 8) are exactly the unblocked ones."""
    result = outcome(after_sync(LOCATED_SYNC2, ["arch2"], S22_CHANGED, S22_UNSYNCED))
    assert result[("x2", "synced")] == (False, "blocked", (0, 1, 2))
    assert result[("e2", "synced")] == (False, "blocked", (3, 4, 5))
    assert result[("n2", "synced")] == (False, "blocked", (7,))
    measured_fixed = {"e2": {6}, "n2": {8}}
    for path, fixed in measured_fixed.items():
        assert set(LOCATED_SYNC2["arch2"][path]) - set(result[(path, "synced")][2]) == fixed


def test_s2_2_a_file_changed_on_the_failed_disk_is_not_recoverable():
    """Spec S2.2: recoverable only if "the file itself is unchanged"; files "last changed on the
    failed disk after that sync are not recoverable". Measured (runs 1, 6, 7): fix restored t2
    bit-exact in its synced version (its positions 9-10 are not blocked), so the probe counted
    plain 1 of 4; the spec rule counts 0 of 4. Reported as a conflict for T066."""
    prediction = after_sync(LOCATED_SYNC2, ["arch2"], S22_CHANGED, S22_UNSYNCED)
    result = outcome(prediction)
    assert result[("t2", "synced")] == (False, "changed", ())
    assert recovered(prediction) == set()
    unblocked = {f.path for f in prediction.files if f.version == "synced" and f.rule != "blocked"}
    assert unblocked == {"t2"}  # what fix restored, measured
    assert prediction.beyond_parity == (0, 1, 2, 3, 4, 5, 7)


def test_s2_3_files_written_to_the_failed_disk_after_the_sync_are_lost():
    """Content written or changed on the failed disk after the sync is never recoverable;
    the files covered by the sync keep the S2.1 prediction."""
    prediction = after_sync(LOCATED_SYNC2, ["arch2"], unsynced={"arch2": {"w2"}})
    result = outcome(prediction)
    assert result[("w2", "unsynced")] == (False, "unsynced", ())
    assert recovered(prediction) == {"x2", "e2", "n2", "t2"}
    assert recovered(prediction, "unsynced") == set()


def test_s2_9_snapshot_variant_recovers_the_synced_state_whatever_changed_elsewhere():
    """Measured (snap phase, runs 5 to 7): snapraid-btrfs fix restored 4 of 4 arch2 files as of
    the sync; the later rewrite of t2 and the new w2 are gone."""
    prediction = after_sync(LOCATED_SYNC2, ["arch2"], S22_CHANGED, S22_UNSYNCED, snapshot=True)
    assert prediction.variant == "snapshot"
    assert recovered(prediction) == {"x2", "e2", "n2", "t2"}
    assert {(f.path, f.rule) for f in prediction.files if f.version == "unsynced"} == {
        ("t2", "unsynced"),
        ("w2", "unsynced"),
    }
    assert prediction.beyond_parity == ()


def test_s2_9_the_value_of_the_snapshot_workflow_is_the_difference_to_s2_2():
    """Results: the probe counted plain 1 of 4, snapshot 4 of 4 (3 files). By the spec rows the
    plain variant recovers none of the four synced versions, so the model's difference is 4."""
    plain = after_sync(LOCATED_SYNC2, ["arch2"], S22_CHANGED, S22_UNSYNCED)
    snapshot = after_sync(LOCATED_SYNC2, ["arch2"], S22_CHANGED, S22_UNSYNCED, snapshot=True)
    assert recovered(snapshot) - recovered(plain) == {"x2", "e2", "n2", "t2"}


def test_s2_8_two_failed_disks_with_one_parity_never_claim_unreconstructable_files():
    """Not measured. Layout of sync 1: arch1 and arch2 share every arch2 position, so every
    file of both disks is lost and the parity limit names those positions."""
    prediction = after_sync(LOCATED_SYNC1, ["arch1", "arch2"])
    assert recovered(prediction) == set()
    assert {f.disk for f in prediction.files} == {"arch1", "arch2"}
    assert outcome(prediction)[("k1", "synced")] == (False, "blocked", (8, 9, 10))
    assert prediction.beyond_parity == span(0, 10)


def test_s2_8_a_position_held_by_one_failed_disk_only_is_rebuilt():
    """Per position, one parity level rebuilds one missing block: arch2 c (320-479) shares no
    position with arch0 (p0 0-159) at the last completed sync of the kill phase."""
    prediction = after_sync(KILL_SYNCED, ["arch0", "arch2"])
    assert recovered(prediction) == {"c", "d", "h"}
    assert prediction.beyond_parity == span(0, 159)


def test_an_unknown_failed_disk_is_an_error():
    with pytest.raises(ValueError, match="arch9"):
        after_sync(LOCATED_SYNC2, ["arch9"])
    with pytest.raises(ValueError, match="arch9"):
        after_interrupted_sync(KILL_SYNCED, KILL_SCANNED, "arch9", 418, {}, {})


# --- after an interrupted sync: S2.4 (results item 4c) ------------------------------------------

# Last completed sync of the kill phase, then before the killed sync: arch1 q1 and arch2 h
# deleted; arch0 f, arch2 n1 and n2 added. The killed sync recorded KILL_SCANNED (`locate` after
# the kill) and processed 160 up to the boundary taken from the parity file: 403 (run 5), 419
# (run 6), 418 (run 7).
KILL_SYNCED = {
    "arch0": {"p0": span(0, 159)},
    "arch1": {"q0": span(0, 479), "q1": span(480, 639)},
    "arch2": {"a": span(0, 159), "h": span(160, 319), "c": span(320, 479), "d": span(480, 639)},
}
KILL_SCANNED = {
    "arch0": {"p0": span(0, 159), "f": span(160, 639)},
    "arch1": {"q0": span(0, 479)},
    "arch2": {
        "a": span(0, 159),
        "n2": span(160, 319),
        "c": span(320, 479),
        "d": span(480, 639),
        "n1": span(640, 759),
    },
}
KILL_CHANGED = {"arch1": {"q1"}, "arch2": {"h"}}
KILL_UNSYNCED = {"arch2": {"n1", "n2"}}


def killed(boundary=418, **kwargs):
    return after_interrupted_sync(
        KILL_SYNCED, KILL_SCANNED, "arch2", boundary, KILL_CHANGED, KILL_UNSYNCED, **kwargs
    )


@pytest.mark.parametrize("boundary", [403, 419, 418])
def test_s2_4_process_kill_matches_the_measured_fix(boundary):
    """a and c (which straddles the boundary, with arch0 f added over empty positions) are
    recovered; d is lost at its unprocessed positions where arch1 deleted q1; n1 and n2, added by
    the interrupted sync, are lost processed (n2) or not (n1). The unrecoverable positions are
    those of the `unrecoverable:` tags."""
    prediction = killed(boundary)
    result = outcome(prediction)
    assert recovered(prediction) == {"a", "c"}
    assert result[("d", "synced")] == (False, "blocked", span(480, 639))
    assert result[("n2", "unsynced")] == (False, "unsynced", span(160, 319))
    assert result[("n1", "unsynced")] == (False, "unsynced", span(640, 759))
    assert result[("h", "synced")] == (False, "changed", ())
    assert prediction.beyond_parity == span(480, 639)
    assert (prediction.fault, prediction.provisional) == ("process-kill", False)


def test_s2_4_the_boundary_is_the_first_unprocessed_position():
    """Run 7: parity new at 160-417, old from 418, so the boundary is 418."""
    assert outcome(killed(480))[("d", "synced")] == (False, "blocked", span(480, 639))
    assert outcome(killed(481))[("d", "synced")] == (False, "blocked", span(481, 639))


def test_s2_4_a_deletion_at_processed_positions_does_not_block():
    """The rule: a position counts when it "was processed by the interrupted sync with the
    surviving block present". The model reads a block the interrupted sync recorded as deleted
    (q1) as no surviving block, so a processed position is rebuilt from the updated parity (a
    deleted block counts as zero there, cmdline/check.c:1017-1033). Not measured."""
    assert recovered(killed(640)) == {"a", "c", "d"}


def test_s2_4_a_surviving_change_after_the_kill_blocks_processed_positions():
    """A surviving block changed after the kill is no longer the block the processed parity
    holds, and at an unprocessed position it is a change since the last completed sync."""
    for boundary in (418, 100):
        result = outcome(killed(boundary, changed_after={"arch0": {"p0"}}))
        assert result[("a", "synced")] == (False, "blocked", span(0, 159)), boundary


def test_s2_4_snapshot_variant_reads_surviving_disks_from_the_interrupted_set():
    """Measured killed snapshot-supported sync (snap phase, runs 5 to 7): arch0 c0 (position 0,
    shared with arch2 x2) was rewritten after the kill; fix read arch0 and arch1 from the
    `syncing` snapshot and restored x2, e2, n2 and t2; n3 (added, processed) was lost. The
    boundary is not measured (last `run:pos` 148, processed past it); any boundary above 90
    gives the same outcome."""
    synced = {
        "arch0": {
            "c0": (0,),
            "f0": (1, 2, 3, 7, 11, 12),
            "m0": span(4, 6),
            "q0": span(8, 10),
            "added-after-sync": (13, 14),
        },
        "arch1": {"g1": (6,), "k1": span(8, 12)},
        "arch2": LOCATED_SYNC1["arch2"],
    }
    scanned = {
        "arch0": {**synced["arch0"], "f2": span(15, 254)},
        "arch1": {**synced["arch1"], "g2": (*span(0, 5), 7, *span(13, 91))},
        "arch2": {**synced["arch2"], "n3": span(11, 90)},
    }
    args = (synced, scanned, "arch2", 149, {}, {"arch2": {"n3"}})
    after = {"arch0": {"c0"}}
    snapshot = after_interrupted_sync(*args, changed_after=after, snapshot=True)
    assert snapshot.variant == "snapshot"
    assert recovered(snapshot) == {"x2", "e2", "n2", "t2"}
    assert outcome(snapshot)[("n3", "unsynced")] == (False, "unsynced", span(11, 90))
    plain = after_interrupted_sync(*args, changed_after=after)
    assert outcome(plain)[("x2", "synced")] == (False, "blocked", (0,))


def test_s2_4_snapshot_variant_still_counts_changes_before_the_interrupted_sync():
    """The interrupted set holds the surviving disks as that sync scanned them, without q1, so
    d stays lost at its unprocessed positions in the snapshot variant too."""
    prediction = killed(snapshot=True)
    assert recovered(prediction) == {"a", "c"}


def test_s2_4_a_file_of_the_failed_disk_changed_after_the_kill_is_not_unchanged():
    result = outcome(killed(changed_after={"arch2": {"a"}}, snapshot=True))
    assert result[("a", "synced")] == (False, "changed", ())


def test_s2_4_device_failure_has_its_own_provisional_prediction():
    """Spec: the device-failure variant uses the same block rule provisionally; whether its
    parity and block state equal the process-kill case is UNVERIFIED, so the prediction is
    marked and never taken as proven by the process-kill evidence."""
    device = killed(fault="device-failure")
    kill = killed()
    assert (device.fault, device.provisional) == ("device-failure", True)
    assert device.files == kill.files
    with pytest.raises(ValueError, match="power-cut"):
        killed(fault="power-cut")
