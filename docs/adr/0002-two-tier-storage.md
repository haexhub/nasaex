# ADR-0002: Two-tier storage

Status: proposed, pending spikes S1 to S4 · Date: 2026-10-15

## Context

SnapRAID with mergerfs pools disks of different sizes, but parity is updated by a sync
run. Data written since the last sync is unprotected, a sync after a disk failure
destroys recoverability, and changes on other disks reduce what can be recovered.
Single-disk failure must not cause data loss.

## Decision

Two tiers, built on the tiered-cache pattern documented by mergerfs.

- A btrfs RAID1 pool takes all new and changing data.
- A SnapRAID archive holds cold data. Every archive disk is its own btrfs filesystem, so
  that `snapraid sync` and `snapraid fix` can run against read-only snapshots of the data
  disks. Changes to archived files after the last sync then do not destroy
  recoverability.
- Two mergerfs pools present both tiers as one share. The base pool contains only the
  archive disks. The cache pool lists the pool branch first and the archive disks after
  it, with the archive branches set to no-create; shares use the cache pool. New files land
  on the pool. An archived file is read, modified and deleted in place on its archive
  disk. There is no overlayfs, no copy-up and no whiteout.
- The mover follows the documented pattern (move files from the cache branch into the base
  pool, on a schedule) with the project's safety rule: copy with a checksum and keep the
  source, run and verify `snapraid sync`, and only then delete the pool copy. The stock
  `--remove-source-files` behaviour is not acceptable.
- If a client deletes a selected pool file after the mover copied it, the mover keeps the
  archive copy and, after parity coverage, renames it into a trash directory beside the
  archive branch on the same data subvolume. The trash is outside both mergerfs pools, is
  never purged in Phase 0, and its exclusion from the pool views and recovery behaviour
  remain subject to the S1/S3 spikes.
- In the degraded state, sync, scrub and the mover are locked and the archive is
  read-only.

Variant B, overlayfs with the archive as a read-only lower layer and a pool subvolume as
the upper layer, is not part of this decision. It is kept as the optional experiment X1 of
the Phase 0 spikes. X1 never gates this ADR; its result only decides whether a copy-up
enhancement is worth adding later.

## Alternatives

- **SnapRAID only**: leaves an unprotected window and the failure hazards above.
- **btrfs RAID1 or ZFS for everything**: simple and real-time safe, but costs about half
  the capacity and drops the mixed-size parity model.
- **Own real-time parity**: no mature open-source implementation; high risk of data loss.
- **overlayfs on top of mergerfs** (the previous proposal), rejected for the baseline and
  kept as experiment X1. It would keep the archive read-only for clients, but it is not an
  established setup:
  - the mergerfs README lists "Read/write overlay on top of read-only filesystem like
    OverlayFS" under its non-features, and the mergerfs FAQ recommends the opposite
    direction (an overlayfs mount as a branch inside the pool);
  - upstream documents no overlayfs on top of mergerfs;
  - the kernel documents changes to the underlying layers of a mounted overlay as
    undefined behaviour, and the mover changes both the archive and the pool layer while
    the share is mounted.
- **Stock mover scripts** (`mergerfs.time-based-mover`, `mergerfs.percent-full-mover`):
  they use `find` and `rsync --remove-source-files`, which deletes the source right after
  the copy, before parity covers the archive copy. Rejected; the project mover keeps the
  source until a sync is verified.

## Consequences

- At least two pool devices are required.
- Archive data disks must be btrfs (a change from XFS or ext4), and the snapshot workflow
  (`snapraid-btrfs`, or the native snapshot option announced for SnapRAID 15.0, a release
  candidate that has not been verified in practice) becomes part of the archive. The choice
  is an open item of the plan; the default is external tooling at the pinned SnapRAID 14.x.
- Archived files are changed in place. Changes since the last sync form an unprotected
  window that the snapshot workflow is meant to close and that spike S2 measures. A file on
  the failed disk that was added or changed after the last sync is not recoverable.
- A full pool must not spill into the archive: the archive branches are no-create and
  mergerfs `moveonenospc` is off in the cache pool (to be confirmed by S1).
- A client delete of a selected pool file has a measured visibility window until the
  trash rename. A crash before that rename is durable may leave the stale archive copy
  visible on retry; this is an explicit S3 failure condition and requires a reviewed
  correction before the tiered model can be accepted.
- The mover is the critical component and needs crash-safety tests.
- The decision is only accepted when the spikes show that the tiered-cache share works with
  Samba, renames and large files (S1), that the SnapRAID failure and snapshot behaviour
  matches the assumptions (S2), that the mover is crash safe (S3) and that the degraded
  modes hold (S4). Otherwise the fallback is direct archive access, read-only when
  degraded.

## Sources

- mergerfs, `mkdocs/docs/extended_usage_patterns.md`, section "tiered cache", and the
  scripts `tools/mergerfs.time-based-mover` and `tools/mergerfs.percent-full-mover`;
  `README.md` (non-features); repository `trapexit/mergerfs`, tag `2.41.1`, commit
  `960f5fdbba5e053eb4a328b409cda0d9dd2e0d48`.
- `snapraid-btrfs`, repository `automorphism88/snapraid-btrfs`, commit
  `6492a45ad55c389c0301075dcc8bc8784ef3e274` (README, FAQ).
- `btrfssnapraid`, repository `dim-geo/btrfssnapraid`, commit
  `a401621928e00b8d2d2c3f19c724fa46b58c5c38` (README).
- SnapRAID 15.0 release candidate, repository `amadvance/snapraid`, tag `v15.0rc2` (tag
  object `1f6c7970f58a5d299ea1fb9069977646808d1855`), release notes (option `snapshot`).
- Kernel overlayfs documentation, `Documentation/filesystems/overlayfs.rst`, repository
  `torvalds/linux`, tag `v6.18` (tag object `f7b88edb52c8dd01b7e576390d658ae6eef0e134`).
- Details and verification status: `specs/001-phase0-storage-spikes/research.md`, sections
  0 and 7.
