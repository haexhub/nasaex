"""spikectl verdict: derivation from scenario outcomes, current verdict per spike,
voiding by a new frozen version, gate matrix with X1 on a separate line."""

import json

import pytest
from helpers import make_root, record_doc, write_criteria, write_record

from spikectl.freeze import freeze
from spikectl.model import SPIKES
from spikectl.verdict import current_verdicts, derive_verdict, format_matrix, gate_matrix

CRITERIA_IDS = ("S1.1", "S1.2")


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def frozen(root, spike, version=1):
    write_criteria(root, spike=spike, version=version)
    return freeze(root, spike)


def put_record(root, entry, spike, outcomes, stamp="20261022T101500Z", **changes):
    doc = record_doc(spike, entry.version, entry.sha256, outcomes, stamp)
    doc.update(changes)
    return write_record(root, doc)


def criteria(spike="S1"):
    return {"scenarios": [{"id": f"{spike}.1"}, {"id": f"{spike}.2"}]}


# --- derive_verdict --------------------------------------------------------


def test_pass_only_if_every_scenario_passes():
    record = record_doc(outcomes={"S1.1": "pass", "S1.2": "pass"})

    assert derive_verdict(criteria(), record) == "PASS"


@pytest.mark.parametrize("outcome", ["fail", "error", "skipped", "flaky"])
def test_any_outcome_other_than_pass_fails(outcome):
    record = record_doc(outcomes={"S1.1": "pass", "S1.2": outcome})

    assert derive_verdict(criteria(), record) == "FAIL"


def test_a_missing_scenario_counts_as_failed():
    record = record_doc(outcomes={"S1.1": "pass"})

    assert derive_verdict(criteria(), record) == "FAIL"


def test_a_scenario_with_a_non_passing_case_fails_even_if_marked_pass():
    record = record_doc(outcomes={"S1.1": "pass", "S1.2": "pass"})
    record["scenarios"][1]["cases"] = [
        {"id": "S1.2:a", "outcome": "pass"},
        {"id": "S1.2:b", "outcome": "fail"},
    ]

    assert derive_verdict(criteria(), record) == "FAIL"


# --- current verdict per spike ---------------------------------------------


def test_spike_without_a_record_has_verdict_none(root):
    frozen(root, "S1")

    verdicts = current_verdicts(root)

    assert verdicts["S1"].verdict == "none"
    assert set(verdicts) == set(SPIKES)


def test_spike_without_a_lock_line_has_verdict_none(root):
    write_criteria(root, spike="S1")  # draft only

    assert current_verdicts(root)["S1"].verdict == "none"


def test_pass_record_gives_pass(root):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "pass"})

    verdict = current_verdicts(root)["S1"]

    assert (verdict.verdict, verdict.criteria_version) == ("PASS", 1)
    assert verdict.errors == []


def test_the_newest_record_naming_the_current_version_decides(root):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "pass"}, stamp="20261022T101500Z")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "fail"}, stamp="20261023T090000Z")

    verdict = current_verdicts(root)["S1"]

    assert verdict.verdict == "FAIL"
    assert verdict.record_id == "S1-20261023T090000Z-v1"


def test_a_new_frozen_version_voids_older_verdicts(root):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "pass"})
    frozen(root, "S1", version=2)

    verdict = current_verdicts(root)["S1"]

    assert (verdict.verdict, verdict.criteria_version) == ("none", 2)


def test_a_draft_amendment_does_not_void_the_frozen_verdict(root):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "pass"})
    write_criteria(root, spike="S1", version=2)  # drafted, not frozen

    assert current_verdicts(root)["S1"].verdict == "PASS"


def test_repro_records_do_not_provide_the_verdict(root):
    entry = frozen(root, "S1")
    doc = record_doc("S1", 1, entry.sha256)
    doc.update(kind="repro", record_id="repro-S1-20261024T000000Z-v1", repro_runs=["a", "b", "c"])
    path = root / "docs/phase0/results/repro" / doc["record_id"] / "record.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert current_verdicts(root)["S1"].verdict == "none"


def test_a_fail_record_without_fallback_decision_is_an_error(root):
    entry = frozen(root, "S1")
    path = put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "fail"})
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["fallback_decision"]
    path.write_text(json.dumps(doc), encoding="utf-8")

    verdict = current_verdicts(root)["S1"]

    assert verdict.verdict == "FAIL"
    assert any("fallback_decision" in e for e in verdict.errors)


def test_a_typed_verdict_that_differs_from_the_derived_one_is_an_error(root):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "fail"}, verdict="PASS")

    verdict = current_verdicts(root)["S1"]

    assert verdict.verdict == "FAIL"
    assert any("verdict" in e for e in verdict.errors)


# --- gate matrix and X1 ----------------------------------------------------


def all_pass(root):
    for spike in ("S1", "S2", "S3", "S4", "S5"):
        entry = frozen(root, spike)
        put_record(root, entry, spike, {f"{spike}.1": "pass", f"{spike}.2": "pass"})


def test_adr_eligibility_follows_the_gating_spikes(root):
    all_pass(root)

    assert gate_matrix(current_verdicts(root)) == {"ADR-0002": True, "ADR-0005": True}


def test_one_failing_spike_blocks_only_its_adr(root):
    all_pass(root)
    entry = frozen(root, "X1")  # irrelevant to the gate
    put_record(root, entry, "X1", {"X1.1": "fail", "X1.2": "fail"})
    s3 = current_verdicts(root)["S3"]
    assert s3.verdict == "PASS"
    # replace S3 by a newer failing record of the same version
    sha = s3.criteria_sha256
    write_record(
        root,
        record_doc("S3", 1, sha, {"S3.1": "pass", "S3.2": "error"}, stamp="20261101T000000Z"),
    )

    matrix = gate_matrix(current_verdicts(root))

    assert matrix == {"ADR-0002": False, "ADR-0005": True}


def test_x1_never_changes_the_gate_and_is_listed_as_non_gating(root):
    all_pass(root)
    entry = frozen(root, "X1")
    put_record(root, entry, "X1", {"X1.1": "fail", "X1.2": "fail"})

    verdicts = current_verdicts(root)
    text = format_matrix(verdicts)

    assert verdicts["X1"].verdict == "FAIL"
    assert verdicts["X1"].gating is False
    assert gate_matrix(verdicts) == {"ADR-0002": True, "ADR-0005": True}
    assert "ADR-0002 eligible: yes" in text
    assert "X1 FAIL (optional, non-gating)" in text


def test_x1_without_a_record_is_reported_as_not_run(root):
    all_pass(root)

    text = format_matrix(current_verdicts(root))

    assert "X1 not run (optional, non-gating)" in text
    assert "S1 PASS" in text and "S5 PASS" in text


def test_matrix_without_any_record_is_not_eligible(root):
    text = format_matrix(current_verdicts(root))

    assert "ADR-0002 eligible: no" in text
    assert "ADR-0005 eligible: no" in text
