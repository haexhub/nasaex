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

A ZFS mirror as the fast pool is not part of this decision either. It is the optional
experiment X2 of the Phase 0 spikes (see Alternatives). X2 never gates this ADR; btrfs RAID1
stays the pool until X2 shows an advantage and a separate reviewed amendment of this ADR
adopts the change.

## Alternatives

- **SnapRAID only**: leaves an unprotected window and the failure hazards above.
- **btrfs RAID1 for everything**: simple and real-time safe, but costs about half the
  capacity and drops the mixed-size parity model.
- **ZFS**, judged per tier:
  - **ZFS for the archive tier** (instead of SnapRAID over single btrfs disks): not adopted.
    A mirror or RAIDZ group only uses the capacity that all its members share: a replacing
    device must be at least as large as the smallest device of the group, and new space
    becomes available only after every device of the group is expanded. RAIDZ expansion
    (OpenZFS 2.3) attaches a disk to an existing RAIDZ group, but old blocks keep their old
    data-to-parity ratio and only new blocks use the new width, and the parity level cannot
    be raised ("you can't turn a RAIDZ1 into a RAIDZ2"). The expansion uses the pool feature
    `raidz_expansion`, which is not read-only compatible, so once it is active the pool can
    only be imported by an OpenZFS that supports it, that is 2.3 or later. One single-disk
    pool per disk keeps mixed sizes but has no parity. Neither replaces the mixed-size
    parity model of SnapRAID. SnapRAID `v15.0rc2` names ZFS among the filesystems of its
    native `snapshot` option, but the pin carries SnapRAID 14.4 and that option is
    unverified (`specs/001-phase0-storage-spikes/research.md`, section 7.3).
  - **ZFS mirror for the fast pool** (instead of btrfs RAID1): an open question, not decided.
    It is measured by the optional, non-gating experiment X2 of the Phase 0 spikes, which
    compares a ZFS mirror with the btrfs RAID1 pool under the same tiered-cache setup
    (filesystem behaviour behind the share, a device failure and replacement, adding and
    replacing capacity, ARC memory use, the kernel pin). ZFS is an out-of-tree kernel module,
    and the NixOS wiki notes that "ZFS often does not support the latest Kernel versions" and
    that the ZFS package then fails to evaluate as "broken"; at the Phase 0 pin the module
    evaluates for the default kernel (spec, User Story 8). btrfs RAID1 stays the baseline
    until X2 shows an advantage and a separate reviewed amendment of this ADR adopts it.
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
- OpenZFS man pages `man/man8/zpool-attach.8` (RAIDZ expansion, old blocks keep their
  data-to-parity ratio), `man/man8/zpool-replace.8` (a new device must be at least the minimum
  size of the group), `man/man7/zpoolprops.7` (`autoexpand`), `man/man7/zpool-features.7`
  (feature states, `raidz_expansion` not read-only compatible), repository `openzfs/zfs`, tag
  `zfs-2.3.0` (tag object `10e3887c843a7f9bbec407172c71de7c59ce4b6b`, commit
  `bc06d8164b8b6673ca2dc9b36090f91a5d6348b3`), and its release notes (RAIDZ expansion listed
  as a key feature of 2.3.0).
- "OpenZFS 2.3 is here, with RAID expansion and faster dedup", The Register, 2025-01-23,
  https://www.theregister.com/2025/01/23/openzfs_23_raid_expansion/ (parity level cannot be
  changed by expansion).
- NixOS wiki, page "ZFS", https://wiki.nixos.org/wiki/ZFS (a living document, read
  2026-10-17: kernel support lags, an unsupported kernel makes the package "broken").
- nixpkgs `pkgs/os-specific/linux/zfs/2_4.nix` and `generic.nix` at the Phase 0 pin
  `a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf` (supported kernel range of the ZFS module).
- Details and verification status: `specs/001-phase0-storage-spikes/research.md`, sections
  0 and 7.
