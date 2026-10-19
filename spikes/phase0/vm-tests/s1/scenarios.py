"""The S1 registry (task T049): which check each scenario of the criteria runs, with which mount
variants, and the loop that records them.

The criteria file decides: every scenario runs in the criteria's order with the check its
`check` field names; a scenario with `variants` (S1.2, S1.6, S1.8; generator `mount-variants`)
runs one case per variant, with the variant's `gating` flag and the cache-pool options of
tables.mount_variants. Criteria that the checks cannot run with are refused before any VM
starts. Each scenario runs in isolation through testlib.recorder (an exception is an outcome,
the next scenario still runs), and the result is saved after each one. Scaffolding for the
spikes, not product code."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from s1 import (
    checks_inplace,
    checks_large_space,
    checks_mover_view,
    checks_placement,
    checks_rename_attrs,
    checks_view,
)
from s1.context import Variant

CHECKS: dict[str, Callable] = {
    "archive_view": checks_view.check_archive_view,
    "new_data_placement": checks_placement.check_new_data_placement,
    "inplace_modify": checks_inplace.check_inplace_modify,
    "inplace_delete": checks_inplace.check_inplace_delete,
    "delete_then_recreate": checks_inplace.check_delete_then_recreate,
    "rename_move": checks_rename_attrs.check_rename_move,
    "attributes_preserved": checks_rename_attrs.check_attributes_preserved,
    "large_files": checks_large_space.check_large_files,
    "pool_out_of_space": checks_large_space.check_pool_out_of_space,
    "mover_view": checks_mover_view.check_mover_view,
    "reboot_view": checks_view.check_reboot_view,
}

# Table entries a check reads (criteria `tables`), by check name.
TABLE_KEYS = {
    "new_data_placement": (("placement_pool_low", "leave_bytes"),),
    "large_files": (("large_file", "flip_offset"),),
    "pool_out_of_space": tuple(
        ("out_of_space", key) for key in ("errno", "smb_status", "leave_bytes", "write_bytes")
    ),
    "mover_view": (
        ("mover_view", "points"),
        ("mover_view", "refresh_procedure"),
        ("bounds", "view_consistency_s"),
    ),
}


@dataclass(frozen=True)
class Planned:
    id: str
    check: Callable
    variants: tuple[Variant, ...] | None


def _problems(criteria: Mapping, scenario: Mapping) -> list[str]:
    tables = criteria.get("tables", {})
    sid, name = scenario["id"], scenario.get("check")
    if name not in CHECKS:
        return [f"{sid}: no check named {name}"]
    problems = [
        f"{sid}: tables.{table}.{key} is missing"
        for table, key in TABLE_KEYS.get(name, ())
        if key not in tables.get(table, {})
    ]
    for variant in scenario.get("variants", ()):
        case = variant["case"]
        if case not in tables.get("mount_variants", {}):
            problems.append(f"{sid}: tables.mount_variants has no {case}")
        offsets = tables.get("large_file", {}).get("flip_offset", {})
        if name == "large_files" and case not in offsets:
            problems.append(f"{sid}: tables.large_file.flip_offset has no {case}")
    return problems


def plan(criteria: Mapping, only: str | None = None) -> list[Planned]:
    scenarios = criteria["scenarios"]
    if only is not None and only not in {s["id"] for s in scenarios}:
        raise ValueError(f"{only} is not a scenario of the criteria")
    problems = [p for s in scenarios for p in _problems(criteria, s)]
    if problems:
        raise ValueError("; ".join(problems))
    overrides = criteria.get("tables", {}).get("mount_variants", {})
    planned = []
    for scenario in scenarios:
        if only is not None and scenario["id"] != only:
            continue
        variants = None
        if "variants" in scenario:
            variants = tuple(
                Variant(v["case"], v["gating"], dict(overrides[v["case"]] or {}))
                for v in scenario["variants"]
            )
        planned.append(Planned(scenario["id"], CHECKS[scenario["check"]], variants))
    return planned


def run_plan(recorder, lab, planned: list[Planned], on_done: Callable[[], None]) -> None:
    """Run every planned scenario through the recorder; `on_done` after each one."""
    for scenario in planned:
        if scenario.variants is None:
            recorder.run(scenario.id, lambda s=scenario: s.check(lab))
        else:
            by_case = {f"{scenario.id}:{v.name}": v for v in scenario.variants}
            cases = [recorder.case(case_id, "none", "none") for case_id in by_case]
            recorder.run_cases(
                scenario.id, cases, lambda case, s=scenario, v=by_case: s.check(lab, v[case.id])
            )
        on_done()
