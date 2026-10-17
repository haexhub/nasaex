"""Paths, lock file and schema access shared by the spikectl verbs.

Scaffolding for the Phase 0 spikes, not product code."""

import datetime
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

SPIKES = ("S1", "S2", "S3", "S4", "S5", "X1")
GATING_SPIKES = SPIKES[:5]

CRITERIA_DIR = Path("docs/phase0/criteria")
RESULTS_DIR = Path("docs/phase0/results")
LOCK_PATH = CRITERIA_DIR / "frozen.lock"
CONTRACTS_DIR = Path("specs/001-phase0-storage-spikes/contracts")

# Version 1 is S<n>.yaml, later versions are S<n>.v<k>.yaml with k >= 2.
_CRITERIA_NAME = re.compile(r"^(S[1-5]|X1)(?:\.v([2-9]|[1-9][0-9]+))?\.yaml$")
_RECORD_STAMP = re.compile(r"-(\d{8}T\d{6}Z)-v\d+$")


class ModelError(Exception):
    """A file of the verdict model is malformed."""


@dataclass(frozen=True)
class LockEntry:
    file: str
    version: int
    sha256: str

    def line(self) -> str:
        return f"{self.file} {self.version} {self.sha256}"


def criteria_filename(spike: str, version: int) -> str:
    return f"{spike}.yaml" if version == 1 else f"{spike}.v{version}.yaml"


def parse_criteria_filename(name: str) -> tuple[str, int] | None:
    match = _CRITERIA_NAME.match(name)
    if match is None:
        return None
    return match.group(1), int(match.group(2) or 1)


def record_timestamp(record_id: str) -> str:
    """Sortable UTC timestamp of a record id (basic format sorts lexicographically)."""
    match = _RECORD_STAMP.search(record_id)
    return match.group(1) if match else ""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_root(start: Path) -> Path:
    """Repository root of `start`, or `start` itself outside a git work tree."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else start


def parse_lock(text: str) -> list[LockEntry]:
    entries = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split(" ")
        valid = (
            len(fields) == 3
            and parse_criteria_filename(fields[0]) is not None
            and fields[1].isdigit()
            and re.fullmatch(r"[0-9a-f]{64}", fields[2]) is not None
        )
        if not valid:
            raise ModelError(f"{LOCK_PATH}:{number}: not '<file> <version> <sha256>'")
        entries.append(LockEntry(fields[0], int(fields[1]), fields[2]))
    return entries


def read_lock(root: Path) -> list[LockEntry]:
    path = root / LOCK_PATH
    return parse_lock(path.read_text(encoding="utf-8")) if path.exists() else []


def _jsonable(value):
    """YAML turns unquoted dates into date objects; the schemas expect strings."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def load_criteria(path: Path) -> dict:
    try:
        doc = _jsonable(yaml.safe_load(path.read_text(encoding="utf-8")))
    except yaml.YAMLError as error:
        raise ModelError(f"{path.name}: not valid YAML: {error}") from error
    if not isinstance(doc, dict):
        raise ModelError(f"{path.name}: top level must be a mapping")
    return doc


def load_record(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ModelError(f"{path.name}: not valid JSON: {error}") from error
    if not isinstance(doc, dict):
        raise ModelError(f"{path.name}: top level must be an object")
    return doc


def schema_errors(root: Path, schema_name: str, doc: dict) -> list[str]:
    """Human-readable schema violations of `doc`, empty when it validates."""
    schema = json.loads((root / CONTRACTS_DIR / schema_name).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]


def criteria_rule_errors(doc: dict) -> list[str]:
    """Rules on a schema-valid criteria document that criteria.schema.json does not carry yet
    (mirroring them there is a reviewed docs PR): S2 has S2.7 as its reviewed scenario, so S2
    cannot pass without a human judgement of the recovery procedure (finding U11)."""
    if doc["spike"] == "S2" and not any(
        s.get("id") == "S2.7" and s.get("review") is True and s.get("review_kind") == "review"
        for s in doc["scenarios"]
    ):
        return ["S2 needs scenario S2.7 with review: true and review_kind: review"]
    return []


def record_paths(root: Path) -> list[Path]:
    """Every docs/phase0/results/<group>/<record-id>/record.json."""
    return sorted((root / RESULTS_DIR).glob("*/*/record.json"))
