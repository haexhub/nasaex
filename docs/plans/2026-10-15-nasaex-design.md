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
   mergerfs and overlayfs mounts, Samba, `smartd` and timers.
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
  `snapraid sync`, verify, apply whiteouts, delete from the pool. Every file is
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

## Open questions

- Does overlayfs over mergerfs behave under Samba, renames and large files? (spike S1)
- Which behaviours of SnapRAID after a disk failure are real? (spike S2)
- Licence for the project. None is set yet.
