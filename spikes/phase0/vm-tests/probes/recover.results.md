# Probe result: the S2 recovery sequence recover.sh (task T062)

Measured on 2026-10-20 with the probe `probe-recover` (`guest_s2.nix` with `probe_recover.py`), one VM test at
a time, KVM, machine state on an ext4 disk. The probe runs `guest/recover.sh` step by step (on the node the
command `recover`) against the real kernel, SnapRAID 14.4, snapper 0.13.1 and snapraid-btrfs `6492a45` of the
`server` node (profile `small`, the pins of probe T057). It measures and never writes a record; the full output
is `probe-recover.json` in the output directory of the driver (evidence, not committed). The host tests
(`tests/test_recover.py`, `tests/test_procedure.py`) check the decisions with stand-ins.

Runs: 3 VM runs, 42 s each. Run 1 found three defects (below), fixed before run 2. Runs 2 and 3 ran the
committed code; run 3 reproduced every outcome of run 2 (differences only in SnapRAID's timing
bars).

```sh
nix build ./spikes/phase0#driver-probe-recover
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-recover
```

## Phase `snap`: snapshot workflow, arch0 fails (runs 2, 3)

Ten seeded files on the three archive disks, `snapsync sync`. Disk inventory: arch0 671088640 bytes (640 MiB),
arch2 536870912 bytes (512 MiB), from `nix/profiles.nix`.

| Step | Result |
| --- | --- |
| `recover detect arch0` before the failure | rc 1, `check failed: arch0 shows no failure; nothing is recovered` |
| fault `device-ioerror` on arch0, `detect` | rc 0, `arch0 failed:` / `a direct read of /dev/vdd fails` |
| `lock arch0` | rc 0; arch0 unmounted (`not mounted, skipped`); parity, arch1, arch2 `ro`, `/var/lib/snapraid` bind `ro` |
| `replace arch0 <spare2> 671088640` | rc 1, `holds 536870912 bytes, less than the 671088640 of the failed disk`; `blkid -p` on spare2 afterwards: rc 2 (still blank) |
| `replace arch0 <spare3>` with spare3 formatted ext4 | rc 1, `already carries a filesystem signature; use a blank disk`; spare3 still `ext4` |
| spare3 (1024 MiB) wiped, `replace arch0 <spare3> 671088640` | rc 0, `virtio-spare3 replaces arch0` (a larger replacement) |
| `recover arch0` | rc 0; `recovered branch/f0.bin`, `f1.bin`, `f2.bin`, `0 unrecoverable errors`; arch1/arch2 from their `synced` snapshot |
| `verify arch0` | rc 0, `Everything OK` |
| `resync arch0` | rc 0; `Nothing to sync.`, a new `synced` set on all three disks, cleanup of the old sets, `covered` |
| status after the resync | `locked`, `stage: resync`, every location `rw` |
| `unlock arch0` | rc 0 |
| files | arch0 3 of 3 bit-exact as of the sync; every other file unchanged |

## Phase `plain`: without the snapshot workflow, arch2 fails with a loss (runs 2, 3)

A plain `snapraid sync`; `snapraid --test-fmt disk locate` put arch1 `f0.bin` and arch2 `f0.bin` both at offset
0, span 524288 (positions 0 and 1 at 256 KiB blocks), arch2 `f1.bin`..`f3.bin` at later positions. Then arch1
`f0.bin` was rewritten in place (same size, new data), so by the S2.2 rule (spec Clarifications 2026-10-20)
arch2 `f0.bin` is unrecoverable and `f1.bin` to `f3.bin` are recoverable.

| Step | Result |
| --- | --- |
| fault `device-ioerror` on arch2, `detect` | rc 0, `a direct read of /dev/vdf fails` |
| `lock`, `replace arch2 <spare2> 536870912` | rc 0, rc 0 |
| `recover arch2 --plain` | rc 3; fix log `status:recovered` f1, f2, f3 and `status:unrecoverable:arch2:branch/f0.bin` (`2 UNRECOVERABLE errors`); the step lists `branch/f0.bin` with the guidance |
| `verify arch2` | rc 1, `unrecoverable files remain; accept the loss with --accept-unrecoverable or retry step 4`; the check log: `error:0:arch2:branch/f0.bin: Open error at position 0. No such file or directory.`, the same at position 1, `status:unrecoverable:arch2:branch/f0.bin` |
| `resync arch2 --plain` | rc 1, `step 5 has not passed`; the archive stays locked and read-only |
| `verify arch2 --accept-unrecoverable` | rc 0 (only the listed file fails) |
| `resync arch2 --plain --accept-unrecoverable` | rc 0, `Everything OK`, `covered` |
| `unlock arch2` | rc 0 |
| files | arch2 f1, f2, f3 bit-exact; f0 missing, `f0.bin.unrecoverable` present; every other file unchanged |

The lost set equals the per-position prediction.

## Phase `unplug` (runs 2, 3; run 1 found the same evidence, printed wrongly)

After `device-unplug` of arch1: `detect arch1` rc 0 with `/mnt/archive/arch1 is not mounted` and `no device
carries the label arch1`.

## Defects found in run 1 and fixed

1. **detect missed `device-ioerror`.** Right after the fault `btrfs device stats --check` reported no error
   and reads came from the page cache, so `detect` said `arch0 shows no failure` (both phases). Fixed: a direct
   read (`dd iflag=direct`) of the mounted device is evidence too; runs 2 and 3: `a direct read of /dev/vdd fails`.
2. **The snapshot resync stopped at `cleanup`.** `snapsync sync` completed the new set, then the wrapper
   refused its `snapraid-btrfs cleanup` in the resync stage (`archive-lock: refused: snapraid-btrfs cleanup`).
   Fixed: the resync stage lets `snapraid-btrfs cleanup` through as well (snapsync runs it only after the new
   set is complete); `cleanup-all` and every other command stay refused.
3. **detect printed several pieces of evidence wrongly** (`printf` reused its format per argument). Fixed: one
   line each.

## UNVERIFIED

- A failure during a sync (S2.4), a replacement across a reboot, the `device-failed-at-boot` path.
- `fix -i` retries in step 4 (guidance only; probe T057 measured `-i` after a completed sync).
