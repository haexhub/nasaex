# Probe result: a boot between the failure, the replacement and the recovery of S2

Measured on 2026-10-22 with the probe `probe-s2-boot` (`vm-tests/s2/default.nix` with `probe_s2_boot.py`, budget
`probe`), one VM test at a time, KVM, machine state on an ext4 disk, on the `server` node of S2 (SnapRAID 14.4,
snapper 0.13.1, snapraid-btrfs `6492a45`, the archive lock of T061 and `guest/recover.sh` of T062). It asks what
the lifecycle point `boot:after-failure` of the S2 criteria does to the archive lock, the state of the recovery
sequence and the mounts. It measures and never writes a record; the full output is `probe-s2-boot.json` in the
output directory of the driver.

The boot: a graceful `shutdown`, then the image of the failed disk is replaced by a blank one (the failed disk
was taken out; `faults.device_failed_at_boot`), then `start`. Seven seeded files on the three disks, a snapshot
sync, arch0 fails by `device-ioerror`.

Runs: 2 VM runs. Run 1 read the mounts with `findmnt -R /mnt`, which fails because `/mnt` is no mount point, and
after the boot set the lock again with `recover lock`, which unmounts the replacement first; both fixed. Run 2 ran
the committed probe; its outcomes of every step run 1 also measured are the same.

```sh
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-s2-boot
```

## A: boot after the replacement (detect, lock, replace, boot, recover ...)

| Step | Result (run 2) |
| --- | --- |
| detect, lock, replace by spare0 | rc 0 each; locked: parity, arch1, arch2 and `/var/lib/snapraid` read-only, the replacement mounted at the arch0 paths read-write; `/run/archive-lock` and `/run/recover/arch0/replaced` exist |
| shutdown, boot | 1.4 s, 15.1 s; no failed unit; the replacement carries the label arch0 and is mounted at `/mnt/archive/arch0` by it; both pools active |
| the lock after the boot | `unlocked`: `/run/archive-lock` and `/run/recover` are gone (tmpfs); **parity, arch1, arch2, arch0 and every content location are read-write** |
| `recover recover arch0` | rc 1, `run step 3 first` (the marker of step 3 is gone) |
| `recover replace arch0 <spare0>` again | rc 1, `already carries a filesystem signature` |
| `archive-lock lock` again, the marker of step 3 set by hand | locked; now the replacement at `/mnt/archive/arch0` is read-only too |
| `recover recover arch0` | rc 1: `Error creating directory '/mnt/archive/arch0/branch/a'. Read-only file system.`, `Stopping at block 0`; verify rc 1, resync refused (`step 5 has not passed`) |
| files | arch0 0 of 3 (not restored); arch1 and arch2 unchanged |

snapper after the boot listed only snapshot 0 for arch0 (the replacement); the content files on the system disk and
the parity disk were present, `snapraid status`: `No error detected.`

## B: boot before the lock (fail, boot, detect, lock, replace, recover ...)

| Step | Result (run 2) |
| --- | --- |
| shutdown, boot | 1.0 s, 22.2 s; no failed unit; no device carries the label arch0; **parity, arch1, arch2 and their content locations read-write**, both pools `inactive` (a branch is missing) |
| detect | rc 0, `/mnt/archive/arch0 is not mounted`, `no device carries the label arch0` |
| lock, replace by spare0, recover, verify, resync, unlock | rc 0 each; `every file of arch0 recovered`, `Everything OK`, `covered` |
| files | arch0 3 of 3 bit-exact as of the sync; arch1 and arch2 unchanged |

## Findings (for the decision on `boot:after-failure`, not decided here)

1. The archive lock and the state of the recovery sequence live in `/run` and do not survive a boot: after any
   boot every archive, parity and content location is read-write until the lock is set again.
2. A boot after the replacement breaks the sequence of T062: step 4 refuses without the marker of step 3, and
   step 3 cannot run again on the formatted replacement. Setting the lock again (as a lock that survives the boot
   would) also makes the mounted replacement read-only, so `fix` cannot write it: a persistent lock must leave
   the replacement of the failed disk writable.
3. A boot before the lock needs nothing to survive it, and the whole sequence passed with every file
   bit-exact. Between that boot and the lock the array is writable (no lock), and the pools stay inactive while
   a branch is missing.

## UNVERIFIED

- A boot while the failed disk is still attached (its image kept): the failed disk would come back with its old
  label beside the replacement.
- A crash instead of a graceful shutdown.
- Maintenance timers at boot: the S2 node runs none.
