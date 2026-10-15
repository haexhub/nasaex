# ADR-0002: Two-tier storage

Status: proposed, pending spikes S1 to S5 · Date: 2026-10-15

## Context

SnapRAID with mergerfs pools disks of different sizes, but parity is updated by a sync
run. Data written since the last sync is unprotected, a sync after a disk failure
destroys recoverability, and changes on other disks reduce what can be recovered.
Single-disk failure must not cause data loss.

## Decision

Two tiers. A btrfs RAID1 pool takes all new and changing data. A SnapRAID archive holds
cold data. overlayfs presents both as one share: archive read-only below, pool above, so
the archive changes only during a controlled mover and sync run. In the degraded state,
sync, scrub and the mover are locked and the archive is read-only.

## Alternatives

- **SnapRAID only**: leaves an unprotected window and the failure hazards above.
- **btrfs RAID1 or ZFS for everything**: simple and real-time safe, but costs about half
  the capacity and drops the mixed-size parity model.
- **Own real-time parity**: no mature open-source implementation; high risk of data loss.

## Consequences

- At least two pool devices are required.
- The mover is the critical component and needs crash-safety tests.
- The decision is only accepted when the spikes show that overlayfs over mergerfs works
  with Samba, renames and large files, and that the SnapRAID failure behaviour matches
  the assumptions. Otherwise the fallback is direct archive access, read-only when
  degraded.
