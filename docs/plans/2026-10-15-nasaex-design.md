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
| Storage | Mirrored pool plus SnapRAID archive, joined by overlayfs and mergerfs | [0002](../adr/0002-two-tier-storage.md) |
| Backend and UI | Go backend, Nuxt (static) with shadcn-vue and `@haex-space/ui`, embedded in the backend | [0003](../adr/0003-go-backend-and-nuxt-frontend.md) |
| Boot model | Dedicated system disk, tmpfs root | [0004](../adr/0004-system-disk-with-tmpfs-root.md) |
| Configuration | JSON in a git-tracked flake, applied with `nixos-rebuild` | [0005](../adr/0005-declarative-config-applied-by-rebuild.md) |

## Components

1. **NixOS flake** with a module that turns the configuration into `snapraid.conf`,
   mergerfs and overlayfs mounts, Samba, `smartd` and timers. The mergerfs mount
   must explicitly support stacking overlayfs on top of it (`passthrough.max-stack-depth=1`).
2. **Configuration**: one JSON file in a git repository on the system disk. Every UI
   change is a commit.
3. **Backend (Go)**: applies a configuration (write JSON, commit, rebuild, stream the
   log), runs imperative actions that are not declarative (format a disk, start a
   sync, read SMART data, roll back), and serves the API and the embedded UI.
4. **Containers and VMs**: the module only enables Docker and libvirt. Their runtime
   state is not declarative.

## Storage model

- **Pool**: btrfs RAID1 over at least two devices. It receives every new write and holds
  everything that changes: appdata, containers, VM images.
- **Archive**: individual data disks (XFS or ext4) with SnapRAID parity for cold bulk data.
- **Shares**: overlayfs with the archive (read-only, joined by mergerfs) as the lower
  layer and a pool subvolume as the upper layer. Writes copy up into the pool; deletes
  become whiteouts in the pool.
- **Mover** (per share, by age or fill level): copy to the archive with a checksum, run
  `snapraid sync`, and verify before removing the pool copy. Applying whiteouts is a
  separate archive mutation: after deleting the archived files, run and verify another
  `snapraid sync` before removing the whiteouts. If copy, checksum, sync or verification
  fails, the pool copy and whiteout remain in place for retry. Every live file is
  protected by the mirror or by parity at all times.
- **Degraded modes**: pool degraded keeps working with a warning; archive degraded is
  read-only with sync, scrub and mover locked, writes continue into the pool; both
  degraded makes every share read-only.

SnapRAID cannot serve the data of a failed disk until it is repaired (`snapraid fix`),
unlike Unraid, which emulates the disk. This is accepted.

## Phases

0. **Spikes**: prove the storage model in NixOS VM tests before any product code.
1. **Core**: pool, archive, shares, SMB, SMART and notifications, minimal UI.
2. **Docker management.**
3. **Plugin system**, with Docker as the first consumer.
4. **VMs.**

Phase n+1 starts only when phase n passes its tests.

### Phase 0 spikes

- **S1 — stacked share filesystem**: verify overlayfs over a read-only mergerfs
  archive, including the required stacking option, xattrs, whiteouts, SMB access,
  renames and large files.
- **S2 — SnapRAID recovery**: exercise a disk failure before and during `sync`, and
  document the safe `fix` and resynchronisation sequence.
- **S3 — mover crash safety**: interrupt the mover between copy, checksum, sync,
  verification, archive deletion and cleanup; prove that retry never loses the pool
  copy and that archive deletions are synced before whiteouts are removed.
- **S4 — degraded modes**: verify transitions for pool, archive and simultaneous
  degradation, including that archive writes, sync, scrub and mover actions are locked
  when required.
- **S5 — declarative rebuild**: measure rebuild duration and test failed builds,
  failed activation and explicit generation rollback without confusing Git history
  with the active NixOS generation.

## Open questions

- Licence for the project. None is set yet.
