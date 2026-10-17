"""Seeded fixtures: bytes and expected manifests derive from a seed and a description only.

The host computes expected manifests from the description; the guest runs `populate` from the
same description. Expected values therefore never come from guest storage (research D7).
Standard library only (this module also runs inside the guest). Large files are streamed in
chunks and never held in memory."""

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from testlib.manifest import Item, Manifest

CHUNK_SIZE = 1024 * 1024
FIXED_MTIME = 1_700_000_000
FILE_MODE = 0o644
DIR_MODE = 0o755


@dataclass(frozen=True)
class Entry:
    """One path of a fixture. `branch` says where it starts (`pool` or an archive branch)."""

    path: str  # relative POSIX path inside the share
    branch: str
    kind: str = "file"  # file, dir, or hardlink (to `link_to` on the same branch)
    size: int = 0
    mode: int | None = None
    mtime: int = FIXED_MTIME
    xattrs: tuple[tuple[str, str], ...] = ()
    # None: dense seeded bytes. Otherwise a sparse file: seeded bytes only inside these
    # (offset, length) extents, holes (zeros) everywhere else.
    extents: tuple[tuple[int, int], ...] | None = None
    link_to: str | None = None

    @property
    def effective_mode(self) -> int:
        if self.mode is not None:
            return self.mode
        return DIR_MODE if self.kind == "dir" else FILE_MODE


@dataclass(frozen=True)
class Fixture:
    seed: str
    entries: tuple[Entry, ...]


def _block(seed: str, path: str, index: int, length: int) -> bytes:
    return hashlib.shake_128(f"{seed}\0{path}\0{index}".encode()).digest(length)


def content_chunks(seed: str, entry: Entry) -> Iterator[bytes]:
    """The bytes of a file entry, in chunks of at most CHUNK_SIZE."""
    if entry.kind != "file":
        raise ValueError(f"{entry.path}: only file entries have content of their own")
    for index, start in enumerate(range(0, entry.size, CHUNK_SIZE)):
        length = min(CHUNK_SIZE, entry.size - start)
        if entry.extents is None:
            yield _block(seed, entry.path, index, length)
            continue
        window = (start, start + length)
        hit = [(max(o, window[0]), min(o + n, window[1])) for o, n in entry.extents]
        hit = [(lo, hi) for lo, hi in hit if lo < hi]
        if not hit:
            yield bytes(length)
            continue
        data = bytearray(length)
        dense = _block(seed, entry.path, index, length)
        for lo, hi in hit:
            data[lo - start : hi - start] = dense[lo - start : hi - start]
        yield bytes(data)


def content_sha256(seed: str, entry: Entry) -> str:
    digest = hashlib.sha256()
    for chunk in content_chunks(seed, entry):
        digest.update(chunk)
    return digest.hexdigest()


def flipped_sha256(seed: str, entry: Entry, offsets) -> str:
    """SHA-256 of the entry's content after inverting every bit of the byte at each offset
    (the expectation of an in-place change made through the share)."""
    for offset in offsets:
        if not 0 <= offset < entry.size:
            raise ValueError(f"{entry.path}: offset {offset} is outside {entry.size} bytes")
    digest = hashlib.sha256()
    start = 0
    for chunk in content_chunks(seed, entry):
        end = start + len(chunk)
        inside = [o - start for o in offsets if start <= o < end]
        if inside:
            data = bytearray(chunk)
            for index in inside:
                data[index] ^= 0xFF
            chunk = bytes(data)
        digest.update(chunk)
        start = end
    return digest.hexdigest()


def _hex_xattrs(xattrs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((name, value.encode().hex()) for name, value in xattrs))


def expected_manifest(
    fixture: Fixture, branches: tuple[str, ...] | None = None, with_hashes: bool = True
) -> Manifest:
    """Expected tree of the chosen branches (default: the union, as the share shows it)."""
    chosen = [e for e in fixture.entries if branches is None or e.branch in branches]
    manifest: Manifest = {}
    by_path = {(e.branch, e.path): e for e in chosen}
    for entry in chosen:
        for parent in list(PurePosixPath(entry.path).parents)[:-1]:
            manifest.setdefault(parent.as_posix(), Item("dir", None, DIR_MODE, None, (), None))
    for entry in chosen:
        source = entry
        if entry.kind == "hardlink":
            source = by_path[(entry.branch, entry.link_to)]
        if entry.kind == "dir":
            manifest[entry.path] = Item(
                "dir", None, entry.effective_mode, None, _hex_xattrs(entry.xattrs), None
            )
        else:
            sha = content_sha256(fixture.seed, source) if with_hashes else None
            manifest[entry.path] = Item(
                "file",
                source.size,
                source.effective_mode,
                source.mtime,
                _hex_xattrs(source.xattrs),
                sha,
            )
    return dict(sorted(manifest.items()))


def _write_file(seed: str, entry: Entry, target: Path) -> None:
    with target.open("wb") as handle:
        position = 0
        for chunk in content_chunks(seed, entry):
            if entry.extents is not None and not any(chunk):
                position += len(chunk)  # leave a hole instead of writing zeros
                handle.seek(position)
            else:
                handle.write(chunk)
                position += len(chunk)
        handle.truncate(entry.size)


def _finish(entry: Entry, target: Path) -> None:
    os.chmod(target, entry.effective_mode)
    for name, value in entry.xattrs:
        os.setxattr(target, name, value.encode())
    if entry.kind != "dir":
        os.utime(target, (entry.mtime, entry.mtime))


def populate(fixture: Fixture, roots: Mapping[str, Path]) -> None:
    """Create the fixture on the branch directories in `roots` (guest side)."""
    by_branch: dict[str, list[Entry]] = {}
    for entry in fixture.entries:
        by_branch.setdefault(entry.branch, []).append(entry)
    for branch, entries in by_branch.items():
        root = roots[branch]
        explicit_dirs = {e.path for e in entries if e.kind == "dir"}
        for entry in sorted(entries, key=lambda e: (e.kind == "hardlink", e.path)):
            target = root / entry.path
            for parent in reversed(list(PurePosixPath(entry.path).parents)[:-1]):
                directory = root / parent
                if not directory.exists():
                    directory.mkdir()
                    if parent.as_posix() not in explicit_dirs:
                        os.chmod(directory, DIR_MODE)
            if entry.kind == "dir":
                target.mkdir(exist_ok=True)
            elif entry.kind == "hardlink":
                os.link(root / entry.link_to, target)
            else:
                _write_file(fixture.seed, entry, target)
                _finish(entry, target)
        for entry in sorted((e for e in entries if e.kind == "dir"), key=lambda e: -len(e.path)):
            _finish(entry, root / entry.path)


def edge_cases(branch: str) -> tuple[Entry, ...]:
    """The edge-case set of the spec: zero-byte, long, non-ASCII, sparse, hard-linked,
    and directories that exist on this branch only."""
    return (
        Entry("edge/zero-byte.dat", branch, size=0),
        Entry("edge/" + "l" * 251 + ".txt", branch, size=33),
        Entry("edge/ünïcödé-文件-🙂.txt", branch, size=77),
        Entry(
            "edge/sparse.bin",
            branch,
            size=8 * CHUNK_SIZE,
            extents=((0, 4096), (4 * CHUNK_SIZE, 4096)),
        ),
        Entry("edge/hard-a.txt", branch, size=512),
        Entry("edge/hard-b.txt", branch, kind="hardlink", link_to="edge/hard-a.txt"),
        Entry("archive-only", branch, kind="dir"),
        Entry("archive-only/nested/file.txt", branch, size=2048),
    )


def to_json(fixture: Fixture) -> str:
    return json.dumps(asdict(fixture), ensure_ascii=False)


def from_json(text: str) -> Fixture:
    data = json.loads(text)
    entries = tuple(
        Entry(
            **{
                **e,
                "xattrs": tuple(tuple(x) for x in e["xattrs"]),
                "extents": None if e["extents"] is None else tuple(tuple(x) for x in e["extents"]),
            }
        )
        for e in data["entries"]
    )
    return Fixture(data["seed"], entries)
