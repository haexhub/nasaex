"""Independent oracle: the invariants of FR-019 over manifests, never over tool output.

Expected state comes from the seeded fixtures (testlib.fixtures); observed state comes from
the share and the raw branches (testlib.manifest). The oracle has no input for mover logs,
mover reports or SnapRAID status, so it cannot trust them. Parity coverage is proven only by
the device-failure drill (invariant e), never by asking SnapRAID or the mover.

Invariants: (a) every live file is complete and correct through the share, (b) a missing pool
copy implies an archive copy with the expected hash, (c) no live file is missing and no
client-deleted file reappears, (d) no partial content is visible, (e) no live file and no
trash content is lost after a single device failure, (f) the content of every completed client
write exists in the pool or in the archive, (g) a client delete of a selected pool file ends
with the file hidden and its content in exactly one trash copy, or nowhere where the host
recorded that the delete removed the archive copy too (and the permitted state per phase of
such a vanished item, contracts/mover-steps.md). Violations are reported, never repaired."""

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from testlib.manifest import Item, Manifest


@dataclass(frozen=True)
class Violation:
    invariant: str  # a to g
    path: str
    detail: str


@dataclass(frozen=True)
class Observed:
    """What the harness read: the share as a client sees it, the pool branch, each archive
    branch and the trash directory of each archive disk (keyed like `archive`, paths relative
    to the trash directory). Manifests only."""

    share: Manifest
    pool: Manifest
    archive: Mapping[str, Manifest]
    trash: Mapping[str, Manifest] = field(default_factory=dict)


@dataclass(frozen=True)
class DeleteInjection:
    """The host's record of a client delete of a selected pool file through the share, taken
    right after the delete (never from the mover): the branches that still held the path, and
    the content of a later client write to the path (`inject:modify-archive-after-delete:M4`)."""

    path: str
    branches_after_delete: frozenset[str]
    write_after_delete_sha256: str | None = None


def _files(manifest: Manifest) -> dict[str, Item]:
    return {path: item for path, item in manifest.items() if item.kind == "file"}


def _same_content(want: Item, got: Item) -> bool:
    return (want.sha256, want.size) == (got.sha256, got.size)


def check_live_files(expected_live: Manifest, observed: Observed) -> list[Violation]:
    """(a) every live file present in the share has the expected content."""
    share = _files(observed.share)
    return [
        Violation(
            "a",
            path,
            f"expected sha256 {want.sha256}, size {want.size}; "
            f"observed sha256 {share[path].sha256}, size {share[path].size}",
        )
        for path, want in sorted(_files(expected_live).items())
        if path in share and not _same_content(want, share[path])
    ]


def check_pool_copy_coverage(
    expected_live: Manifest, observed: Observed, pool_origin: Collection[str]
) -> list[Violation]:
    """(b) a file that started on the pool and is gone from it has a correct archive copy."""
    violations = []
    pool = _files(observed.pool)
    expected = _files(expected_live)
    for path in sorted(pool_origin):
        if path in pool or path not in expected:
            continue
        copies = (_files(m).get(path) for m in observed.archive.values())
        if not any(c is not None and _same_content(expected[path], c) for c in copies):
            violations.append(
                Violation(
                    "b", path, "pool copy is gone and no archive copy has the expected content"
                )
            )
    return violations


def check_no_loss_or_resurrection(
    expected_live: Manifest, observed: Observed, deleted: Collection[str]
) -> list[Violation]:
    """(c) every live file is in the share; no client-deleted file is anywhere."""
    share = _files(observed.share)
    violations = [
        Violation("c", path, "live file is missing from the share")
        for path in sorted(_files(expected_live))
        if path not in share
    ]
    layers = {"share": share, "pool": _files(observed.pool)}
    layers |= {name: _files(m) for name, m in observed.archive.items()}
    for path in sorted(deleted):
        where = [name for name, files in layers.items() if path in files]
        if where:
            violations.append(
                Violation("c", path, f"client-deleted file reappears in {', '.join(where)}")
            )
    return violations


def check_no_partial_content(
    expected_live: Manifest,
    share: Manifest,
    acceptable: Mapping[str, Collection[str]] | None = None,
) -> list[Violation]:
    """(d) every visible live file holds a complete acceptable version: by default only the
    expected one, or complete old and complete new where a path lists both."""
    acceptable = acceptable or {}
    files = _files(share)
    expected = _files(expected_live)
    violations = []
    for path in sorted(expected.keys() | acceptable.keys()):
        if path not in files:
            continue
        allowed = set(acceptable.get(path, ())) or {expected[path].sha256}
        if files[path].sha256 not in allowed:
            violations.append(
                Violation(
                    "d", path, f"content {files[path].sha256} is no complete acceptable version"
                )
            )
    return violations


def check_survival_after_device_failure(
    expected_live: Manifest, restored: Manifest
) -> list[Violation]:
    """(e) after one device failure and recovery, every live file is back, bit-exact."""
    files = _files(restored)
    violations = []
    for path, want in sorted(_files(expected_live).items()):
        if path not in files:
            violations.append(Violation("e", path, "live file lost after the device failure"))
        elif not _same_content(want, files[path]):
            violations.append(Violation("e", path, "live file restored with other content"))
    return violations


def check_invariants(
    expected_live: Manifest,
    observed: Observed,
    deleted: Collection[str] = frozenset(),
    pool_origin: Collection[str] = frozenset(),
    acceptable: Mapping[str, Collection[str]] | None = None,
) -> list[Violation]:
    """Invariants (a) to (d) over one observation; (e) needs the drill's restored state."""
    return [
        *check_live_files(expected_live, observed),
        *check_pool_copy_coverage(expected_live, observed, pool_origin),
        *check_no_loss_or_resurrection(expected_live, observed, deleted),
        *check_no_partial_content(expected_live, observed.share, acceptable),
    ]


def _trash_copies(observed: Observed, path: str) -> list[tuple[str, str, Item]]:
    """(disk, trash name, item) of every trash entry of `path`, named
    `<pool-relative path>.<copy-time ctime ns>.<pool inode>` by the mover contract."""
    pattern = re.compile(re.escape(path) + r"\.\d+\.\d+")
    return [
        (disk, name, item)
        for disk, manifest in sorted(observed.trash.items())
        for name, item in sorted(_files(manifest).items())
        if pattern.fullmatch(name)
    ]


def check_trash_survival_after_device_failure(
    trash_before: Mapping[str, Manifest], restored: Observed
) -> list[Violation]:
    """(e) for trash content: every trash entry comes back on its disk with the same content,
    under its trash name or the archive path it was moved from (judged by content; the landing
    path is recorded by the check, not judged here)."""
    violations = []
    for disk, manifest in sorted(trash_before.items()):
        trash = _files(restored.trash.get(disk, {}))
        archive = _files(restored.archive.get(disk, {}))
        for name, want in sorted(_files(manifest).items()):
            former = re.sub(r"\.\d+\.\d+$", "", name)
            landed = [got for got in (trash.get(name), archive.get(former)) if got is not None]
            if not any(_same_content(want, got) for got in landed):
                violations.append(
                    Violation("e", name, f"trash content of {disk} lost after the device failure")
                )
    return violations


def check_completed_writes(completed: Mapping[str, str], observed: Observed) -> list[Violation]:
    """(f) the content (sha256) of every completed client write, keyed by the path written,
    exists under any path in the pool branch or an archive branch."""
    found = {item.sha256 for item in _files(observed.pool).values()}
    for manifest in observed.archive.values():
        found |= {item.sha256 for item in _files(manifest).values()}
    return [
        Violation("f", path, f"content {sha} of a completed write is neither in pool nor archive")
        for path, sha in sorted(completed.items())
        if sha not in found
    ]


# Phases of a vanished item (mover contract, permitted state per phase): from the delete to
# `mid:M5:trash-renamed`, between the two trash points, and from `mid:M5:trash-parent-synced`
# on, which includes `post:M5` and the state after the run that handled the delete.
VANISHED_PHASES = ("deleted", "trash-renamed", "trashed")


def check_client_delete(
    injection: DeleteInjection, copied: Item, observed: Observed, phase: str = "trashed"
) -> list[Violation]:
    """(g) a client delete of a selected pool file, judged against the host's injection log and
    the copy the mover made (`copied`, the fixture's expected item): the permitted state of the
    vanished item in `phase`; the default phase is the state after the run."""
    if phase not in VANISHED_PHASES:
        raise ValueError(f"unknown phase {phase!r}, expected one of {VANISHED_PHASES}")
    path = injection.path
    kept = injection.branches_after_delete & observed.archive.keys()
    in_archive = {
        name: files[path]
        for name, manifest in sorted(observed.archive.items())
        if path in (files := _files(manifest))
    }
    trashed = _trash_copies(observed, path)
    problems = []
    if injection.write_after_delete_sha256 is not None:
        problems += _write_after_delete(injection, kept, in_archive, trashed, observed)
    elif not kept:
        # A vanished item without archive copy: the client's delete removed every copy.
        problems += [f"{where} holds the path" for where in _holders(path, observed, in_archive)]
        problems += [
            f"trash copy {d}:{n} exists, the delete removed the archive copy" for d, n, _ in trashed
        ]
    else:
        problems += _vanished_with_archive_copy(phase, copied, kept, in_archive, trashed)
        if path in _files(observed.pool):
            problems.append("the pool branch holds the path of the vanished item")
        if phase == "trashed" and path in _files(observed.share):
            problems.append("the deleted file is visible through the share")
    return [Violation("g", path, problem) for problem in problems]


def _holders(path: str, observed: Observed, in_archive: Mapping[str, Item]) -> list[str]:
    layers = [("the share", observed.share), ("the pool branch", observed.pool)]
    return [name for name, m in layers if path in _files(m)] + sorted(in_archive)


def _vanished_with_archive_copy(
    phase: str,
    copied: Item,
    kept: Collection[str],
    in_archive: Mapping[str, Item],
    trashed: list[tuple[str, str, Item]],
) -> list[str]:
    if not any(_same_content(copied, item) for item in in_archive.values()) and not any(
        _same_content(copied, item) for _, _, item in trashed
    ):
        return [
            "the content of the vanished item exists nowhere: the mover deleted the archive copy"
        ]
    where = f"the copy-time content on {', '.join(sorted(kept))}"
    problems = [
        f"archive copy on {n} is not {where}"
        for n, item in in_archive.items()
        if n not in kept or not _same_content(copied, item)
    ]
    problems += [
        f"trash copy {d}:{n} is not {where}"
        for d, n, item in trashed
        if d not in kept or not _same_content(copied, item)
    ]
    if phase == "deleted" and trashed:
        problems.append("a trash copy exists before the trash move")
    if phase == "trashed":
        problems += [f"archive copy left on {n} after the trash move" for n in in_archive]
        if not trashed:
            problems.append("no trash copy exists")
    if len(trashed) > 1:
        problems.append(f"{len(trashed)} trash copies exist, expected exactly one")
    return problems


def _write_after_delete(
    injection: DeleteInjection,
    kept: Collection[str],
    in_archive: Mapping[str, Item],
    trashed: list[tuple[str, str, Item]],
    observed: Observed,
) -> list[str]:
    """`inject:modify-archive-after-delete:M4`: where the delete left the archive copy, the
    write changed it in place and it stays (`vanished-archive-changed`); where the delete
    removed it, the write is a new pool file (a changed item). Either way it stays visible and
    nothing is trashed."""
    sha = injection.write_after_delete_sha256
    path = injection.path
    problems = [f"trash copy {d}:{n} exists, the written file must stay" for d, n, _ in trashed]
    shown = _files(observed.share).get(path)
    if shown is None or shown.sha256 != sha:
        problems.append("the client's write is not visible through the share")
    holders = (
        [in_archive.get(n) for n in sorted(kept)] if kept else [_files(observed.pool).get(path)]
    )
    if not any(item is not None and item.sha256 == sha for item in holders):
        where = "archive copy left in place" if kept else "new pool file"
        problems.append(f"the {where} does not hold the client's write")
    return problems
