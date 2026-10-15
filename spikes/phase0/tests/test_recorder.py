"""Scenario recorder: five outcomes, only pass passes, per-scenario isolation, cases,
budget handling, and output that fits the result-record schema."""

import pytest
from helpers import make_root, record_doc

from spikectl.model import schema_errors
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
