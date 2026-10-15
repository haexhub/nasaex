"""Independent oracle: the invariants of FR-019 over manifests, never over tool output.

Expected state comes from the seeded fixtures (testlib.fixtures); observed state comes from
the share and the raw branches (testlib.manifest). The oracle has no input for mover logs,
mover reports or SnapRAID status, so it cannot trust them. Parity coverage is proven only by
the device-failure drill (invariant e), never by asking SnapRAID or the mover.

Invariants: (a) every live file is complete and correct through the share, (b) a missing pool
copy implies an archive copy with the expected hash, (c) no live file is missing and no
client-deleted file reappears, (d) no partial content is visible, (e) no live file is lost
after a single device failure. Violations are reported, never repaired."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from testlib.manifest import Item, Manifest


@dataclass(frozen=True)
class Violation:
    invariant: str  # a to e
    path: str
    detail: str


@dataclass(frozen=True)
class Observed:
    """What the harness read: the share as a client sees it, the pool branch, each archive
    branch. Manifests only."""

    share: Manifest
    pool: Manifest
    archive: Mapping[str, Manifest]


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
