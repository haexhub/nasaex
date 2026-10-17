"""Tree manifests: build from a directory, compare, hash, and move between host and guest.

Standard library only (this module also runs inside the guest). Comparison reports
differences; nothing here ever repairs a tree."""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

READ_SIZE = 1024 * 1024
# Compared in this order; a field whose expected value is None is not compared
# (directories have no size, mtime or hash; hashes may be left out of an expectation).
FIELDS = ("kind", "size", "mode", "mtime", "xattrs", "sha256")


@dataclass(frozen=True)
class Item:
    kind: str  # file, dir or other (symlinks, devices, ...)
    size: int | None
    mode: int
    mtime: int | None  # whole seconds
    xattrs: tuple[tuple[str, str], ...]  # user.* attributes as (name, hex value), sorted
    sha256: str | None


Manifest = dict[str, Item]


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str  # missing, unexpected or mismatch
    field: str | None = None
    expected: object = None
    observed: object = None


def _xattrs(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        names = sorted(
            n for n in os.listxattr(path, follow_symlinks=False) if n.startswith("user.")
        )
        return tuple((n, os.getxattr(path, n, follow_symlinks=False).hex()) for n in names)
    except OSError:
        return ()  # the filesystem has no user xattrs


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> Manifest:
    """Manifest of everything below `root` (not `root` itself), keyed by relative POSIX path."""
    manifest: Manifest = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = Path(directory) / name
            info = path.lstat()
            rel = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                manifest[rel] = Item("dir", None, mode, None, _xattrs(path), None)
            elif stat.S_ISREG(info.st_mode):
                item = Item(
                    "file",
                    info.st_size,
                    mode,
                    info.st_mtime_ns // 10**9,
                    _xattrs(path),
                    _sha256(path),
                )
                manifest[rel] = item
            else:
                manifest[rel] = Item("other", None, mode, None, (), None)
    return manifest


def compare(
    expected: Manifest, observed: Manifest, ignore: tuple[str, ...] = ()
) -> list[Difference]:
    """Differences between an expectation and an observation, sorted by path."""
    differences = []
    for path in sorted(expected.keys() | observed.keys()):
        if path not in observed:
            differences.append(Difference(path, "missing"))
        elif path not in expected:
            differences.append(Difference(path, "unexpected"))
        else:
            for field in FIELDS:
                want, got = getattr(expected[path], field), getattr(observed[path], field)
                if field not in ignore and want is not None and want != got:
                    differences.append(Difference(path, "mismatch", field, want, got))
    return differences


def subtree(manifest: Manifest, rel: str) -> Manifest:
    """The entries below directory `rel` (not `rel` itself), keyed relative to it."""
    if not rel:
        return dict(manifest)
    prefix = rel.rstrip("/") + "/"
    return {path[len(prefix) :]: item for path, item in manifest.items() if path.startswith(prefix)}


def describe(differences: list[Difference], limit: int | None = 20) -> str:
    """The differences in one line, for the note of a failed check; modes in octal."""

    def show(field: str | None, value: object) -> str:
        return oct(value) if field == "mode" and isinstance(value, int) else repr(value)

    parts = [
        f"{d.path} {d.kind}"
        if d.kind != "mismatch"
        else f"{d.path} {d.field} {show(d.field, d.expected)} -> {show(d.field, d.observed)}"
        for d in differences[:limit]
    ]
    rest = len(differences) - len(parts)
    return ", ".join(parts) + (f" (+{rest} more)" if rest > 0 else "")


def tree_hash(manifest: Manifest) -> str:
    """One hash over every path and every compared field of a branch's manifest."""
    digest = hashlib.sha256()
    for path in sorted(manifest):
        item = manifest[path]
        line = [path, item.kind, item.size, item.mode, item.mtime, item.xattrs, item.sha256]
        digest.update(json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n")
    return digest.hexdigest()


def to_json(manifest: Manifest) -> str:
    return json.dumps(
        {
            path: [i.kind, i.size, i.mode, i.mtime, [list(x) for x in i.xattrs], i.sha256]
            for path, i in sorted(manifest.items())
        },
        ensure_ascii=False,
    )


def from_json(text: str) -> Manifest:
    return {
        path: Item(kind, size, mode, mtime, tuple((n, v) for n, v in xattrs), sha256)
        for path, (kind, size, mode, mtime, xattrs, sha256) in json.loads(text).items()
    }
