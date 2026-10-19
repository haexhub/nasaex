"""spikectl verdict for S2: the derived pending status and the review record of S2.7."""

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
from spikectl.model import LockEntry, sha256_hex
from spikectl.verdict import current_verdicts, format_matrix, gate_matrix


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
