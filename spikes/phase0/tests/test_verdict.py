"""spikectl verdict: derivation from scenario outcomes, current verdict per spike,
voiding by a new frozen version, gate matrix with X1 on a separate line."""

import json

import pytest
from helpers import (
    LOCK,
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
from spikectl.model import SPIKES, LockEntry, sha256_hex
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


def test_matrix_without_any_record_is_not_eligible(root):
    text = format_matrix(current_verdicts(root))

    assert "ADR-0002 eligible: no" in text
    assert "ADR-0005 eligible: no" in text


# --- S2: derived pending and the review record ------------------------------


def frozen_s2(root, version=1):
    doc = criteria_doc("S2", version=version, scenarios=7, review=True)
    write_criteria(root, doc=doc)
    return freeze(root, "S2")


def s2_run(root, entry, stamp="20261022T101500Z", **outcome_changes):
    outcomes = {**S2_OUTCOMES, **outcome_changes}
    doc = record_doc("S2", entry.version, entry.sha256, outcomes, stamp)
    write_record(root, doc)
    return doc


def s2(root):
    return current_verdicts(root)["S2"]


def test_an_s2_run_without_a_review_is_pending(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)

    verdict = s2(root)

    assert run["verdict"] == "PASS"  # the stored verdict is the automated one
    assert verdict.verdict == "pending"
    assert verdict.record_id == run["record_id"]
    assert verdict.errors == []


def test_a_pending_s2_blocks_the_adr_gate_and_is_shown(root):
    for spike in ("S1", "S3", "S4"):
        entry = frozen(root, spike)
        put_record(root, entry, spike, {f"{spike}.1": "pass", f"{spike}.2": "pass"})
    s2_run(root, frozen_s2(root))
    verdicts = current_verdicts(root)

    assert verdicts["S2"].verdict == "pending"
    assert gate_matrix(verdicts)["ADR-0002"] is False
    assert "S2 pending" in format_matrix(verdicts)


def test_a_passing_review_opens_the_adr_gate(root):
    for spike in ("S1", "S3", "S4"):
        entry = frozen(root, spike)
        put_record(root, entry, spike, {f"{spike}.1": "pass", f"{spike}.2": "pass"})
    run = s2_run(root, frozen_s2(root))
    write_review(root, review_doc(run, "pass"))

    assert gate_matrix(current_verdicts(root))["ADR-0002"] is True


def test_a_passing_review_makes_the_s2_run_pass(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    review = review_doc(run, "pass")
    write_review(root, review)

    verdict = s2(root)

    assert verdict.verdict == "PASS"
    assert verdict.record_id == run["record_id"]
    assert verdict.review_id == review["record_id"]
    assert verdict.errors == []


def test_a_failing_review_makes_the_s2_run_fail(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    write_review(root, review_doc(run, "fail"))

    assert s2(root).verdict == "FAIL"


def test_a_failed_automated_step_check_fails_without_waiting_for_review(root):
    entry = frozen_s2(root)
    s2_run(root, entry, **{"S2.7": "fail"})

    assert s2(root).verdict == "FAIL"


def test_a_passing_review_cannot_override_a_failed_automated_step_check(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry, **{"S2.7": "fail"})
    write_review(root, review_doc(run, "pass"))

    assert s2(root).verdict == "FAIL"


def test_a_failed_other_scenario_is_fail_not_pending(root):
    # The review judges S2.7 only: it cannot rescue a run whose other scenario failed.
    entry = frozen_s2(root)
    s2_run(root, entry, **{"S2.3": "fail"})

    assert s2(root).verdict == "FAIL"


def test_a_passing_review_cannot_rescue_a_failed_other_scenario(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry, **{"S2.3": "fail"})
    write_review(root, review_doc(run, "pass"))

    assert s2(root).verdict == "FAIL"


def test_review_records_are_ignored_when_choosing_the_newest_spike_run(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry, stamp="20261022T101500Z")
    write_review(root, review_doc(run, "pass", stamp="20261030T000000Z"))

    verdict = s2(root)

    assert verdict.record_id == run["record_id"]
    assert verdict.verdict == "PASS"


def test_a_review_of_an_older_run_does_not_apply_to_a_newer_run(root):
    entry = frozen_s2(root)
    old = s2_run(root, entry, stamp="20261022T101500Z")
    write_review(root, review_doc(old, "pass"))
    new = s2_run(root, entry, stamp="20261025T080000Z")

    verdict = s2(root)

    assert verdict.record_id == new["record_id"]
    assert verdict.verdict == "pending"


def test_a_new_frozen_version_voids_the_review_too(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    write_review(root, review_doc(run, "pass"))
    frozen_s2(root, version=2)

    verdict = s2(root)

    assert (verdict.verdict, verdict.criteria_version) == ("none", 2)


def test_a_review_whose_hash_differs_from_the_lock_line_is_an_error_and_not_applied(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    write_review(root, {**review_doc(run, "pass"), "criteria_sha256": "f" * 64})

    verdict = s2(root)

    assert verdict.verdict == "pending"
    assert any("hash differs" in e for e in verdict.errors)


def test_conflicting_reviews_of_one_run_are_an_error_and_the_failure_stands(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    write_review(root, review_doc(run, "fail", stamp="20261023T120000Z"))
    write_review(root, review_doc(run, "pass", stamp="20261024T120000Z"))

    verdict = s2(root)

    assert verdict.verdict == "FAIL"
    assert any("more than one review" in e for e in verdict.errors)


def test_a_failing_review_without_fallback_decision_is_an_error(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    review = review_doc(run, "fail")
    del review["fallback_decision"]
    write_review(root, review)

    verdict = s2(root)

    assert verdict.verdict == "FAIL"
    assert any("fallback_decision" in e for e in verdict.errors)


def test_a_non_pass_review_without_fallback_decision_is_an_error(root):
    entry = frozen_s2(root)
    run = s2_run(root, entry)
    review = review_doc(run, "pass")
    review["review"]["outcome"] = "unexpected"
    write_review(root, review)

    verdict = s2(root)

    assert any("fallback_decision" in e for e in verdict.errors)


def test_a_stored_pending_verdict_is_an_error(root):
    entry = frozen_s2(root)
    doc = record_doc("S2", entry.version, entry.sha256, S2_OUTCOMES)
    doc["verdict"] = "pending"
    write_record(root, doc)

    verdict = s2(root)

    assert verdict.verdict == "pending"
    assert any("typed verdict" in e for e in verdict.errors)


def test_a_spike_without_a_review_scenario_never_waits_for_review(root):
    # freeze refuses S2 criteria without the reviewed S2.7 and verify reports them (T037d);
    # the verdict still derives from the criteria as locked, so lock such a file by hand.
    path = write_criteria(root, doc=criteria_doc("S2", status="frozen"))
    entry = LockEntry(path.name, 1, sha256_hex(path.read_bytes()))
    (root / LOCK).write_text(entry.line() + "\n", encoding="utf-8")
    put_record(root, entry, "S2", {"S2.1": "pass", "S2.2": "pass"})

    assert s2(root).verdict == "PASS"
