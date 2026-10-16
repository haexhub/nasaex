"""Scenario recorder: five outcomes, only pass passes, per-scenario isolation, cases,
budget handling, and output that fits the result-record schema."""

import pytest
from helpers import criteria_doc, make_root, record_doc

from spikectl.model import schema_errors
from spikectl.verdict import derive_verdict
from testlib.recorder import OUTCOMES, Recorder, Skip, counts_as_pass


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def by_id(recorder):
    return {s["id"]: s for s in recorder.scenarios()}


def test_the_outcomes_are_exactly_the_five_of_the_verdict_model():
    assert set(OUTCOMES) == {"pass", "fail", "error", "skipped", "flaky"}


def test_only_pass_counts_as_passing():
    assert [o for o in OUTCOMES if counts_as_pass(o)] == ["pass"]


def test_returning_normally_is_pass_and_assertions_are_fail():
    rec = Recorder()

    rec.run("S1.1", lambda: None)
    rec.run("S1.2", lambda: (_ for _ in ()).throw(AssertionError("content differs")))

    results = by_id(rec)
    assert results["S1.1"]["outcome"] == "pass"
    assert results["S1.2"]["outcome"] == "fail"
    assert "content differs" in results["S1.2"]["notes"]


def test_other_exceptions_are_errors_and_skip_is_skipped():
    rec = Recorder()

    def boom():
        raise RuntimeError("driver lost the machine")

    def skip():
        raise Skip("needs a second disk")

    rec.run("S1.1", boom)
    rec.run("S1.2", skip)

    results = by_id(rec)
    assert results["S1.1"]["outcome"] == "error"
    assert results["S1.2"]["outcome"] == "skipped"


def test_one_failing_scenario_does_not_stop_the_others():
    rec = Recorder()
    ran = []

    def boom():
        raise RuntimeError("x")

    rec.run("S1.1", boom)
    rec.run("S1.2", lambda: ran.append("S1.2"))

    assert ran == ["S1.2"]
    assert by_id(rec)["S1.2"]["outcome"] == "pass"


def test_a_scenario_with_cases_passes_only_if_every_case_passes():
    rec = Recorder()

    def case_fn(case):
        assert case.id != "S3.2:post:M5:poweroff", "lost a file"

    rec.run_cases(
        "S3.2",
        [
            Recorder.case("S3.2:post:M4:kill", "kill", "post:M4"),
            Recorder.case("S3.2:post:M5:poweroff", "poweroff", "post:M5"),
        ],
        case_fn,
    )

    scenario = by_id(rec)["S3.2"]
    assert scenario["outcome"] == "fail"
    assert [(c["id"], c["outcome"]) for c in scenario["cases"]] == [
        ("S3.2:post:M4:kill", "pass"),
        ("S3.2:post:M5:poweroff", "fail"),
    ]


def test_case_entries_carry_fault_kind_and_point():
    rec = Recorder()

    rec.run_cases("S3.2", [Recorder.case("S3.2:pre:M1:kill", "kill", "pre:M1")], lambda case: None)

    assert by_id(rec)["S3.2"]["cases"] == [
        {"id": "S3.2:pre:M1:kill", "outcome": "pass", "fault_kind": "kill", "fault_point": "pre:M1"}
    ]
    assert by_id(rec)["S3.2"]["outcome"] == "pass"


# --- variant cases and the gating flag -------------------------------------


def variant_criteria():
    """S1.2 with the baseline variant `ff` gating and the variant `lus` informational."""
    doc = criteria_doc("S1")
    doc["scenarios"][1].update(
        cases="mount-variants",
        variants=[{"case": "ff", "gating": True}, {"case": "lus", "gating": False}],
    )
    return doc


def variant_cases(*names):
    return [Recorder.case(f"S1.2:{name}", "none", "mount") for name in names]


def run_variants(outcomes, names=("ff", "lus"), criteria=None):
    rec = Recorder(criteria=criteria or variant_criteria())

    def case_fn(case):
        outcome = outcomes[case.id.split(":")[1]]
        if outcome == "fail":
            raise AssertionError("differs")
        if outcome == "error":
            raise RuntimeError("driver lost the machine")
        if outcome == "skipped":
            raise Skip("not applicable")

    rec.run_cases("S1.2", variant_cases(*names), case_fn)
    return by_id(rec)["S1.2"]


def test_variant_case_entries_carry_the_gating_flag_of_the_criteria():
    scenario = run_variants({"ff": "pass", "lus": "pass"})

    assert [(c["id"], c["gating"]) for c in scenario["cases"]] == [
        ("S1.2:ff", True),
        ("S1.2:lus", False),
    ]


@pytest.mark.parametrize("outcome", ["fail", "error", "skipped"])
def test_an_informational_variant_never_changes_the_scenario_outcome(outcome):
    scenario = run_variants({"ff": "pass", "lus": outcome})

    assert scenario["outcome"] == "pass"
    assert [c["outcome"] for c in scenario["cases"]] == ["pass", outcome]


def test_a_failing_gating_variant_fails_the_scenario_whatever_the_informational_ones_do():
    scenario = run_variants({"ff": "fail", "lus": "pass"})

    assert scenario["outcome"] == "fail"


def test_the_most_severe_gating_outcome_decides_not_an_informational_one():
    criteria = variant_criteria()
    criteria["scenarios"][1]["variants"].append({"case": "second", "gating": True})
    rec = Recorder(criteria=criteria)

    def case_fn(case):
        if case.id.endswith("lus"):
            raise RuntimeError("informational error")
        if case.id.endswith("second"):
            raise AssertionError("gating failure")

    rec.run_cases("S1.2", variant_cases("ff", "lus", "second"), case_fn)

    assert by_id(rec)["S1.2"]["outcome"] == "fail"


def test_a_gating_variant_that_was_not_run_is_an_error():
    scenario = run_variants({"lus": "pass"}, names=("lus",))

    assert scenario["outcome"] == "error"
    assert "S1.2:ff" in scenario["notes"]


def test_a_case_that_is_not_a_listed_variant_is_an_error():
    scenario = run_variants({"ff": "pass", "other": "pass"}, names=("ff", "other"))

    assert scenario["outcome"] == "error"
    assert "other" in scenario["notes"]


def test_cases_of_a_scenario_without_variants_carry_no_gating_flag():
    rec = Recorder(criteria=variant_criteria())

    rec.run_cases("S1.1", [Recorder.case("S1.1:a", "kill", "pre:M1")], lambda case: None)

    assert "gating" not in by_id(rec)["S1.1"]["cases"][0]


def test_variant_output_fits_the_schema_and_the_derived_verdict_ignores_informational_cases(
    tmp_path,
):
    root = make_root(tmp_path)
    rec = Recorder(criteria=variant_criteria())
    rec.run("S1.1", lambda: None)

    def case_fn(case):
        assert case.id != "S1.2:lus", "informational variant differs"

    rec.run_cases("S1.2", variant_cases("ff", "lus"), case_fn)
    doc = record_doc("S1", 1, "0" * 64)
    doc["scenarios"] = rec.scenarios()

    assert schema_errors(root, "result-record.schema.json", doc) == []
    assert derive_verdict(variant_criteria(), doc) == "PASS"


def test_error_in_a_case_beats_fail_and_every_case_still_runs():
    rec = Recorder()
    seen = []

    def case_fn(case):
        seen.append(case.id)
        if case.id.endswith("a"):
            raise RuntimeError("infrastructure")
        if case.id.endswith("b"):
            raise AssertionError("wrong")

    cases = [Recorder.case(f"S3.2:{x}", "kill", x) for x in "abc"]
    rec.run_cases("S3.2", cases, case_fn)

    assert seen == ["S3.2:a", "S3.2:b", "S3.2:c"]
    assert by_id(rec)["S3.2"]["outcome"] == "error"


def test_a_scenario_started_after_the_deadline_is_an_error_and_is_not_run():
    clock = Clock()
    rec = Recorder(deadline=100.0, clock=clock)
    ran = []

    clock.now = 101.0
    rec.run("S1.1", lambda: ran.append(1))

    assert ran == []
    assert by_id(rec)["S1.1"]["outcome"] == "error"
    assert "budget" in by_id(rec)["S1.1"]["notes"]


def test_a_scenario_finishing_after_the_deadline_is_an_error():
    clock = Clock()
    rec = Recorder(deadline=100.0, clock=clock)

    def work():
        clock.now = 101.0

    rec.run("S1.1", work)

    assert by_id(rec)["S1.1"]["outcome"] == "error"
    assert "after it finished" in by_id(rec)["S1.1"]["notes"]


def test_case_generation_failure_records_observed_cases_and_scenario_error():
    rec = Recorder()

    def cases():
        yield Recorder.case("S1.1:a", "kill", "pre:M1")
        raise RuntimeError("case source failed")

    rec.run_cases("S1.1", cases(), lambda case: None)

    scenario = by_id(rec)["S1.1"]
    assert scenario["outcome"] == "error"
    assert [case["id"] for case in scenario["cases"]] == ["S1.1:a"]
    assert "case source failed" in scenario["notes"]


def test_scenarios_unfinished_at_the_budget_become_errors():
    rec = Recorder()
    rec.run("S1.1", lambda: None)

    rec.complete(["S1.1", "S1.2", "S1.3"], reason="budget exhausted")

    results = by_id(rec)
    assert results["S1.1"]["outcome"] == "pass"
    assert results["S1.2"]["outcome"] == "error"
    assert results["S1.3"]["outcome"] == "error"
    assert "budget exhausted" in results["S1.3"]["notes"]


def test_duration_is_measured_with_the_injected_clock():
    clock = Clock()
    rec = Recorder(clock=clock)

    def work():
        clock.now += 12.5

    rec.run("S1.1", work)

    assert by_id(rec)["S1.1"]["duration_s"] == 12.5


def test_evidence_attached_while_running_is_recorded_for_that_scenario():
    rec = Recorder()

    rec.run("S1.1", lambda: rec.attach("manifests/s1.json", "d" * 64))
    rec.run("S1.2", lambda: None)

    results = by_id(rec)
    assert results["S1.1"]["evidence"] == [{"path": "manifests/s1.json", "sha256": "d" * 64}]
    assert "evidence" not in results["S1.2"]


def test_a_scenario_id_can_only_be_recorded_once():
    rec = Recorder()
    rec.run("S1.1", lambda: None)

    with pytest.raises(ValueError, match="S1.1"):
        rec.run("S1.1", lambda: None)


def test_recorder_output_fits_the_result_record_schema(tmp_path):
    root = make_root(tmp_path)
    rec = Recorder()
    rec.run("S1.1", lambda: None)
    rec.run_cases("S1.2", [Recorder.case("S1.2:a", "kill", "pre:M1")], lambda case: None)
    rec.complete(["S1.1", "S1.2", "S1.3"], reason="budget exhausted")
    doc = record_doc("S1", 1, "0" * 64)
    doc["scenarios"] = rec.scenarios()
    doc["verdict"] = "FAIL"
    doc["fallback_decision"] = "Adopt the documented fallback."

    assert schema_errors(root, "result-record.schema.json", doc) == []


def test_review_failure_requires_an_immutable_fallback_decision(tmp_path):
    root = make_root(tmp_path)
    doc = {
        "record_id": "review-S2-20261016T120000Z-v1",
        "kind": "review",
        "spike": "S2",
        "criteria_version": 1,
        "criteria_sha256": "a" * 64,
        "reviewed_record": "S2-20261016T110000Z-v1",
        "review": {
            "scenario": "S2.7",
            "outcome": "pass",
            "reviewer": "maintainer",
            "date": "2026-10-16T12:00:00Z",
            "evidence": [{"path": "procedure.txt", "sha256": "b" * 64}],
        },
    }

    assert schema_errors(root, "result-record.schema.json", doc) == []

    failing = {
        **doc,
        "review": {**doc["review"], "outcome": "fail"},
        "fallback_decision": "Adopt the documented fallback.",
    }
    assert schema_errors(root, "result-record.schema.json", failing) == []

    without_fallback = {key: value for key, value in failing.items() if key != "fallback_decision"}
    assert schema_errors(root, "result-record.schema.json", without_fallback)
