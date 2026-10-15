"""Pack the evidence archive of a result record: sanitised, deterministic, small.

Layout of the staging directory (anything else is left out):

    junit.xml, driver.log, environment.json, procedure.txt
    manifests/  snapraid/  status/  journal/       oracle manifests (hashes), tool logs
    serial/<scenario id>.log                       kept only for scenarios that did not pass

Disk images, file contents and serial logs of passing scenarios are excluded."""

import hashlib
import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from testlib.sanitiser import Sanitiser

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024  # target of plan open item O4
KEPT_FILES = ("junit.xml", "driver.log", "environment.json", "procedure.txt")
KEPT_DIRS = ("manifests", "snapraid", "status", "journal", "serial")
IMAGE_SUFFIXES = (".qcow2", ".img", ".raw", ".iso")


@dataclass
class EvidenceArchive:
    path: Path
    sha256: str
    size: int
    members: dict[str, str]  # member name -> sha256 of the stored (sanitised) bytes
    excluded: list[str] = field(default_factory=list)

    @property
    def within_target(self) -> bool:
        return self.size <= MAX_ARCHIVE_BYTES


def trim_log(text: str, max_lines: int) -> str:
    """Keep the head and the tail of a long log, with a marker for the gap."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = max_lines // 2
    tail = max_lines - head
    marker = f"[... {len(lines) - max_lines} lines trimmed ...]"
    return "\n".join([*lines[:head], marker, *lines[-tail:]])


def _keep(rel: str, passing: set[str]) -> bool:
    parts = rel.split("/")
    if rel.endswith(IMAGE_SUFFIXES):
        return False
    if len(parts) == 1:
        return rel in KEPT_FILES
    if parts[0] not in KEPT_DIRS:
        return False
    return not (parts[0] == "serial" and Path(rel).stem in passing)


def build_evidence(
    staging: Path, out_dir: Path, sanitiser: Sanitiser, passing: set[str]
) -> EvidenceArchive:
    """Write out_dir/evidence.tar.xz from the staging directory."""
    kept: dict[str, bytes] = {}
    excluded = []
    staging_root = staging.resolve()
    for path in sorted(staging.rglob("*")):
        rel = path.relative_to(staging).as_posix()
        if path.is_symlink():
            excluded.append(rel)
            continue
        if not path.is_file():
            continue
        try:
            source = path.resolve(strict=True)
            source.relative_to(staging_root)
        except (OSError, ValueError):
            excluded.append(rel)
            continue
        try:
            text = path.read_bytes().decode("utf-8") if _keep(rel, passing) else None
        except UnicodeDecodeError:
            text = None  # binary content is never evidence
        if text is None:
            excluded.append(rel)
        else:
            member = sanitiser.sanitise(rel)
            member_path = Path(member)
            if member_path.is_absolute() or ".." in member_path.parts:
                excluded.append(rel)
                continue
            if member in kept:
                suffix = 2
                while f"{member}~{suffix}" in kept:
                    suffix += 1
                member = f"{member}~{suffix}"
            kept[member] = sanitiser.sanitise(text).encode("utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / "evidence.tar.xz"
    with tarfile.open(archive_path, "w:xz", format=tarfile.GNU_FORMAT, preset=6) as tar:
        for name, data in sorted(kept.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    data = archive_path.read_bytes()
    return EvidenceArchive(
        path=archive_path,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        members={name: hashlib.sha256(blob).hexdigest() for name, blob in kept.items()},
        excluded=excluded,
    )
