"""Checks S2.2 and S2.9 (vm-tests/s2/checks_history.py, task T068): which commands they send in
which order, and what they judge, against the scripted history and `server` of
tests/test_s2_checks_baseline.py. The predictions are those of the real draft criteria
docs/phase0/criteria/S2.yaml. What the recovery does to the disks is measured in the VM
(probe-s2-history-checks)."""

import pytest
import test_s2_checks_baseline as bl
from s2 import checks_history

machine, lab, qemu_img = bl.machine, bl.lab, bl.qemu_img
CRITERIA, ROWS = bl.CRITERIA, bl.CRITERIA_ROWS
LOST = ["a/a0.bin", "a/a1.bin", "a/a2.bin", "a/a3.bin"]
FIX_LOG = "log=$(ls -t /var/log/recover/fix-arch0-*.log 2>/dev/null | head -1) || log=;"
HISTORY = [
    "history modify b/b1.bin",
    "history modify b/b2.bin",
    "history delete c/c2.bin",
    "history modify a/a4.bin",
    "history add new/n0.bin",
    "history add new/n1.bin",
]


def listing(files):
    names = "".join(f"  branch/{f}\n" for f in files)
    return (
        "recover.sh: unrecoverable files of arch0 (renamed *.unrecoverable by the fix):\n"
        f"{names}recover.sh: restore them from elsewhere, or retry the fix now\n"
    )


def tags(recovered, lost):
    return "".join(
        [f"status:recovered:arch0:branch/{f}\n" for f in recovered]
        + [f"status:unrecoverable:arch0:branch/{f}\n" for f in lost]
    )


# --- S2.2 ----------------------------------------------------------------------------------------


@pytest.fixture
def s22(lab, machine):
    lab.history.observed["arch0"] = bl.exact(ROWS["S2.2"])
    machine.script["recover recover"] = (3, listing(LOST))
    machine.script[FIX_LOG] = (0, tags(["a/a4.bin", "a/a5.bin"], LOST))
    return lab


def test_s22_changes_the_array_after_a_plain_sync_then_runs_the_plain_sequence(s22, machine):
    checks_history.check_history_plain(s22)

    assert bl.shell(machine) == [
        "history reset",
        "history populate 16",
        "history sync snapshots=False",
        *HISTORY,
        "history fail arch0 device-ioerror",
        "history replace arch0 same",
        "shutdown",
        "start",
        "unit multi-user.target",
        "settle",
        "$ recover recover arch0 --plain 2>&1",
        "$ recover verify arch0 --accept-unrecoverable 2>&1",
        "$ recover resync arch0 --plain --accept-unrecoverable 2>&1",
        "$ recover unlock arch0 2>&1",
        "$ log=$(ls -t /var/log/recover/fix-arch0-*.log 2>/dev/null | head -1) || log=; "
        'if [ -n "$log" ]; then cat "$log" 2>&1; fi',
    ]


def test_s22_records_the_listing_the_fix_tags_and_the_recovered_set(s22):
    checks_history.check_history_plain(s22)

    seen = bl.evidence(s22, "S2.2-history-plain")
    assert seen["mismatches"] == [] and seen["failures"] == []
    assert seen["listed"] == LOST and seen["listed_extra"] == []
    assert seen["status_tags"]["a/a4.bin"] == "recovered"
    assert seen["status_vs_prediction"] == []
    assert s22.required_settings["workflow_value"] == {"plain_recovered": ["a/a4.bin", "a/a5.bin"]}


def test_s22_a_precondition_that_is_not_met_is_a_harness_error_and_no_snapsync_runs(s22, machine):
    s22.history.sync_result = (1, "boom", "/log")

    with pytest.raises(RuntimeError, match="the first sync exited 1"):
        checks_history.check_history_plain(s22)
    assert not any("snapsync" in e for e in machine.events)


def test_s22_recover_must_list_the_lost_files_with_exit_3(s22, machine):
    machine.script["recover recover"] = (0, "recover.sh: every file of arch0 recovered\n")

    with pytest.raises(AssertionError, match="recover recover arch0 exited 0, .*exit 3"):
        checks_history.check_history_plain(s22)


def test_s22_a_lost_file_that_the_recover_step_does_not_list_fails(s22, machine):
    machine.script["recover recover"] = (3, listing(["a/a0.bin", "a/a1.bin", "a/a3.bin"]))

    with pytest.raises(AssertionError, match="recover did not list a/a2.bin"):
        checks_history.check_history_plain(s22)


def test_s22_a_listed_file_beyond_the_prediction_is_recorded_not_judged(s22, machine):
    machine.script["recover recover"] = (3, listing([*LOST, "a/a5.bin"]))

    checks_history.check_history_plain(s22)

    assert bl.evidence(s22, "S2.2-history-plain")["listed_extra"] == ["a/a5.bin"]


@pytest.mark.parametrize(
    ("row", "state", "message"),
    [
        (("a/a1.bin", "synced"), "bit_exact", "predicted unrecoverable, observed bit_exact"),
        (("a/a5.bin", "synced"), "missing", "predicted recoverable, observed missing"),
        (("a/a4.bin", "unsynced"), "bit_exact", "predicted unrecoverable, observed bit_exact"),
    ],
)
def test_s22_the_recovered_set_must_equal_the_prediction(s22, row, state, message):
    s22.history.observed["arch0"][row] = state

    with pytest.raises(AssertionError, match=message):
        checks_history.check_history_plain(s22)


def test_s22_the_surviving_disks_keep_the_content_of_the_history(s22):
    s22.history.observed["arch1"] = {
        ("b/b0.bin", "synced"): "bit_exact",
        ("b/b1.bin", "synced"): "differs",
        ("b/b1.bin", "unsynced"): "bit_exact",
        ("new/n0.bin", "unsynced"): "bit_exact",
    }
    s22.history.changed["arch1"] = {"branch/b/b1.bin"}

    checks_history.check_history_plain(s22)


def test_s22_a_survivor_that_lost_the_changed_content_fails(s22):
    s22.history.observed["arch1"] = {("b/b1.bin", "unsynced"): "differs"}

    with pytest.raises(AssertionError, match="arch1 changed: differs b/b1.bin"):
        checks_history.check_history_plain(s22)


def test_s22_the_fix_tags_that_differ_from_the_prediction_are_recorded(s22, machine):
    machine.script[FIX_LOG] = (0, tags(["a/a5.bin"], [*LOST, "a/a4.bin"]))

    checks_history.check_history_plain(s22)

    assert bl.evidence(s22, "S2.2-history-plain")["status_vs_prediction"] == [
        "a/a4.bin: predicted recovered, the fix log says unrecoverable"
    ]


# --- S2.9 ----------------------------------------------------------------------------------------


@pytest.fixture
def s29(lab):
    lab.history.observed["arch0"] = bl.exact(ROWS["S2.9"])
    return lab


def test_s29_changes_the_array_after_a_snapshot_sync_then_runs_the_sequence(s29, machine):
    checks_history.check_history_snapshot(s29)

    assert bl.shell(machine) == [
        "history reset",
        "history populate 16",
        "history sync snapshots=True",
        "$ snapsync complete 2>&1",
        *HISTORY,
        "history fail arch0 device-ioerror",
        "history replace arch0 same",
        "shutdown",
        "start",
        "unit multi-user.target",
        "settle",
        "$ recover recover arch0 2>&1",
        "$ recover verify arch0 2>&1",
        "$ recover resync arch0 2>&1",
        "$ recover unlock arch0 2>&1",
    ]


def test_s29_records_what_the_workflow_recovered_that_the_plain_variant_did_not(s29):
    s29.required_settings["workflow_value"] = {"plain_recovered": ["a/a4.bin", "a/a5.bin"]}

    checks_history.check_history_snapshot(s29)

    assert s29.required_settings["workflow_value"] == {
        "plain_recovered": ["a/a4.bin", "a/a5.bin"],
        "snapshot_recovered": [f"a/a{i}.bin" for i in range(6)],
        "recovered_by_snapshot_only": LOST,
    }
    assert (
        bl.evidence(s29, "S2.9-history-snapshot")["workflow_value"]
        == (s29.required_settings["workflow_value"])
    )


def test_s29_without_a_measurement_of_s22_it_records_only_its_own(s29):
    checks_history.check_history_snapshot(s29)

    assert s29.required_settings["workflow_value"] == {
        "snapshot_recovered": [f"a/a{i}.bin" for i in range(6)],
        "recovered_by_snapshot_only": None,
        "note": "S2.2 did not run; nothing to compare with",
    }


def test_s29_every_step_exits_0_a_listing_recover_fails(s29, machine):
    machine.script["recover recover"] = (3, listing(["a/a0.bin"]))

    with pytest.raises(AssertionError, match="recover recover arch0 exited 3"):
        checks_history.check_history_snapshot(s29)


def test_s29_a_synced_file_that_does_not_come_back_fails(s29):
    s29.history.observed["arch0"][("a/a0.bin", "synced")] = "differs"

    with pytest.raises(AssertionError, match=r"a/a0.bin \(synced\): predicted recoverable"):
        checks_history.check_history_snapshot(s29)


def test_s29_the_unsynced_content_of_a4_must_be_gone(s29):
    s29.history.observed["arch0"][("a/a4.bin", "unsynced")] = "bit_exact"

    with pytest.raises(AssertionError, match=r"a/a4.bin \(unsynced\): predicted unrecoverable"):
        checks_history.check_history_snapshot(s29)


def test_the_checks_are_named_by_the_criteria():
    names = {s["id"]: s["check"] for s in CRITERIA["scenarios"]}

    assert checks_history.CHECKS == {
        names["S2.2"]: checks_history.check_history_plain,
        names["S2.9"]: checks_history.check_history_snapshot,
    }
