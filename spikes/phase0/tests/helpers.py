"""Builders shared by the spikectl unit tests: a throwaway repository root with
criteria files, lock lines, result records and real git history; and a probe for user xattrs on
the test filesystem."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = Path("specs/001-phase0-storage-spikes/contracts")
CRITERIA = Path("docs/phase0/criteria")
RESULTS = Path("docs/phase0/results")
LOCK = CRITERIA / "frozen.lock"

GATES = {
    "S1": "ADR-0002",
    "S2": "ADR-0002",
    "S3": "ADR-0002",
    "S4": "ADR-0002",
    "S5": "ADR-0005",
    "X1": "none",
}


def make_root(path: Path) -> Path:
    """Empty Phase 0 tree with the two schemas copied from the real contracts."""
    for name in ("criteria.schema.json", "result-record.schema.json"):
        target = path / CONTRACTS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / CONTRACTS / name, target)
    (path / CRITERIA).mkdir(parents=True, exist_ok=True)
    (path / RESULTS).mkdir(parents=True, exist_ok=True)
    return path


def criteria_doc(spike="S1", version=1, status="draft", scenarios=2, review=False):
    """`review=True` marks S2.7 as the reviewed scenario (spike S2 with at least 7 scenarios)."""
    doc = {
        "spike": spike,
        "criteria_version": version,
        "status": status,
        "name": f"{spike} test spike",
        "gates": GATES[spike],
    }
    if spike != "X1":
        doc["fallback"] = "Adopt the documented fallback."
    if version >= 2:
        doc.update(amends=version - 1, reason="wording fix", date="2026-10-15")
    doc["scenarios"] = [
        {
            "id": f"{spike}.{n}",
            "title": f"scenario {n}",
            "precondition": "given",
            "action": "when",
            "observable": "then",
            "check": f"check_{n}",
        }
        for n in range(1, scenarios + 1)
    ]
    if review:
        reviewed = doc["scenarios"][6]
        reviewed.update(review=True, review_kind="review")
    return doc


def criteria_name(spike: str, version: int) -> str:
    return f"{spike}.yaml" if version == 1 else f"{spike}.v{version}.yaml"


def write_criteria(root: Path, doc=None, name=None, **kwargs) -> Path:
    """Write a criteria file with a leading comment so tests can see it survive."""
    doc = doc or criteria_doc(**kwargs)
    name = name or criteria_name(doc["spike"], doc["criteria_version"])
    path = root / CRITERIA / name
    path.write_text("# test criteria\n" + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lock_lines(root: Path) -> list[str]:
    path = root / LOCK
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


# The disks of a run (FR-009), required in the records of S1 to S4 by spikectl verify (T037e).
DISKS = [
    {"serial": "pool0", "role": "pool", "size_mib": 1024, "filesystem": "btrfs"},
    {"serial": "pool1", "role": "pool", "size_mib": 1024, "filesystem": "btrfs"},
    {"serial": "arch0", "role": "archive", "size_mib": 640, "filesystem": "btrfs"},
    {"serial": "parity0", "role": "parity", "size_mib": 1280, "filesystem": "ext4"},
]


def record_doc(spike="S1", version=1, sha="0" * 64, outcomes=None, stamp="20261022T101500Z"):
    outcomes = outcomes or {f"{spike}.1": "pass", f"{spike}.2": "pass"}
    record_id = f"{spike}-{stamp}-v{version}"
    doc = {
        "record_id": record_id,
        "kind": "spike-run",
        "spike": spike,
        "criteria_version": version,
        "criteria_sha256": sha,
        "environment": {
            "repo_commit": "1" * 40,
            "flake_lock_sha256": "2" * 64,
            "nixpkgs_rev": "3" * 40,
            "profile": "small",
            "disks": [dict(disk) for disk in DISKS],
            "tool_versions": {"kernel": "6.18.56", "qemu": "10.2.4"},
            "host": {"cpu_class": "test", "vcpus": 2, "memory_gib": 8},
            "kvm": True,
        },
        "date": {"start": "2026-10-22T10:15:00Z", "end": "2026-10-22T10:45:00Z"},
        "scenarios": [{"id": sid, "outcome": out} for sid, out in outcomes.items()],
        "verdict": "PASS" if all(o == "pass" for o in outcomes.values()) else "FAIL",
        "residual_risk": ["device-level volatile caches are not emulated"],
        "evidence_archive": {"path": "evidence.tar.xz", "sha256": "0" * 64},
    }
    if spike == "X1":
        doc["gating"] = False
    if doc["verdict"] == "FAIL" and spike != "X1":
        doc["fallback_decision"] = "Adopt the documented fallback."
    return doc


S2_OUTCOMES = {f"S2.{n}": "pass" for n in range(1, 8)}


def review_doc(reviewed: dict, outcome="pass", stamp="20261023T120000Z", evidence=None):
    """A review record for `reviewed` (an S2 spike-run record dict)."""
    doc = {
        "record_id": f"review-S2-{stamp}-v{reviewed['criteria_version']}",
        "kind": "review",
        "spike": "S2",
        "criteria_version": reviewed["criteria_version"],
        "criteria_sha256": reviewed["criteria_sha256"],
        "reviewed_record": reviewed["record_id"],
        "review": {
            "scenario": "S2.7",
            "outcome": outcome,
            "reviewer": "maintainer",
            "date": "2026-10-23T12:00:00Z",
            "evidence": evidence or [{"path": "procedure.txt", "sha256": "b" * 64}],
        },
    }
    if outcome == "fail":
        doc["fallback_decision"] = "Adopt the documented fallback."
    return doc


def write_review(root: Path, doc: dict) -> Path:
    """Write a review record: record.json only, its evidence lives in the reviewed archive."""
    rec_dir = root / RESULTS / doc["spike"] / doc["record_id"]
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = rec_dir / "record.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def write_record(root: Path, doc: dict, evidence: bytes = b"evidence") -> Path:
    """Write record.json and an evidence archive stand-in whose hash the record names."""
    doc["evidence_archive"] = {"path": "evidence.tar.xz", "sha256": sha256_bytes(evidence)}
    rec_dir = root / RESULTS / doc["spike"] / doc["record_id"]
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "evidence.tar.xz").write_bytes(evidence)
    path = rec_dir / "record.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def init_repo(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")


def commit_all(root: Path, message: str = "c") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def xattrs_supported(path: Path) -> bool:
    """Whether the filesystem of `path` takes user xattrs (some build sandboxes do not)."""
    probe = path / ".xattr-probe"
    probe.write_bytes(b"")
    try:
        os.setxattr(probe, "user.probe", b"1")
        return True
    except OSError:
        return False
    finally:
        probe.unlink()
