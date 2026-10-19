"""The S1 registry (vm-tests/s1/scenarios.py): the criteria decide which scenarios run with
which check and which mount variants, and every scenario is recorded in isolation with the
result written after each one. The real draft criteria docs/phase0/criteria/S1.yaml must plan."""

import copy

import pytest
import yaml
from helpers import REPO_ROOT
from s1 import scenarios
from s1.context import Variant

from testlib.recorder import Recorder

CRITERIA = yaml.safe_load((REPO_ROOT / "docs/phase0/criteria/S1.yaml").read_text())


def test_the_s1_criteria_plan_every_scenario_in_order_with_its_named_check():
    planned = scenarios.plan(CRITERIA)

    assert [p.id for p in planned] == [f"S1.{n}" for n in range(1, 12)]
    for p, scenario in zip(planned, CRITERIA["scenarios"], strict=True):
        assert p.check is scenarios.CHECKS[scenario["check"]]


def test_variant_scenarios_carry_gating_and_overrides_from_the_criteria():
    planned = {p.id: p for p in scenarios.plan(CRITERIA)}

    assert planned["S1.2"].variants == (
        Variant("ff", True, {}),
        Variant("lus", False, {"category.create": "lus"}),
    )
    rw = {"passthrough.io": "rw", "cache.files": "auto-full"}
    for sid in ("S1.6", "S1.8"):
        assert planned[sid].variants == (
            Variant("baseline", True, {}),
            Variant("passthrough-rw-auto-full", False, rw),
        )
    assert planned["S1.1"].variants is None


def test_only_plans_one_scenario_and_refuses_an_unknown_one():
    assert [p.id for p in scenarios.plan(CRITERIA, only="S1.9")] == ["S1.9"]
    with pytest.raises(ValueError, match="S1.99"):
        scenarios.plan(CRITERIA, only="S1.99")


def broken(mutate):
    criteria = copy.deepcopy(CRITERIA)
    mutate(criteria)
    return criteria


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["scenarios"][0].update(check="no_such_check"), "no_such_check"),
        (lambda c: c["tables"]["mount_variants"].pop("lus"), "lus"),
        (lambda c: c["tables"]["large_file"]["flip_offset"].pop("baseline"), "flip_offset"),
        (lambda c: c["tables"]["out_of_space"].pop("smb_status"), "smb_status"),
        (lambda c: c["tables"]["mover_view"].pop("points"), "points"),
        (lambda c: c["tables"]["bounds"].pop("view_consistency_s"), "view_consistency_s"),
        (lambda c: c["tables"]["placement_pool_low"].pop("leave_bytes"), "leave_bytes"),
    ],
)
def test_criteria_the_checks_cannot_run_with_are_refused_before_any_vm_starts(mutate, message):
    with pytest.raises(ValueError, match=message):
        scenarios.plan(broken(mutate))


def test_run_plan_records_each_scenario_in_isolation_and_saves_after_each():
    calls, saved = [], []

    def ok(lab, *variant):
        calls.append(("ok", *(v.name for v in variant)))

    def fails_informational(lab, variant):
        calls.append(("v", variant.name))
        assert variant.gating, "informational variant failed"

    def raises(lab):
        raise RuntimeError("harness defect")

    plan = [
        scenarios.Planned("S1.1", raises, None),
        scenarios.Planned("S1.2", fails_informational, scenarios.plan(CRITERIA)[1].variants),
        scenarios.Planned("S1.3", ok, None),
    ]
    recorder = Recorder(criteria=CRITERIA)

    scenarios.run_plan(
        recorder, object(), plan, on_done=lambda: saved.append(len(recorder.scenarios()))
    )

    results = {s["id"]: s for s in recorder.scenarios()}
    assert results["S1.1"]["outcome"] == "error"
    assert results["S1.2"]["outcome"] == "pass"
    assert [(c["id"], c["gating"], c["outcome"]) for c in results["S1.2"]["cases"]] == [
        ("S1.2:ff", True, "pass"),
        ("S1.2:lus", False, "fail"),
    ]
    assert results["S1.3"]["outcome"] == "pass"
    assert calls == [("v", "ff"), ("v", "lus"), ("ok",)]
    assert saved == [1, 2, 3]
