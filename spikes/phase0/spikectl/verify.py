"""spikectl verify: mechanical checks behind "criteria first" and "amendment voids the verdict".

Scaffolding for the Phase 0 spikes, not product code. Check groups:

- criteria: schema, lock hashes, frozen status matches the lock, S2 has the reviewed S2.7
- records: schema, record-to-lock pairing, layout, evidence hash, disks of S1 to S4 runs; a
  review record (S2.7) has no archive of its own and is checked against the record it reviews
- git: append-only lock history, criteria commit before record commit, reviewed record before
  its review (needs a git work tree)
- hygiene: host paths, user and host names, authoring-tool self-references, spikes/ references
"""

import getpass
import re
import socket
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from spikectl.model import (
    CRITERIA_DIR,
    LOCK_PATH,
    LockEntry,
    ModelError,
    criteria_rule_errors,
    load_criteria,
    load_record,
    parse_criteria_filename,
    read_lock,
    record_paths,
    record_rule_errors,
    schema_errors,
    sha256_hex,
)
from testlib.sanitiser import HOST_PATH_PATTERNS, name_pattern

GROUPS = ("criteria", "records", "git", "hygiene")

# Documents whose text must be free of host-specific data and tool self-references.
SCANNED_PREFIXES = ("docs/", "specs/")
# Places that may mention spikes/: the scaffolding itself, decision artifacts, the feature's
# documents, spec tooling and the Phase 0 CI workflow (plan, "Structure Decision").
SPIKES_REFERENCE_ALLOWED = (
    "spikes/",
    "docs/",
    "specs/",
    ".specify/",
    ".claude/",
    ".agents/",
    ".spaex/",
    ".github/workflows/phase0.yml",
)
# Generic account and host names that are words in prose (CI users, VM host names).
GENERIC_NAMES = {"runner", "root", "localhost", "nixos", "ubuntu", "github", "build"}

SELF_REFERENCE_PATTERNS = (
    re.compile(r"\bClaude\b"),
    re.compile(r"\bAnthropic\b"),
    re.compile(r"\bChatGPT\b"),
    re.compile(r"\bGitHub Copilot\b"),
    re.compile(r"(?i)\bco-authored-by\b"),
    re.compile(r"\bGenerated with\b"),
)
SPIKES_REFERENCE = re.compile(r"(?<![\w.-])spikes/")
MAX_TEXT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    check: str
    message: str


def machine_names() -> tuple[str, ...]:
    """User and short host name of this machine, minus generic words."""
    names = {getpass.getuser(), socket.gethostname().split(".")[0]}
    return tuple(sorted(n for n in names if len(n) >= 4 and n.lower() not in GENERIC_NAMES))


def verify(
    root: Path,
    only: tuple[str, ...] | None = None,
    forbidden_names: tuple[str, ...] | None = None,
    base_ref: str | None = None,
) -> list[Finding]:
    groups = only or GROUPS
    unknown = set(groups) - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown check group(s): {', '.join(sorted(unknown))}")
    names = machine_names() if forbidden_names is None else forbidden_names
    findings = []
    if "criteria" in groups:
        findings += _check_criteria(root)
    if "records" in groups:
        findings += _check_records(root)
    if "git" in groups:
        findings += _check_git(root, base_ref)
    if "hygiene" in groups:
        findings += _check_hygiene(root, names)
    return findings


# --- criteria ---------------------------------------------------------------


def _check_criteria(root: Path) -> list[Finding]:
    findings = []
    criteria_dir = root / CRITERIA_DIR
    try:
        lock = read_lock(root)
    except ModelError as error:
        return [Finding("lock-format", str(error))]

    locked = {}
    for entry in lock:
        if entry.file in locked:
            findings.append(Finding("lock-format", f"{entry.file} appears twice in the lock"))
        locked[entry.file] = entry
        path = criteria_dir / entry.file
        if not path.exists():
            findings.append(Finding("lock-hash", f"{entry.file} is locked but does not exist"))
        elif sha256_hex(path.read_bytes()) != entry.sha256:
            findings.append(Finding("lock-hash", f"{entry.file} differs from its lock line"))

    for path in sorted(criteria_dir.glob("*.y*ml")):
        parsed = parse_criteria_filename(path.name)
        if parsed is None:
            findings.append(
                Finding("criteria-name", f"{path.name} is not S<n>.yaml or S<n>.v<k>.yaml")
            )
            continue
        findings += _check_criteria_file(root, path, parsed, locked)
    return findings


def _check_criteria_file(root, path, parsed, locked) -> list[Finding]:
    try:
        doc = load_criteria(path)
    except ModelError as error:
        return [Finding("criteria-schema", str(error))]
    problems = [
        Finding("criteria-schema", f"{path.name}: {p}")
        for p in schema_errors(root, "criteria.schema.json", doc)
    ]
    if problems:
        return problems
    findings = [Finding("criteria-review", f"{path.name}: {p}") for p in criteria_rule_errors(doc)]
    if (doc["spike"], doc["criteria_version"]) != parsed:
        findings.append(
            Finding("criteria-schema", f"{path.name}: spike or version disagree with the name")
        )
    if doc["status"] == "frozen" and path.name not in locked:
        findings.append(Finding("lock-missing", f"{path.name} says frozen but has no lock line"))
    if path.name in locked and doc["status"] != "frozen":
        findings.append(
            Finding("lock-status", f"{path.name} is locked but its status is {doc['status']}")
        )
    return findings


# --- records ----------------------------------------------------------------


def _check_records(root: Path) -> list[Finding]:
    try:
        lock = read_lock(root)
    except ModelError as error:
        return [Finding("lock-format", str(error))]
    findings = []
    for path in record_paths(root):
        findings += _check_record(root, path, lock)
    return findings


def _check_record(root: Path, path: Path, lock: list[LockEntry]) -> list[Finding]:
    rel = path.relative_to(root)
    try:
        record = load_record(path)
    except ModelError as error:
        return [Finding("record-schema", f"{rel}: {error}")]
    problems = schema_errors(root, "result-record.schema.json", record)
    if problems:
        return [Finding("record-schema", f"{rel}: {p}") for p in problems]

    findings = [Finding("record-disks", f"{rel}: {p}") for p in record_rule_errors(record)]
    if path.parent.name != record["record_id"]:
        findings.append(
            Finding("record-layout", f"{rel}: directory is not named {record['record_id']}")
        )
    pair = (record["spike"], record["criteria_version"], record["criteria_sha256"])
    if not any((parse_criteria_filename(e.file)[0], e.version, e.sha256) == pair for e in lock):
        findings.append(
            Finding("record-lock", f"{rel}: {pair[0]} v{pair[1]} with this hash is not in the lock")
        )
    if record["kind"] == "review":
        return findings + _check_review(root, path, record)
    archive = path.parent / record["evidence_archive"]["path"]
    if (
        not archive.exists()
        or sha256_hex(archive.read_bytes()) != record["evidence_archive"]["sha256"]
    ):
        findings.append(
            Finding("record-evidence", f"{rel}: evidence archive missing or hash differs")
        )
    return findings


def _reviewed_path(path: Path, record: dict) -> Path:
    """record.json of the record a review names (reviews sit in the same group directory)."""
    return path.parent.parent / record["reviewed_record"] / "record.json"


def _archive_member_sha256(archive: Path, name: str) -> str | None:
    try:
        with tarfile.open(archive, mode="r:*") as tar:
            extracted = tar.extractfile(tar.getmember(name))
            return sha256_hex(extracted.read()) if extracted else None
    except (KeyError, tarfile.TarError, EOFError, OSError):
        return None


def _check_review(root: Path, path: Path, record: dict) -> list[Finding]:
    """A review is judged against the record it reviews: same criteria version and hash, and
    its evidence entries are pinned entries of that record's archive."""
    rel = path.relative_to(root)
    reviewed_path = _reviewed_path(path, record)
    try:
        reviewed = load_record(reviewed_path)
    except (ModelError, OSError):
        return [
            Finding(
                "record-review", f"{rel}: reviewed record {record['reviewed_record']} not found"
            )
        ]
    if reviewed.get("kind") != "spike-run" or reviewed.get("spike") != "S2":
        return [
            Finding(
                "record-review",
                f"{rel}: reviewed record {record['reviewed_record']} is not an S2 spike-run",
            )
        ]
    pair = ("criteria_version", "criteria_sha256")
    if any(reviewed.get(key) != record[key] for key in pair):
        return [
            Finding(
                "record-review",
                f"{rel}: criteria version or hash differ from the reviewed record "
                f"{record['reviewed_record']}",
            )
        ]
    archive = reviewed_path.parent / reviewed.get("evidence_archive", {}).get("path", "")
    return [
        Finding(
            "record-evidence",
            f"{rel}: evidence {entry['path']} is not in the archive of "
            f"{record['reviewed_record']} or its hash differs",
        )
        for entry in record["review"]["evidence"]
        if _archive_member_sha256(archive, entry["path"]) != entry["sha256"]
    ]


# --- git history ------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _lock_lines_at(root: Path, commit: str) -> list[str]:
    shown = _git(root, "show", f"{commit}:{LOCK_PATH}")
    return shown.stdout.splitlines() if shown.returncode == 0 else []


def _is_prefix(older: list[str], newer: list[str]) -> bool:
    return newer[: len(older)] == older


def _check_git(root: Path, base_ref: str | None) -> list[Finding]:
    membership = _git(root, "rev-parse", "--is-inside-work-tree")
    if membership.returncode != 0:
        # A standalone tree is valid for the criteria/records/hygiene checks.  A
        # broken repository marker, however, must not silently skip git checks.
        if (root / ".git").exists():
            return [Finding("git", f"cannot inspect git history from {root}")]
        return []
    findings = _check_lock_append_only(root, base_ref)
    return findings + _check_criteria_before_records(root)


def _check_lock_append_only(root: Path, base_ref: str | None) -> list[Finding]:
    history = _git(root, "log", "--reverse", "--format=%H", "--", str(LOCK_PATH))
    commits = history.stdout.split() if history.returncode == 0 else []
    states = [(commit[:12], _lock_lines_at(root, commit)) for commit in commits]
    lock_file = root / LOCK_PATH
    current = lock_file.read_text(encoding="utf-8").splitlines() if lock_file.exists() else []
    states.append(("working tree", current))

    findings = []
    for (before_name, before), (after_name, after) in zip(states, states[1:], strict=False):
        if not _is_prefix(before, after):
            findings.append(
                Finding(
                    "lock-append-only",
                    f"{LOCK_PATH} changed from {before_name} to {after_name} "
                    "other than by appending lines",
                )
            )
    if base_ref is not None:
        if _git(root, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}").returncode != 0:
            findings.append(Finding("lock-append-only", f"base ref {base_ref} does not exist"))
        elif not _is_prefix(_lock_lines_at(root, base_ref), current):
            findings.append(
                Finding("lock-append-only", f"{LOCK_PATH} does not extend the lock at {base_ref}")
            )
    return findings


def _first_commit(root: Path, *log_args: str) -> str | None:
    log = _git(root, "log", "--reverse", "--format=%H", *log_args)
    commits = log.stdout.split() if log.returncode == 0 else []
    return commits[0] if commits else None


def _check_criteria_before_records(root: Path) -> list[Finding]:
    try:
        lock = read_lock(root)
        findings = []
        for path in record_paths(root):
            findings += _check_record_order(root, path, lock)
    except ModelError:
        return []  # reported by the criteria and records groups
    return findings


def _check_record_order(root: Path, path: Path, lock: list[LockEntry]) -> list[Finding]:
    rel = path.relative_to(root)
    try:
        record = load_record(path)
    except ModelError:
        return []
    entry = next(
        (
            e
            for e in lock
            if e.sha256 == record.get("criteria_sha256")
            and e.version == record.get("criteria_version")
        ),
        None,
    )
    if entry is None:
        return []  # reported as record-lock

    record_commit = _first_commit(root, "--diff-filter=A", "--", str(rel))
    if record_commit is None:
        return [
            Finding(
                "ancestry", f"{rel} is not committed, so criteria-before-record cannot be shown"
            )
        ]
    lock_commit = _first_commit(root, f"-S{entry.line()}", "--", str(LOCK_PATH))
    if lock_commit is None:
        return [Finding("ancestry", f"the lock line of {entry.file} is not committed")]
    if lock_commit == record_commit:
        return [Finding("ancestry", f"{rel} and the lock line of {entry.file} are in one commit")]
    ancestor = _git(root, "merge-base", "--is-ancestor", lock_commit, record_commit)
    if ancestor.returncode != 0:
        return [Finding("ancestry", f"the lock commit of {entry.file} is not an ancestor of {rel}")]
    if record.get("kind") == "review":
        return _check_review_order(root, path, record, record_commit)
    return []


def _check_review_order(root: Path, path: Path, record: dict, review_commit: str) -> list[Finding]:
    """The reviewed record is committed before its review (a review is a later, additive PR)."""
    rel = path.relative_to(root)
    reviewed_rel = _reviewed_path(path, record).relative_to(root)
    reviewed_commit = _first_commit(root, "--diff-filter=A", "--", str(reviewed_rel))
    if reviewed_commit is None:
        return []  # a missing record is reported as record-review, an uncommitted one as ancestry
    if reviewed_commit == review_commit:
        return [Finding("ancestry", f"{rel} and the record it reviews are in one commit")]
    ancestor = _git(root, "merge-base", "--is-ancestor", reviewed_commit, review_commit)
    if ancestor.returncode != 0:
        return [
            Finding(
                "ancestry", f"the commit of the record reviewed by {rel} is not an ancestor of it"
            )
        ]
    return []


# --- hygiene ----------------------------------------------------------------


def _tracked_files(root: Path) -> list[str]:
    """Repository-relative paths: tracked files in a git work tree, else everything."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode == 0:
        listed = _git(root, "ls-files", "-z")
        return sorted(p for p in listed.stdout.split("\0") if p)
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def _text(data: bytes) -> str | None:
    if len(data) > MAX_TEXT_BYTES:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _evidence_texts(path: Path):
    """Text members of an evidence archive as (member name, text)."""
    try:
        with tarfile.open(path, mode="r:*") as tar:
            for member in tar:
                extracted = tar.extractfile(member) if member.isfile() else None
                text = _text(extracted.read()) if extracted else None
                if text is not None:
                    yield member.name, text
    except (tarfile.TarError, EOFError, OSError):
        # Not a readable archive: the records group reports a hash or layout problem if it matters.
        return


def _scan(label: str, text: str, patterns, check: str, what: str) -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            if pattern.search(line):
                findings.append(Finding(check, f"{label}:{number}: {what}"))
                break
    return findings


def _check_hygiene(root: Path, forbidden_names: tuple[str, ...]) -> list[Finding]:
    name_patterns = tuple(name_pattern(n) for n in forbidden_names)
    findings = []
    for rel in _tracked_files(root):
        path = root / rel
        if rel.startswith(SCANNED_PREFIXES):
            if rel.endswith(".tar.xz"):
                texts = [(f"{rel}!{name}", text) for name, text in _evidence_texts(path)]
            else:
                text = _text(path.read_bytes())
                texts = [(rel, text)] if text is not None else []
            for label, text in texts:
                findings += _scan(
                    label, text, HOST_PATH_PATTERNS, "host-paths", "absolute host path"
                )
                for forbidden in name_patterns:
                    findings += _scan(label, text, (forbidden,), "host-paths", "user or host name")
                findings += _scan(
                    label,
                    text,
                    SELF_REFERENCE_PATTERNS,
                    "self-references",
                    "authoring-tool reference",
                )
        if not rel.startswith(SPIKES_REFERENCE_ALLOWED):
            text = _text(path.read_bytes())
            if text is not None:
                findings += _scan(
                    rel, text, (SPIKES_REFERENCE,), "spikes-references", "references spikes/"
                )
    return findings
