# Probe result: a boot between the failure, the replacement and the recovery of S2

Measured on 2026-10-22 and 2026-10-23 with the probe `probe-s2-boot` (`vm-tests/s2/default.nix` with
`probe_s2_boot.py`, budget `probe`), one VM test at a time, KVM, machine state on an ext4 disk, on the `server`
node of S2 (SnapRAID 14.4, snapper 0.13.1, snapraid-btrfs `6492a45`, the archive lock of T061 and
`guest/recover.sh` of T062). It asks what the lifecycle point `boot:after-failure` of the S2 criteria does to the
archive lock, the state of the recovery sequence and the mounts, and whether the lock that survives a boot (task
T066a: state in `/var/lib/archive-lock` and `/var/lib/recover`, the unit `archive-lock-restore` of
`nix/archive-lock.nix`) closes the gap. It measures and never writes a record; the full output is
`probe-s2-boot.json` in the output directory of the driver.

Every phase starts from a fresh array (`History.reset`): seven seeded files on the three disks, a snapshot sync.
A boot is a graceful `shutdown` (or a crash: QEMU process termination), the image of the failed disk replaced by
a blank one (the failed disk taken out; `faults.device_failed_at_boot`) unless the phase keeps it, then `start`.

Runs: 5 VM runs.

- Runs 1 and 2 used the lock of T061, with its state in `/run`. Run 1 read the mounts with `findmnt -R /mnt`, which fails because `/mnt` is no mount point, and set the lock again with `recover lock`, which unmounts the replacement first. Both were fixed for run 2.
- Run 3 used the first version of T066a and found the three faults below.
- Runs 4 and 5 ran the committed code. Run 5 took 323 s. Run 5 reproduced every outcome of run 4; only boot times, filesystem UUIDs and device names differ.

```sh
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-s2-boot
```

## Before T066a: the lock in `/run` (run 2)

| Phase | Result |
| --- | --- |
| detect, lock, replace by spare0; shutdown, boot | `unlocked`: `/run/archive-lock` and `/run/recover` are gone (tmpfs); **parity, arch1, arch2, arch0 and every content location read-write**; `recover recover` rc 1 `run step 3 first`; step 3 again rc 1 `already carries a filesystem signature` |
| the same, the lock set again by hand | the replacement is read-only too; `fix` rc 1 `Read-only file system`; arch0 0 of 3 restored |
| arch0 fails, boot before the lock, then the whole sequence | rc 0 each; 3 of 3 bit-exact; between the boot and the lock every location is read-write, both pools `inactive` |

## T066a, run 3: three faults

| Phase | Fault |
| --- | --- |
| the reset boot (every disk blank, the content file of the system disk left from the phase before) | `archive-lock-restore` failed with exit 1 and no output. The parity file does not exist while the parity disk is not mounted; `findmnt --target` exits 1 without a word on a missing path (measured with util-linux 2.39 on the host; the guest unit failed with the same silence), and `set -e` ended the lock. A lock with a missing parity disk failed the same way. |
| crash right after step 3 | The marker `replaced` (synced) survived; the new filesystem had not been written: `recover` rc 1, `/mnt/archive/arch0/.snapshots is not a valid btrfs subvolume`. verify and resync refused; arch0 0 of 3 restored. |
| failed disk back (its image kept) | The boot mounted the **failed** disk (same label arch0) at `/mnt/archive/arch0`, and the exempt-by-name left it writable. recover, verify, resync passed against the failed disk; the replacement stayed unused. |

Fixes (runs 4 and 5):
- `_al_mount_of` falls back to the parent of a missing path.
- Step 3 writes the new filesystem to the disk (`sync -f`) before the exempt and the marker.
- `archive-lock exempt DISK` records the UUID of the filesystem mounted at the data path. A different filesystem there is locked (`not the filesystem exempt as the replacement of arch0, locked`).
- Steps 4 to 6 refuse unless the data path is mounted from that filesystem.

## T066a, runs 4 and 5

After every boot, `archive-lock-restore` was `active`/`success`, ordered `After` the parity and data mount units and `local-fs.target` and `Before` `sysinit.target`. No unit failed.

| Phase | Boot (run 4 / run 5) | After the boot | Recovery | Files |
| --- | --- | --- | --- | --- |
| after-replace: detect, lock, replace; shutdown; boot | 29.6 s / 16.8 s | locked (`arch0 failed`), `exempt: arch0 <UUID of the replacement>`; parity, arch1, arch2 and `/var/lib/snapraid` read-only, only arch0 (the replacement, same UUID) read-write; `/var/lib/recover/arch0/replaced` kept; pools active | recover, verify, resync, unlock rc 0 each; `covered` | 7 of 7 bit-exact |
| crash-after-replace: the same with a crash | 14.1 s / 27.7 s | the same | the same | 7 of 7 bit-exact |
| power-off-failure: arch0 fails while off; boot | 24.2 s / 30.2 s | locked by the restore: `boot: arch0 is not mounted`; parity, arch1, arch2 and `/var/lib/snapraid` read-only; pools inactive | the whole sequence rc 0 each | 7 of 7 bit-exact |
| failed-disk-back: detect, lock, replace; boot with the failed disk attached | 15.5 s / 16.8 s | label arch0 on the failed disk, mounted at `/mnt/archive/arch0` (other UUID); the restore locked it: **every location read-only** | `recover` rc 1: `/mnt/archive/arch0 is not mounted from the replacement of step 3; take the failed disk out and boot again`; no fix ran | |
| failed-disk-taken-out: the failed disk taken out; boot | 15.3 s / 15.6 s | the replacement mounted (the recorded UUID), the only location read-write | recover, verify, resync, unlock rc 0 each | 7 of 7 bit-exact |

The reset boot before the crash phase (every disk blank) succeeded: the restore printed four `not mounted,
skipped` lines, and the reset lifted that lock. While the failed disk was back, its ro remount was recorded. After the boot without it, `archive-lock status` lists `remount /mnt/archive/arch0 rw` (the exempt
replacement). The unlock remounts it read-write, which it already is.

## Findings

1. With T066a no boot opened the archive. After every measured boot (graceful, crash, a disk failed while off, the failed disk back), no data, parity or content location was writable except the replacement exempt by its filesystem UUID. The recovery sequence completed with every file bit-exact.
2. A boot between `replace` and `recover` is safe once step 3 writes its filesystem to the disk before its marker. Without that write, a crash kept the marker but not the layout.
3. A failed disk that comes back with its label can take the place of the replacement at a boot. The UUID binds the exempt and the steps 4 to 6 to the replacement; the failed disk stays read-only, and the sequence stops until it is taken out.
4. `findmnt --target` on a missing path exits 1 without output. Under `set -e` that ended the lock silently; `_al_mount_of` now falls back to the parent directory.

## UNVERIFIED

- Maintenance timers at boot: the S2 node runs none (the restore is ordered before `sysinit.target`, so before any timer).
- `recover unlock` after a failed recovery: in run 3 it unlocked the array with arch0 empty (the probe runs every step regardless). Whether the next sync would then drop the files of arch0 from the parity was not measured. SnapRAID refuses a sync while a whole disk is empty, unless it gets `-E`.
- While the array is locked, the base pool (`mfs`) has only the replacement as a writable branch, so a write to the share during the recovery would land on it. Not measured.
