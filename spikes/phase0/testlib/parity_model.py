"""SnapRAID block-allocation and recoverability model for S2 (tasks T058, T059).

Allocation (SnapRAID 14.4, measured by probe T057, vm-tests/probes/snapraid.results.md item 4a):
per sync and data disk, removed files (deleted or rewritten) free their positions first; then the
new and rewritten files, in the order of their FIEMAP physical start, each take the lowest free
positions from 0, filling holes and splitting a file across them (cmdline/scan.c:207-293,
1697-1718, 1755-1771). Parity position p combines the blocks at p of every data disk.

Recoverability with one parity level, per position and block state (spec User Story 2,
"Recoverability predictions"; Clarifications 2026-10-20, S2 SnapRAID probe). Each prediction
lists every file of the failed disks in two versions: `synced`, its content as of the last
completed sync (what `fix` restores), and `unsynced`, content written or changed on that disk
after it, which is never recoverable. Rules of a file prediction:
  intact     recoverable
  blocked    a position of the file cannot be rebuilt (`positions` names them)
  changed    S2.4: the interrupted sync scanned the file changed or deleted on the failed disk,
             so its content file no longer holds the synced version
  unsynced   unsynced content (`positions`: where the interrupted sync recorded it, if it did)
An added block over a position that was empty at the last completed sync is no change: the
manual's "newly added files don't prevent recovery of already existing files".

Standard library only. Throwaway scaffolding for the Phase 0 spikes, not product code."""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

# disk -> path -> parity positions of the file's blocks, in file order
Layout = Mapping[str, Mapping[str, Sequence[int]]]
# disk -> paths
Paths = Mapping[str, Collection[str]]

PROCESS_KILL = "process-kill"
DEVICE_FAILURE = "device-failure"


@dataclass(frozen=True)
class NewFile:
    """A file a sync adds to a disk (new, or rewritten and also listed as removed)."""

    path: str
    size: int  # bytes
    physical: int  # FIEMAP physical start of its first extent when the sync scans it


@dataclass(frozen=True)
class FilePrediction:
    disk: str
    path: str
    version: str  # "synced" or "unsynced"
    recoverable: bool
    rule: str  # intact, blocked, changed, unsynced
    positions: tuple[int, ...] = ()


@dataclass(frozen=True)
class Prediction:
    variant: str  # "plain", or "snapshot" (fix reads the surviving disks from snapshots)
    fault: str | None  # S2.4 only: process-kill or device-failure
    # True where the spec marks the rule UNVERIFIED for this fault (S2.4 device failure): the
    # process-kill evidence is no proof for it.
    provisional: bool
    files: tuple[FilePrediction, ...]
    beyond_parity: tuple[int, ...]  # positions the single parity level cannot rebuild


def allocate(
    layout: Mapping[str, Sequence[int]],
    removed: Collection[str],
    added: Iterable[NewFile],
    block_size: int,
) -> dict[str, tuple[int, ...]]:
    """One disk's layout after one sync: path -> positions."""
    out = {path: tuple(pos) for path, pos in layout.items() if path not in removed}
    occupied = {p for pos in out.values() for p in pos}
    free = 0
    for new in sorted(added, key=lambda f: f.physical):
        if new.path in out:
            raise ValueError(f"{new.path}: a rewritten file is removed before it is added again")
        positions = []
        for _ in range(-(-new.size // block_size)):
            while free in occupied:
                free += 1
            positions.append(free)
            occupied.add(free)
        out[new.path] = tuple(positions)
    return out


def after_sync(
    synced: Layout,
    failed: Iterable[str],
    changed: Paths | None = None,
    unsynced: Paths | None = None,
    *,
    snapshot: bool = False,
) -> Prediction:
    """Failure of the `failed` data disks after a completed sync (S2.1, S2.2, S2.3, S2.8, S2.9).

    `changed`: files of the last completed sync changed in place, rewritten or deleted since,
    per disk; `unsynced`: files written or rewritten on a failed disk since, always lost. Plain
    (S2.2): the synced version of a failed disk's file is recoverable iff at each of its
    positions at most one block is missing, counting the failed disks' blocks and the changed
    or deleted blocks of the surviving disks (two failed disks share the single parity level
    too, S2.8); a later change of the file itself does not matter, fix reverts it to the synced
    version (maintainer decision on S2.2, 2026-10-20). Snapshot (S2.9): every synced version is
    recoverable apart from the failed disks' own overlap, whatever changed afterwards."""
    failed = _known(synced, failed)
    changed = {} if snapshot else (changed or {})
    missing = Counter(
        p
        for disk, files in synced.items()
        for path, positions in files.items()
        if disk in failed or path in changed.get(disk, ())
        for p in positions
    )
    files = []
    for disk in failed:
        for path, positions in synced[disk].items():
            files.append(_judged(disk, path, [p for p in positions if missing[p] > 1]))
        files += _unsynced(disk, (unsynced or {}).get(disk, ()), {})
    return _prediction(snapshot, None, files)


def after_interrupted_sync(
    synced: Layout,
    scanned: Layout,
    failed: str,
    boundary: int,
    changed: Paths,
    unsynced: Paths,
    changed_after: Paths | None = None,
    *,
    snapshot: bool = False,
    fault: str = PROCESS_KILL,
) -> Prediction:
    """Failure of one data disk after a sync was interrupted (S2.4).

    `scanned` is the layout the interrupted sync recorded (its content file is written at its
    start only), `boundary` the first position it did not process (from the parity file),
    `changed` the synced files changed or deleted before it scanned, `changed_after` those
    changed after it stopped, `unsynced` the failed disk's files written or rewritten since the
    last completed sync. A synced file of the failed disk is recoverable iff the interrupted
    sync scanned it unchanged (a change after it stopped is reverted by fix, as in S2.2) and,
    at each of its positions, every surviving disk's block is unchanged since the last
    completed sync or the position was processed with the surviving block present (still as
    the interrupted sync scanned it). The snapshot variant reads the surviving disks from the
    interrupted sync's snapshots, so changes after it stopped do not count there."""
    if fault not in (PROCESS_KILL, DEVICE_FAILURE):
        raise ValueError(f"{fault}: the S2.4 faults are {PROCESS_KILL} and {DEVICE_FAILURE}")
    _known(synced, [failed])
    seen_after = {} if snapshot else (changed_after or {})
    old, new = _owners(synced, failed), _owners(scanned, failed)

    def rebuilt(p: int) -> bool:
        parity_old = all(
            path not in changed.get(disk, ()) and path not in seen_after.get(disk, ())
            for disk, path in old.get(p, ())
        )
        parity_new = p < boundary and all(
            path not in seen_after.get(disk, ()) for disk, path in new.get(p, ())
        )
        return parity_old or parity_new

    files = []
    for path, positions in synced[failed].items():
        if path in changed.get(failed, ()):
            files.append(FilePrediction(failed, path, "synced", False, "changed"))
        else:
            files.append(_judged(failed, path, [p for p in positions if not rebuilt(p)]))
    files += _unsynced(failed, unsynced.get(failed, ()), scanned.get(failed, {}))
    return _prediction(snapshot, fault, files)


def _known(layout: Layout, disks: Iterable[str]) -> tuple[str, ...]:
    disks = tuple(disks)
    unknown = [disk for disk in disks if disk not in layout]
    if unknown:
        raise ValueError(f"{', '.join(unknown)}: not a data disk of the layout")
    return disks


def _owners(layout: Layout, failed: str) -> dict[int, list[tuple[str, str]]]:
    """position -> (disk, path) of every surviving disk's block there."""
    out: dict[int, list[tuple[str, str]]] = {}
    for disk, files in layout.items():
        if disk != failed:
            for path, positions in files.items():
                for p in positions:
                    out.setdefault(p, []).append((disk, path))
    return out


def _judged(disk: str, path: str, blocked: list[int]) -> FilePrediction:
    if blocked:
        return FilePrediction(disk, path, "synced", False, "blocked", tuple(blocked))
    return FilePrediction(disk, path, "synced", True, "intact")


def _unsynced(
    disk: str, paths: Iterable[str], scanned: Mapping[str, Sequence[int]]
) -> list[FilePrediction]:
    return [
        FilePrediction(disk, path, "unsynced", False, "unsynced", tuple(scanned.get(path, ())))
        for path in paths
    ]


def _prediction(snapshot: bool, fault: str | None, files: list[FilePrediction]) -> Prediction:
    blocked = sorted({p for f in files if f.rule == "blocked" for p in f.positions})
    return Prediction(
        variant="snapshot" if snapshot else "plain",
        fault=fault,
        provisional=fault == DEVICE_FAILURE,
        files=tuple(files),
        beyond_parity=tuple(blocked),
    )
