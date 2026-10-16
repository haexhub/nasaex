"""spikectl verdict: derive verdicts from records and print the advisory gate matrix.

Scaffolding for the Phase 0 spikes, not product code. Nothing here edits an ADR."""

from dataclasses import dataclass, field
from pathlib import Path

from spikectl.model import (
    CRITERIA_DIR,
    GATING_SPIKES,
    SPIKES,
    ModelError,
    load_criteria,
    load_record,
    parse_criteria_filename,
    read_lock,
    record_paths,
    record_timestamp,
)

# Which gating spikes each ADR needs (data-model.md, "ADR gate").
ADR_REQUIRES = {"ADR-0002": ("S1", "S2", "S3", "S4"), "ADR-0005": ("S5",)}


@dataclass
class SpikeVerdict:
    spike: str
    criteria_version: int | None = None
    criteria_sha256: str | None = None
    verdict: str = "none"  # PASS, FAIL, pending (derived, never stored) or none
    record_id: str | None = None
    review_id: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def gating(self) -> bool:
        return self.spike in GATING_SPIKES


def _scenario_passes(scenario: dict | None) -> bool:
    """Only gating cases count: `gating: false` is informational, an absent flag gates."""
    if scenario is None or scenario.get("outcome") != "pass":
        return False
    return all(
        case.get("outcome") == "pass"
        for case in scenario.get("cases", [])
        if case.get("gating", True)
    )


def derive_verdict(criteria: dict, record: dict) -> str:
    """The automated verdict: PASS iff every scenario of the criteria appears in the record and
    passes (for S2.7 its automated step checks). This is what a spike-run record stores."""
    results = {scenario["id"]: scenario for scenario in record.get("scenarios", [])}
    ok = all(_scenario_passes(results.get(s["id"])) for s in criteria["scenarios"])
    return "PASS" if ok else "FAIL"


def derive_status(criteria: dict, record: dict, review: dict | None) -> str:
    """Spike status of one spike-run: the automated verdict combined with the review record.

    A scenario with `review: true` (S2.7) needs a review record once the automated verdict is PASS:
    `pending` while none names the run, else the review outcome. An automated FAIL (a failed
    step check or any other failed scenario) stays FAIL: no review can overturn it."""
    verdict = derive_verdict(criteria, record)
    if verdict == "FAIL" or not any(s.get("review") for s in criteria["scenarios"]):
        return verdict
    if review is None:
        return "pending"
    return "PASS" if review.get("review", {}).get("outcome") == "pass" else "FAIL"


def _record_errors(spike: str, record: dict, derived: str) -> list[str]:
    errors = []
    name = record["record_id"]
    if record.get("verdict") != derived:
        errors.append(
            f"{name}: typed verdict {record.get('verdict')!r} differs from derived {derived}"
        )
    if derived == "FAIL" and spike in GATING_SPIKES and not record.get("fallback_decision"):
        errors.append(f"{name}: FAIL record without fallback_decision")
    if spike == "X1" and record.get("gating") is not False:
        errors.append(f"{name}: X1 record must carry gating: false")
    return errors


def _review_for(run_id: str, reviews: list[dict], errors: list[str]) -> dict | None:
    """The review record that applies to a run. Reviews of other runs do not apply; when several
    name the run, a failing one stands (a failing review carries an immutable fallback_decision)."""
    named = sorted(
        (r for r in reviews if r.get("reviewed_record") == run_id),
        key=lambda r: record_timestamp(r["record_id"]),
    )
    if len(named) > 1:
        ids = ", ".join(r["record_id"] for r in named)
        errors.append(f"{run_id}: more than one review record names it ({ids})")
    failing = next((r for r in named if r.get("review", {}).get("outcome") != "pass"), None)
    return failing or (named[-1] if named else None)


def _current_for(root: Path, spike: str, lock) -> SpikeVerdict:
    entries = [e for e in lock if (parse_criteria_filename(e.file) or ("", 0))[0] == spike]
    if not entries:
        return SpikeVerdict(spike)
    current = max(entries, key=lambda e: e.version)
    result = SpikeVerdict(spike, current.version, current.sha256)

    runs, reviews = [], []
    for path in record_paths(root):
        try:
            record = load_record(path)
        except ModelError as error:
            result.errors.append(str(error))
            continue
        if record.get("kind") not in ("spike-run", "review") or record.get("spike") != spike:
            continue
        if record.get("criteria_version") != current.version:
            continue
        if record.get("criteria_sha256") != current.sha256:
            result.errors.append(f"{record['record_id']}: hash differs from the lock line")
            continue
        (runs if record["kind"] == "spike-run" else reviews).append(record)
    if not runs:
        return result

    newest = max(runs, key=lambda r: record_timestamp(r["record_id"]))
    try:
        criteria = load_criteria(root / CRITERIA_DIR / current.file)
    except (ModelError, OSError) as error:
        result.errors.append(f"{current.file}: {error}")
        return result
    review = _review_for(newest["record_id"], reviews, result.errors)
    result.record_id = newest["record_id"]
    result.verdict = derive_status(criteria, newest, review)
    result.errors += _record_errors(spike, newest, derive_verdict(criteria, newest))
    if review is not None:
        result.review_id = review["record_id"]
        failed = review.get("review", {}).get("outcome") == "fail"
        if failed and not review.get("fallback_decision"):
            result.errors.append(f"{review['record_id']}: FAIL review without fallback_decision")
    return result


def current_verdicts(root: Path) -> dict[str, SpikeVerdict]:
    """Current verdict of every spike: newest record naming the current locked version."""
    lock = read_lock(root)
    return {spike: _current_for(root, spike, lock) for spike in SPIKES}


def gate_matrix(verdicts: dict[str, SpikeVerdict]) -> dict[str, bool]:
    """ADR eligibility from the gating spikes only; X1 is never an input."""
    return {
        adr: all(verdicts[spike].verdict == "PASS" for spike in required)
        for adr, required in ADR_REQUIRES.items()
    }


def format_matrix(verdicts: dict[str, SpikeVerdict]) -> str:
    eligible = gate_matrix(verdicts)
    yes_no = {True: "yes", False: "no"}
    lines = [
        "  ".join(f"{spike} {verdicts[spike].verdict}" for spike in GATING_SPIKES),
        f"ADR-0002 eligible: {yes_no[eligible['ADR-0002']]} (S1..S4 must all PASS)   "
        f"ADR-0005 eligible: {yes_no[eligible['ADR-0005']]} (S5 must PASS)",
    ]
    x1 = verdicts["X1"].verdict
    lines.append(
        "X1 not run (optional, non-gating)" if x1 == "none" else f"X1 {x1} (optional, non-gating)"
    )
    for verdict in verdicts.values():
        if verdict.verdict == "pending":
            lines.append(
                f"{verdict.spike} pending: automated checks pass, the review record is missing; "
                "no ADR gate may be applied"
            )
    for verdict in verdicts.values():
        lines += [f"ERROR {verdict.spike}: {error}" for error in verdict.errors]
    return "\n".join(lines)
