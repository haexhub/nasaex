"""spikectl verdict: derivation from scenario outcomes, current verdict per spike,
voiding by a new frozen version, gate matrix with X1 on a separate line (S2 and its review
record: test_verdict_review.py)."""

import json

import pytest
from helpers import (
    CRITERIA,
    RESULTS,
    S2_OUTCOMES,
    criteria_doc,
    make_root,
    record_doc,
    review_doc,
    write_criteria,
    write_record,
    write_review,
)

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


# --- gating of cases -------------------------------------------------------


def with_cases(record, scenario_id, cases):
    next(s for s in record["scenarios"] if s["id"] == scenario_id)["cases"] = cases
    return record


def test_an_informational_case_never_changes_the_scenario_outcome():
    record = with_cases(
        record_doc(outcomes={"S1.1": "pass", "S1.2": "pass"}),
        "S1.2",
        [
            {"id": "S1.2:ff", "outcome": "pass", "gating": True},
            {"id": "S1.2:lus", "outcome": "fail", "gating": False},
        ],
    )

    assert derive_verdict(criteria(), record) == "PASS"


@pytest.mark.parametrize("flag", [{"gating": True}, {}], ids=["gating-true", "flag-absent"])
def test_a_failing_case_without_gating_false_fails_the_scenario(flag):
    record = with_cases(
        record_doc(outcomes={"S1.1": "pass", "S1.2": "pass"}),
        "S1.2",
        [
            {"id": "S1.2:ff", "outcome": "pass", "gating": True},
            {"id": "S1.2:lus", "outcome": "fail", **flag},
        ],
    )

    assert derive_verdict(criteria(), record) == "FAIL"


def test_a_failing_scenario_stays_failed_when_all_its_gating_cases_pass():
    record = with_cases(
        record_doc(outcomes={"S1.1": "pass", "S1.2": "fail"}),
        "S1.2",
        [{"id": "S1.2:ff", "outcome": "pass", "gating": True}],
    )

    assert derive_verdict(criteria(), record) == "FAIL"


def test_the_current_verdict_ignores_a_failing_informational_case(root):
    entry = frozen(root, "S1")
    doc = record_doc("S1", entry.version, entry.sha256, {"S1.1": "pass", "S1.2": "pass"})
    write_record(
        root,
        with_cases(
            doc,
            "S1.2",
            [
                {"id": "S1.2:ff", "outcome": "pass", "gating": True},
                {"id": "S1.2:lus", "outcome": "error", "gating": False},
            ],
        ),
    )

    verdict = current_verdicts(root)["S1"]

    assert verdict.verdict == "PASS"
    assert verdict.errors == []


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


@pytest.mark.parametrize("data", ["{not json", "[]"], ids=["broken-json", "not-an-object"])
def test_an_unreadable_record_is_an_error_and_the_readable_ones_still_decide(root, data):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "pass"})
    broken = root / RESULTS / "S1" / "S1-20261023T090000Z-v1" / "record.json"
    broken.parent.mkdir(parents=True)
    broken.write_text(data, encoding="utf-8")

    verdict = current_verdicts(root)["S1"]

    assert verdict.verdict == "PASS"
    assert len(verdict.errors) == 1 and verdict.errors[0].startswith("record.json: ")


@pytest.mark.parametrize("text", [None, "spike: [S1\n"], ids=["missing", "broken-yaml"])
def test_locked_criteria_that_cannot_be_read_are_an_error_and_give_no_verdict(root, text):
    entry = frozen(root, "S1")
    put_record(root, entry, "S1", {"S1.1": "pass", "S1.2": "pass"})
    path = root / CRITERIA / "S1.yaml"
    if text is None:
        path.unlink()
    else:
        path.write_text(text, encoding="utf-8")

    verdict = current_verdicts(root)["S1"]

    assert (verdict.verdict, verdict.record_id) == ("none", None)
    assert len(verdict.errors) == 1 and verdict.errors[0].startswith("S1.yaml: ")


# --- gate matrix and X1 ----------------------------------------------------


def all_pass(root):
    for spike in ("S1", "S3", "S4", "S5"):
        entry = frozen(root, spike)
        put_record(root, entry, spike, {f"{spike}.1": "pass", f"{spike}.2": "pass"})
    # S2 cannot be frozen without the reviewed S2.7 (T037d), so it passes through a review.
    write_review(root, review_doc(s2_run(root, frozen_s2(root)), "pass"))


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


def test_an_x1_record_without_gating_false_is_an_error(root):
    entry = frozen(root, "X1")
    put_record(root, entry, "X1", {"X1.1": "pass", "X1.2": "pass"}, gating=True)

    verdict = current_verdicts(root)["X1"]

    assert verdict.verdict == "PASS"
    assert verdict.errors == ["X1-20261022T101500Z-v1: X1 record must carry gating: false"]


def test_matrix_without_any_record_is_not_eligible(root):
    text = format_matrix(current_verdicts(root))

    assert "ADR-0002 eligible: no" in text
    assert "ADR-0005 eligible: no" in text


# --- S2 builders (all_pass; the S2 tests: test_verdict_review.py) ---------------


def frozen_s2(root, version=1):
    doc = criteria_doc("S2", version=version, scenarios=7, review=True)
    write_criteria(root, doc=doc)
    return freeze(root, "S2")


def s2_run(root, entry, stamp="20261022T101500Z", **outcome_changes):
    outcomes = {**S2_OUTCOMES, **outcome_changes}
    doc = record_doc("S2", entry.version, entry.sha256, outcomes, stamp)
    write_record(root, doc)
    return doc
