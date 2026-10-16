"""Record writer, RAM-budget preflight and mode gate of `spikectl run`.

Scaffolding for the Phase 0 spikes, not product code.

Two modes: `shakedown` runs anything and writes only below spikes/phase0/scratch; `record`
produces the immutable record directory docs/phase0/results/<spike>/<record-id>/. Both modes
refuse to start when the aggregate VM RAM budget is not available (the reference runner is a
maintainer workstation, so overcommitting memory is never acceptable)."""

import datetime
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from spikectl.model import (
    CRITERIA_DIR,
    GATING_SPIKES,
    RESULTS_DIR,
    LockEntry,
    parse_criteria_filename,
    read_lock,
    schema_errors,
    sha256_hex,
)
from spikectl.verdict import derive_verdict

DEFAULT_RAM_BUDGET_MIB = 8192
RAM_BUDGET_ENV = "SPIKE_VM_RAM_BUDGET_MIB"
SCRATCH_DIR = Path("spikes/phase0/scratch")
MODES = ("shakedown", "record")

# Stated in every record (plan open item O14).
FIXED_RESIDUAL_RISK = (
    "Device-level volatile write caches are not emulated: a poweroff fault is QEMU process "
    "termination, not host power loss.",
    "Virtual disks stand in for real disks: timing, error behaviour and wear of physical "
    "drives are not represented.",
)


class RecordError(Exception):
    """A record cannot be written."""


class PreflightError(RecordError):
    """The run must not start."""


def configured_ram_budget_mib(env: Mapping[str, str]) -> int:
    """Aggregate RAM budget for all test VMs together: 8 GiB unless configured."""
    raw = env.get(RAM_BUDGET_ENV)
    if raw is None:
        return DEFAULT_RAM_BUDGET_MIB
    if not raw.isdigit() or int(raw) < 1:
        raise PreflightError(f"{RAM_BUDGET_ENV} must be a positive number of MiB, got {raw!r}")
    return int(raw)


def read_available_ram_mib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise PreflightError("MemAvailable is not reported by /proc/meminfo")


def _git_dirty(root: Path) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False
    )
    if status.returncode != 0:
        raise PreflightError("record mode needs a git work tree")
    return bool(status.stdout.strip())


def _newest_criteria_file(root: Path, spike: str) -> Path | None:
    found = {}
    for path in (root / CRITERIA_DIR).glob("*.yaml"):
        parsed = parse_criteria_filename(path.name)
        if parsed and parsed[0] == spike:
            found[parsed[1]] = path
    return found[max(found)] if found else None


def preflight(
    root: Path,
    spike: str,
    mode: str,
    *,
    vm_ram_mib: int,
    ram_budget_mib: int = DEFAULT_RAM_BUDGET_MIB,
    available_ram_mib: Callable[[], int] = read_available_ram_mib,
    kvm_path: Path = Path("/dev/kvm"),
) -> None:
    """Refuse (PreflightError) instead of starting a run that must not happen."""
    if mode not in MODES:
        raise PreflightError(f"unknown mode {mode!r}, expected one of {', '.join(MODES)}")
    if vm_ram_mib > ram_budget_mib:
        raise PreflightError(
            f"planned VM memory {vm_ram_mib} MiB exceeds the configured budget {ram_budget_mib} MiB"
        )
    available = available_ram_mib()
    if available < vm_ram_mib:
        raise PreflightError(
            f"only {available} MiB RAM available, the VMs need {vm_ram_mib} MiB; "
            "refusing to overcommit"
        )
    if mode == "shakedown":
        return

    if not os.access(kvm_path, os.R_OK | os.W_OK):
        raise PreflightError(
            f"no usable kvm device at {kvm_path}; record mode never falls back to TCG"
        )
    if _git_dirty(root):
        raise PreflightError("the git tree is dirty; commit or remove changes before a record run")
    criteria = _newest_criteria_file(root, spike)
    locked = {entry.file: entry for entry in read_lock(root)}
    if (
        criteria is None
        or criteria.name not in locked
        or sha256_hex(criteria.read_bytes()) != locked[criteria.name].sha256
    ):
        raise PreflightError(
            f"the current criteria file of {spike} is not locked (or differs from its locked hash)"
        )


def make_record_id(spike: str, criteria_version: int, when: datetime.datetime) -> str:
    stamp = when.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{spike}-{stamp}-v{criteria_version}"


def _utc(when: datetime.datetime) -> str:
    return when.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_record(
    *,
    spike: str,
    criteria: dict,
    lock_entry: LockEntry,
    scenarios: list[dict],
    environment: dict,
    start: datetime.datetime,
    end: datetime.datetime,
    evidence_sha256: str,
    residual_risk: Collection[str] = (),
    supersedes: str | None = None,
    coverage: dict | None = None,
    required_settings: dict | None = None,
) -> dict:
    """Assemble a record; the verdict is derived from the scenario outcomes only."""
    verdict = derive_verdict(criteria, {"scenarios": scenarios})
    record = {
        "record_id": make_record_id(spike, lock_entry.version, start),
        "kind": "spike-run",
        "spike": spike,
        "criteria_version": lock_entry.version,
        "criteria_sha256": lock_entry.sha256,
        "environment": environment,
        "date": {"start": _utc(start), "end": _utc(end)},
        "scenarios": scenarios,
        "verdict": verdict,
        "residual_risk": [
            *FIXED_RESIDUAL_RISK,
            *(r for r in residual_risk if r not in FIXED_RESIDUAL_RISK),
        ],
        "evidence_archive": {"path": "evidence.tar.xz", "sha256": evidence_sha256},
    }
    if spike == "X1":
        record["gating"] = False
    elif verdict == "FAIL" and spike in GATING_SPIKES:
        record["fallback_decision"] = criteria["fallback"]
    if supersedes:
        record["supersedes"] = supersedes
    if coverage:
        record["coverage"] = coverage
    if required_settings:
        record["required_settings"] = required_settings
    return record


def build_review_record(
    *,
    reviewed_record: dict,
    criteria: dict,
    outcome: str,
    reviewer: str,
    when: datetime.datetime,
    evidence: Sequence[dict],
    notes: str | None = None,
) -> dict:
    """Assemble the review record of S2.7: it names the reviewed S2 spike-run and carries its
    criteria version and hash, but no run fields. The evidence entries are paths inside the
    reviewed record's archive with their SHA-256."""
    if reviewed_record.get("kind") != "spike-run" or reviewed_record.get("spike") != "S2":
        raise RecordError("only an S2 spike-run record can be reviewed")
    if outcome not in ("pass", "fail"):
        raise RecordError(f"review outcome must be pass or fail, got {outcome!r}")
    if not reviewer:
        raise RecordError("a review names its reviewer")
    if not evidence:
        raise RecordError("a review lists the evidence it read")
    version = reviewed_record["criteria_version"]
    review = {
        "scenario": "S2.7",
        "outcome": outcome,
        "reviewer": reviewer,
        "date": _utc(when),
        "evidence": list(evidence),
    }
    if notes:
        review["notes"] = notes
    record = {
        "record_id": f"review-{make_record_id('S2', version, when)}",
        "kind": "review",
        "spike": "S2",
        "criteria_version": version,
        "criteria_sha256": reviewed_record["criteria_sha256"],
        "reviewed_record": reviewed_record["record_id"],
        "review": review,
    }
    if outcome == "fail":
        record["fallback_decision"] = criteria["fallback"]
    return record


def write_record(root: Path, record: dict, evidence_path: Path | None, mode: str) -> Path:
    """Write record.json and evidence.tar.xz; record mode into the results tree, shakedown
    into scratch. An existing record directory is never touched. A review record has no
    evidence archive of its own (evidence_path is None): it pins entries of the reviewed one."""
    problems = schema_errors(root, "result-record.schema.json", record)
    if problems:
        raise RecordError("record does not validate against the schema: " + "; ".join(problems))
    if (record["kind"] == "review") != (evidence_path is None):
        raise RecordError("a review record has no evidence archive, every other record needs one")
    if evidence_path is not None and (
        sha256_hex(evidence_path.read_bytes()) != record["evidence_archive"]["sha256"]
    ):
        raise RecordError("evidence archive does not match the hash named in the record")
    base = root / (
        RESULTS_DIR / record["spike"] if mode == "record" else SCRATCH_DIR / record["spike"]
    )
    target = base / record["record_id"]
    if target.exists():
        raise RecordError(f"{target.relative_to(root)} exists; records are never overwritten")
    target.mkdir(parents=True)
    (target / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if evidence_path is not None:
        shutil.copyfile(evidence_path, target / "evidence.tar.xz")
    return target


__all__ = [
    "DEFAULT_RAM_BUDGET_MIB",
    "FIXED_RESIDUAL_RISK",
    "PreflightError",
    "RecordError",
    "build_record",
    "build_review_record",
    "configured_ram_budget_mib",
    "make_record_id",
    "preflight",
    "read_available_ram_mib",
    "write_record",
]
