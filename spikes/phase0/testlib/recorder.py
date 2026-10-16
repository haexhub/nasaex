"""Scenario recorder: per-scenario isolation, per-case entries, evidence references.

The test driver's `subtest` re-raises and would abort the script on the first failure, so
every scenario runs through this recorder instead (research D2). Outcomes are exactly the five
of the verdict model; only `pass` counts as passing. Cases of a variant scenario (the `variants`
of the criteria, for example `mount-variants`) carry the `gating` flag of their variant; only
gating cases decide the scenario outcome, informational ones are recorded and never change it."""

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

OUTCOMES = ("pass", "fail", "error", "skipped", "flaky")
# Scenario outcome of a case matrix with any non-pass case: the most severe case outcome.
SEVERITY = ("error", "fail", "flaky", "skipped")


class Skip(Exception):
    """A scenario that cannot apply here; counts as failed like every non-pass outcome."""


@dataclass(frozen=True)
class Case:
    id: str  # <scenario id>:<case>, for example S3.2:post:M5:poweroff
    fault_kind: str
    fault_point: str


def counts_as_pass(outcome: str) -> bool:
    return outcome == "pass"


class Recorder:
    def __init__(
        self,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        criteria: dict | None = None,
    ):
        self._deadline = deadline
        self._clock = clock
        # scenario id -> {variant name: gating}, for the scenarios the criteria give variants
        self._variants = {
            scenario["id"]: {v["case"]: v["gating"] for v in scenario["variants"]}
            for scenario in (criteria or {}).get("scenarios", [])
            if "variants" in scenario
        }
        self._results: dict[str, dict] = {}
        self._evidence: list[dict] = []

    @staticmethod
    def case(case_id: str, fault_kind: str, fault_point: str) -> Case:
        return Case(case_id, fault_kind, fault_point)

    def _execute(self, label: str, fn: Callable[[], None]) -> tuple[str, str]:
        """Run fn in isolation; return (outcome, note)."""
        if self._deadline is not None and self._clock() > self._deadline:
            return "error", f"{label}: budget exhausted before it started"
        try:
            fn()
        except Skip as skip:
            return "skipped", f"{label}: {skip}"
        except AssertionError as failure:
            return "fail", f"{label}: {failure}"
        except Exception as error:  # noqa: BLE001 - isolation: any harness error is an outcome
            return "error", f"{label}: {type(error).__name__}: {error}"
        if self._deadline is not None and self._clock() > self._deadline:
            return "error", f"{label}: budget exhausted after it finished"
        return "pass", ""

    def _claim(self, scenario_id: str) -> None:
        if scenario_id in self._results:
            raise ValueError(f"scenario {scenario_id} was already recorded")
        self._results[scenario_id] = {}
        self._evidence = []

    def _store(self, scenario_id: str, outcome: str, notes: list[str], started: float, **extra):
        result = {"id": scenario_id, "outcome": outcome, **extra}
        result["duration_s"] = self._clock() - started
        if notes:
            result["notes"] = "; ".join(notes)
        if self._evidence:
            result["evidence"] = list(self._evidence)
        self._results[scenario_id] = result

    def run(self, scenario_id: str, fn: Callable[[], None]) -> None:
        self._claim(scenario_id)
        started = self._clock()
        outcome, note = self._execute(scenario_id, fn)
        self._store(scenario_id, outcome, [note] if note else [], started)

    def run_cases(
        self, scenario_id: str, cases: Iterable[Case], fn: Callable[[Case], None]
    ) -> None:
        self._claim(scenario_id)
        started = self._clock()
        variants = self._variants.get(scenario_id)
        entries, notes = [], []
        try:
            for case in cases:
                gating = self._case_gating(scenario_id, variants, case)
                outcome, note = self._execute(case.id, lambda case=case: fn(case))
                entry = {
                    "id": case.id,
                    "outcome": outcome,
                    "fault_kind": case.fault_kind,
                    "fault_point": case.fault_point,
                }
                if variants is not None:
                    entry["gating"] = gating
                entries.append(entry)
                if note:
                    notes.append(note)
        except Exception as error:  # noqa: BLE001 - iterator failures are scenario outcomes
            outcome = "error"
            notes.append(f"{scenario_id}: {type(error).__name__}: {error}")
        else:
            outcomes = {entry["outcome"] for entry in entries if entry.get("gating", True)}
            ran = {entry["id"] for entry in entries}
            missing = [
                f"{scenario_id}:{name}"
                for name, gating in (variants or {}).items()
                if gating and f"{scenario_id}:{name}" not in ran
            ]
            if not entries:
                outcome, notes = "error", [f"{scenario_id}: no cases were generated"]
            elif missing:
                outcome = "error"
                notes.append(f"{scenario_id}: gating variant(s) not run: {', '.join(missing)}")
            elif outcomes == {"pass"}:
                outcome = "pass"
            else:
                outcome = next(o for o in SEVERITY if o in outcomes)
        self._store(scenario_id, outcome, notes, started, cases=entries)

    @staticmethod
    def _case_gating(scenario_id: str, variants: dict[str, bool] | None, case: Case) -> bool:
        """Gating flag of a case: that of its variant, true for scenarios without variants."""
        if variants is None:
            return True
        name = case.id.removeprefix(f"{scenario_id}:")
        if name not in variants:
            raise ValueError(f"{case.id} is not a variant listed by the criteria of {scenario_id}")
        return variants[name]

    def attach(self, path: str, sha256: str) -> None:
        """Reference an entry of the evidence archive from the running scenario."""
        self._evidence.append({"path": path, "sha256": sha256})

    def complete(self, expected_ids: Iterable[str], reason: str) -> None:
        """Record every expected scenario that never finished as an error."""
        for scenario_id in expected_ids:
            if scenario_id not in self._results:
                self._results[scenario_id] = {
                    "id": scenario_id,
                    "outcome": "error",
                    "notes": f"{scenario_id}: not finished ({reason})",
                }

    def scenarios(self) -> list[dict]:
        return list(self._results.values())
