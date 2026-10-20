# Probe result: the S2 guest scripts snapsync.sh and archive-lock.sh (tasks T060, T061)

Measured on 2026-10-20 with the probe `guest_s2.nix` (`probe_guest_s2.py`), one VM test at a time, KVM,
machine state on an ext4 disk. The probe runs `guest/snapsync.sh` and `guest/archive-lock.sh` with
`nix/snapper-configs.nix` and `nix/archive-lock.nix` against the real tools of the `server` node. It
measures and never writes a record; the full output is `probe-guest-s2.json` in the output directory of the
driver (evidence, not committed). The host unit tests (`tests/test_archive_lock.py`, `tests/test_snapsync.py`)
check the scripts' decisions with stand-ins; this probe checks what the kernel and the tools do.

Runs: 6 VM runs, 65 to 71 s each. Run 1 never started (the app needs a git work tree as its working
directory). Run 2 stopped at a probe defect (`pkill -f held.bin` also killed the shell running it). Runs 3 and
4 found two script defects and one gap, fixed before run 5 (below). Runs 5 and 6 ran the committed code;
run 6 reproduced every outcome of run 5 (differences only in SnapRAID's timing
bars, dates, snapshot UUIDs, temporary file names and the order of kernel log lines).

## Environment

Profile `small`, VM `server` only (1536 MiB, 2 vCPUs); kernel 6.18.56, SnapRAID 14.4, snapper 0.13.1,
snapraid-btrfs 0.14.1+git `6492a45`, the same pins as probe T057 (`snapraid.results.md`). Archive disks
`arch0` to `arch2` (btrfs, `data` and `content` subvolumes), parity on ext4 (`/mnt/parity`), the base pool
`/mnt/base` (mergerfs over the three `branch` directories). Disk failure: fault kind `device-ioerror` on `arch2`
(`drive_del` of its QEMU drive); replacement: a new btrfs filesystem labelled `arch2` on the spare of the same
size (`spare2`), with `data`, `content`, `branch`, `trash` and `.snapshots`.

```sh
nix build ./spikes/phase0#driver-probe-guest-s2
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-guest-s2
```

## Generated configuration (runs 3 to 6)

- `/etc/snapraid.conf`: `parity /mnt/parity/snapraid.parity`, `content /var/lib/snapraid/snapraid.content`,
  `content /mnt/parity/snapraid.content`, `data archN /mnt/archive/archN/` for N = 0, 1, 2,
  `exclude /.snapshots/`. No content copy on an archive disk.
- `/etc/snapper/configs/arch0`: `FSTYPE="btrfs"`, `SUBVOLUME="/mnt/archive/arch0"`, nothing else (so no
  `NUMBER_CLEANUP`, `TIMELINE_*` or `EMPTY_PRE_POST_CLEANUP` key is set); `/etc/sysconfig/snapper`:
  `SNAPPER_CONFIGS="arch0 arch1 arch2"`.
- `type -a`: `snapraid` and `snapraid-btrfs` resolve to the wrappers of `nix/archive-lock.nix` in
  `/run/current-system/sw/bin`; `snapsync` and `archive-lock` are there too.
- `spike-snapshots-init arch0 arch1 arch2`: `.snapshots` is a subvolume (`btrfs subvolume show`), `root`, mode 750.

## snapsync.sh (runs 3 to 6, identical)

| Step | Result |
| --- | --- |
| `snapsync sync` (fresh array) | rc 0, `Everything OK`; `arch0..2: snapshot 1 synced` |
| `snapsync complete` | rc 0, snapshot 1 `synced` on all three configs |
| `snapsync covered` (synced set) and `covered --plain` | rc 0, `covered`, both |
| a new file on arch0, then `covered` / `covered --plain` | synced set rc 0 `covered`; live rc 1, `diff exit 2, summary:exit:diff, content_info:block_unsynced:0` |
| second `snapsync sync` | rc 0; `cleanup` ran `snapper -c archN rm 1`; each config keeps only snapshot 2 `synced` |
| `snapsync free arch0` before and after it | 583204864 both times (no file was deleted, so nothing to free) |
| `snapsync sync -- -E` | rc 2, `the force-empty override is passed only as --force-empty`; snapshots unchanged |

The space that a cleanup frees after deletions was measured by probe T057 (item 19 follow-up); this probe did
not delete files before a sync.

## archive-lock.sh

**A file open for writing (runs 3 to 6).** With a writer holding a file on arch1 open: `archive-lock lock`
exits 1 with `mount: /mnt/archive/arch1: mount point is busy.` and `archive-lock: not read-only, the refusal
stays: /mnt/archive/arch1`; `status` lists arch1 `rw` and the other locations `ro`; `snapraid sync` is refused
(rc 30). After the writer ended, `lock` again exited 0 (run 3: exit 1, defect 1 below).

**Locked, healthy array (runs 3 to 6).** `findmnt` shows `ro` on every `/mnt/archive/archN` and, without
being named, on every `/mnt/archive-content/archN` (the remount of a btrfs subvolume mount makes the whole
filesystem read-only), on `/mnt/parity` and on the bind mount `/var/lib/snapraid`. Every route:

| Attempt | Result |
| --- | --- |
| `touch`, in-place `dd`, `rm` on an archive disk | rc 1, `Read-only file system` |
| `touch` in `/mnt/base` (base pool) | rc 1, `touch: cannot touch '/mnt/base/branchless.bin': Read-only file system` (runs 5, 6, as a command of its own; in runs 3 and 4 it shared one shell command with `findmnt`, `echo` and `ls`, which returned rc 1 without the touch message or the later output, cause UNVERIFIED) |
| `touch` in `/var/lib/snapraid` and in `/mnt/parity` | rc 1, `Read-only file system` |
| `snapraid sync`, `snapraid scrub`, `snapraid-btrfs sync`, `snapraid-btrfs diff` (wrappers) | rc 30, `archive-lock: refused: <command>: the archive is locked (<reason>)` |
| the real snapraid `sync` | rc 1, `Error opening parity file '/mnt/parity/snapraid.parity'. Read-only file system.` |
| the real snapraid `touch` | rc 1, `Error removing the stale content file '/var/lib/snapraid/snapraid.content.tmp'. Read-only file system.` |
| `snapper -c arch0 create` | rc 1, `IO Error (mkdir failed errno:30 (Read-only file system)).` |
| `snapper -c arch1 delete <synced snapshot>` | rc 1, `Deleting snapshot failed.` |
| `systemctl start snapper-cleanup.service` | started and deactivated; no snapshot changed |
| `mover-effects mover_copy_item` | rc 30, the refusal line |
| `snapsync sync` | rc 30, the refusal line |
| `snapraid status`, `snapraid diff`, `snapraid-btrfs ls` | rc 0 (`diff`: 11 equal, `No differences`): the existing lock file opens on the read-only bind |

Afterwards the parity file and both content copies (SHA-256), the snapper snapshot list of every config and
every array file (SHA-256) were unchanged. `archive-lock unlock`: rc 0, every mount `rw` again, a write works.

**Disk failure, replacement and recovery under the lock (runs 5, 6; runs 3, 4 up to the resync, defect 2).** After `device-ioerror` on arch2 a
read of an arch2 file still succeeded (page cache); btrfs logged write errors and `forced readonly`. `lock`
exited 0; arch2's mounts show `ro`. `snapraid sync` was refused (rc 30). The failed mounts unmounted; the
replacement was mounted read-write while the lock held (`status`: arch2 `rw`, the others `ro`). `snapsync fix
arch2` under the lock: rc 0, `recovered branch/f0.bin`, `f1.bin`, `f2.bin`, `0 unrecoverable errors`; arch0 and
arch1 read from their `synced` snapshot; all 3 arch2 files bit-exact as of the last sync. `unlock`, then
`snapsync sync`: rc 0, `Nothing to sync.`, a new `synced` set on all three configs, `cleanup` removed the old
sets and arch2's pre-fix and post-fix snapshots; `complete` rc 0, `covered` rc 0; every array file bit-exact.

## Defects found and fixed

1. **Second lock after a partial one (run 3).** `lock` touched `<first content file>.lock` unconditionally;
   after a lock that stopped at a busy filesystem, `/var/lib/snapraid` was already read-only and `touch` failed
   (`cannot touch '/var/lib/snapraid/snapraid.content.lock': Read-only file system`), so the retry exited 1.
   Fixed: the lock file is created only when it is missing; test
   `test_lock_leaves_an_existing_snapraid_lock_file_alone`.
2. **snapperd started while locked (runs 3, 4).** `snapsync fix` stops snapperd, and the fix's own snapper
   calls start it again while arch0 and arch1 are read-only. After `unlock` that snapperd still refused:
   `snapraid-btrfs sync` failed creating its snapshots, and `snapper -c arch0 create` and `-c arch1 create`
   failed with `IO Error (mkdir failed errno:30 (Read-only file system)).` while `-c arch2` (mounted after the
   lock) worked (run 4). After `systemctl stop snapperd` all three creates and the sync worked. Fixed: `unlock`
   stops snapperd after the remounts (the next snapper call starts a fresh one); tests
   `test_unlock_stops_snapperd_after_the_filesystems_are_writable_again` and
   `test_a_failed_unlock_keeps_snapperd_and_the_refusal`. Runs 5 and 6: the sync after the replacement worked
   without any extra step. Why the running snapperd keeps the read-only state is UNVERIFIED.
3. **Command words behind option values (host test, before any VM run).** The wrappers took the first known
   command word anywhere in the arguments, so `snapraid-btrfs -u sync config` was refused as `sync`. Fixed:
   the command is the first argument that is neither an option nor an option's value, with the value-taking
   options of SnapRAID 14.4 (`cmdline/snapraid.c` `OPTIONS` and `long_options`) and snapraid-btrfs `6492a45`.

## UNVERIFIED

- Why snapperd keeps refusing after the remount (defect 2); the fix does not depend on the cause.
- The lock across a reboot (state in `/run`, by design of S2; S4 makes it persistent), a failure by
  `device-unplug` or `device-failed-at-boot`, and a scheduled route (a systemd timer running the wrappers).
- `snapsync free` after deletions with this wrapper (measured with raw commands in probe T057).
- Writes through the cache pool and the share while locked (S1/S4 scope).
