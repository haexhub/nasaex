# nasaex design

Date: 2026-10-15 · Status: draft, storage model pending spikes (see [ADR-0002](../adr/0002-two-tier-storage.md))

## Goal

An open-source replacement for Unraid: a NAS operating system that pools disks of
different sizes, protects them with parity, serves shares over SMB, runs containers
and VMs, and is managed from a web UI.

## Non-goals (for now)

- Booting from a USB stick or running entirely from RAM ([ADR-0004](../adr/0004-system-disk-with-tmpfs-root.md)).
- Reimplementing Unraid's real-time parity driver.
- Compatibility with Unraid plugins, configuration files or licences.

## Decisions

| Topic | Decision | ADR |
|---|---|---|
| Base OS | NixOS | [0001](../adr/0001-nixos-as-base-os.md) |
| Storage | Mirrored pool plus SnapRAID archive on btrfs data disks, joined by a mergerfs tiered cache | [0002](../adr/0002-two-tier-storage.md) |
| Backend and UI | Go backend, Nuxt (static) with shadcn-vue and `@haex-space/ui`, embedded in the backend | [0003](../adr/0003-go-backend-and-nuxt-frontend.md) |
| Boot model | Dedicated system disk, tmpfs root | [0004](../adr/0004-system-disk-with-tmpfs-root.md) |
| Configuration | JSON in a git-tracked flake, applied with `nixos-rebuild` | [0005](../adr/0005-declarative-config-applied-by-rebuild.md) |

## Components

1. **NixOS flake** with a module that turns the configuration into `snapraid.conf`,
   the two mergerfs pools (base pool and cache pool), btrfs snapshot handling for the
   archive disks, Samba, `smartd` and timers.
2. **Configuration**: one JSON file in a git repository on the system disk. Every UI
   change is a commit.
3. **Backend (Go)**: applies a configuration (write JSON, commit, rebuild, stream the
   log), runs imperative actions that are not declarative (format a disk, start a
   sync, read SMART data, roll back), and serves the API and the embedded UI.
4. **Containers and VMs**: the module only enables Docker and libvirt. Their runtime
   state is not declarative.

## Storage model

The baseline (variant A) follows the tiered-cache pattern documented by mergerfs
(`extended_usage_patterns.md`, section "tiered cache"). mergerfs itself states that it
does not natively support tiered caching; the pattern is two pools plus a mover script.
The earlier idea of stacking overlayfs on top of mergerfs is not part of the baseline
(see [ADR-0002](../adr/0002-two-tier-storage.md) and experiment X1).

- **Pool**: btrfs RAID1 over at least two devices. It receives every new write and holds
  everything that changes: appdata, containers, VM images.
- **Archive**: individual data disks, each its own btrfs filesystem, with SnapRAID parity
  for cold bulk data. `snapraid sync` and `snapraid fix` run against read-only btrfs
  snapshots of the data disks (taken before the sync, kept until the next sync
  completes), so that changes made to archived files after the last sync do not destroy
  recoverability. Whether the snapshots are managed by `snapraid-btrfs` or by the native
  snapshot option of SnapRAID 15 is an open question.
- **Shares**: two mergerfs pools. The **base pool** contains only the archive disks. The
  **cache pool** lists the pool branch first and the archive disks after it; the share is
  the cache pool. New files are created on the pool branch (create policy `ff` or `lus`,
  see open questions; archive branches are set to no-create and `moveonenospc=false`, so
  a full pool produces an out-of-space error instead of an unprotected write to an archive
  disk). An archived file is read, modified and deleted in place on its archive disk.
  There is no overlayfs, no copy-up and no whiteout.
- **Mover** (per share, by age or fill level): follows the documented pattern (move files
  from the cache branch, not from the pool, into the base pool) with one safety rule that
  the stock scripts do not have. The stock scripts use `rsync --remove-source-files`, which
  deletes the source immediately after the copy. The nasaex mover copies with a checksum
  and keeps the source, runs and verifies `snapraid sync`, and only then removes the pool
  copy. If copy, checksum, sync or verification fails, the pool copy remains in place for
  retry. The mover takes no lock that SMB clients would have to honour: it selects only
  pool files that have been idle for a minimum age and are not open, and it removes a pool
  copy only if its ctime, size and inode are unchanged since the copy (compare and delete);
  a file changed meanwhile stays in the pool for a later run. A client delete through the
  share removes every copy of the file (the cache pool keeps the mergerfs default unlink
  policy `epall`), so a delete after the copy leaves no archive copy behind. If the archive
  copy is still there when the mover finds the pool file missing (a client delete during the
  copy, before the mover renames the copy into place, or a pool file that vanished for
  another reason), the mover moves the archive copy into a trash directory beside the
  archive branch on the same disk instead of deleting it. How the trash behaves is subject
  to the S1 and S3 spikes.
- **Protection**: a file on the pool is protected by the mirror. A file in the archive is
  protected by parity as of the last completed sync. Changes made to archived files in
  place since that sync (the unprotected window) are what the snapshot workflow addresses
  and what spike S2 measures. Single-disk failure must not lose data that was synced or is
  on the mirror.
- **Degraded modes**: pool degraded keeps working with a warning; archive degraded is
  read-only with sync, scrub and mover locked, writes continue into the pool; both
  degraded makes every share read-only.

SnapRAID cannot serve the data of a failed disk until it is repaired (`snapraid fix`),
unlike Unraid, which emulates the disk. This is accepted.

### Variant B (experiment X1, optional)

The former design: overlayfs with the archive (read-only, joined by mergerfs) as the lower
layer and a pool subvolume as the upper layer, so that a write to an archived file copies
it up into the pool and a delete becomes a whiteout. It would keep the archive read-only
for clients, but it is not an established setup (mergerfs lists a read/write overlay on top
of a read-only filesystem among its non-features, upstream documents no overlayfs on
mergerfs, and the kernel documents changes to underlying layers while mounted as undefined,
which the mover does). It is kept only as the optional experiment X1. X1 never gates a
decision; its result only decides whether a copy-up enhancement is worth adding later.

## Phases

0. **Spikes**: prove the storage model in NixOS VM tests before any product code.
1. **Core**: pool, archive, shares, SMB, SMART and notifications, minimal UI.
2. **Docker management.**
3. **Plugin system**, with Docker as the first consumer.
4. **VMs.**

Phase n+1 starts only when phase n passes its tests.

### Phase 0 spikes

- **S1 — tiered-cache share**: verify the mergerfs two-pool setup under SMB: new files land
  on the pool, archived files are readable and changeable in place through the same share,
  renames, xattrs, files over 4 GiB, pool full, the behaviour of the create policy, a file
  that stays visible through the share before and after the mover moved it, and
  persistence across reboot. PASS means a normal SMB client sees one ordinary share and
  nothing is written to an archive disk except by an in-place change of an archived file or
  by the mover.
- **S2 — SnapRAID recovery**: exercise a disk failure before and during `sync`, predict
  recoverability per parity block, measure recovery with and without the btrfs snapshot
  workflow, show how a sync after a failure is reached and refused, and document the safe
  `fix` and resynchronisation sequence. PASS means every prediction matches and the
  documented sequence restores everything it promises.
- **S3 — mover crash safety**: interrupt the mover between copy, checksum, sync,
  verification and pool copy removal; prove that retry never loses the pool copy and that
  nothing is removed before parity covers its replacement. The stock `rsync
  --remove-source-files` mover is a negative control that must be flagged as a failure.
- **S4 — degraded modes**: verify transitions for pool, archive and simultaneous
  degradation, including that archive writes, sync, scrub and mover actions are locked
  when required.
- **S5 — declarative rebuild**: measure rebuild duration and test failed builds,
  failed activation and explicit generation rollback without confusing Git history
  with the active NixOS generation. The restoration of the previous generation after a
  failed activation is backend behaviour that the harness must supply.
- **X1 — overlayfs experiment (optional, non-gating)**: variant B on its own, including the
  stacking depth defaults 0, 1 and 2, xattrs, whiteouts, renames with `redirect_dir`, large
  files and the mover changing layers while the overlay is mounted. It can be skipped
  without affecting any ADR.
- **X2 — ZFS mirror pool experiment (optional, non-gating)**: compares a ZFS mirror with the
  btrfs RAID1 pool as the pool branch under the same tiered-cache setup (filesystem
  behaviour behind the share, a device failure and replacement, adding and replacing
  capacity, memory use, the kernel pin). It runs only after S1 to S4 and can be skipped
  without affecting any ADR.

ADR-0002 needs S1 to S4 to pass. X1 and X2 never gate it.

## Open questions

- Licence for the project. None is set yet.
- Snapshot tooling for the archive: `snapraid-btrfs` (external, works with SnapRAID 14.x)
  or the native `snapshot` option announced for SnapRAID 15.0 (a release candidate, not yet
  verified in practice). Default until decided: external tooling at the pinned SnapRAID 14.x.
- Create policy of the cache pool: `ff` or `lus` (mergerfs recommends `ff`, `lus` or `lfs`
  for a cache pool). Default until decided: `ff` with the archive branches set to no-create.
- Whether a copy-up enhancement (variant B) is worth adding later: decided by X1, not before.
- Whether a ZFS mirror would be the better fast pool: compared by X2; btrfs RAID1 stays unless
  X2 shows an advantage and a reviewed amendment of ADR-0002 adopts it.
