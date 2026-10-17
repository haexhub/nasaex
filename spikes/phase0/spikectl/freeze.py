"""spikectl freeze: append one line to the append-only criteria lock.

Scaffolding for the Phase 0 spikes, not product code."""

import re
from pathlib import Path

from spikectl.model import (
    CRITERIA_DIR,
    LOCK_PATH,
    SPIKES,
    LockEntry,
    ModelError,
    criteria_rule_errors,
    load_criteria,
    parse_criteria_filename,
    read_lock,
    schema_errors,
    sha256_hex,
)

_DRAFT_STATUS = re.compile(r"^status:[ \t]*draft[ \t]*(#.*)?$", re.MULTILINE)


class FreezeError(Exception):
    """The criteria file cannot be frozen; nothing was changed."""


def _highest_version_file(root: Path, spike: str) -> tuple[int, Path]:
    found = {}
    for path in (root / CRITERIA_DIR).glob("*.yaml"):
        parsed = parse_criteria_filename(path.name)
        if parsed and parsed[0] == spike:
            found[parsed[1]] = path
    if not found:
        raise FreezeError(f"no criteria file for {spike} in {CRITERIA_DIR}")
    version = max(found)
    return version, found[version]


def freeze(root: Path, spike: str) -> LockEntry:
    """Freeze the newest criteria file of `spike`: set `status: frozen`, hash, append."""
    if spike not in SPIKES:
        raise FreezeError(f"unknown spike {spike!r}, expected one of {', '.join(SPIKES)}")
    version, path = _highest_version_file(root, spike)

    try:
        locked = read_lock(root)
    except ModelError as error:
        raise FreezeError(str(error)) from error
    if any(entry.file == path.name for entry in locked):
        raise FreezeError(f"{path.name} is already frozen")

    try:
        doc = load_criteria(path)
    except ModelError as error:
        raise FreezeError(str(error)) from error
    problems = schema_errors(root, "criteria.schema.json", doc)
    if problems:
        raise FreezeError(f"{path.name} fails the criteria schema: " + "; ".join(problems))
    rules = criteria_rule_errors(doc)
    if rules:
        raise FreezeError(f"{path.name}: " + "; ".join(rules))
    if doc["spike"] != spike or doc["criteria_version"] != version:
        raise FreezeError(
            f"{path.name} says {doc['spike']} version {doc['criteria_version']}, "
            f"its name says {spike} version {version}"
        )
    if doc["status"] != "draft":
        raise FreezeError(
            f"{path.name} has status {doc['status']!r} but no lock line; only a draft can be frozen"
        )
    if version >= 2 and not any(
        parse_criteria_filename(entry.file) == (spike, version - 1) for entry in locked
    ):
        raise FreezeError(f"earlier version {version - 1} of {spike} is not frozen")

    text, replaced = _DRAFT_STATUS.subn("status: frozen", path.read_text(encoding="utf-8"))
    if replaced != 1:
        raise FreezeError(f"{path.name} needs exactly one top-level 'status: draft' line")

    lock_file = root / LOCK_PATH
    previous = lock_file.read_text(encoding="utf-8") if lock_file.exists() else ""
    if previous and not previous.endswith("\n"):
        raise FreezeError(f"{LOCK_PATH} does not end with a newline")

    data = text.encode("utf-8")
    entry = LockEntry(path.name, version, sha256_hex(data))
    path.write_bytes(data)
    with lock_file.open("a", encoding="utf-8") as handle:
        handle.write(entry.line() + "\n")
    return entry
