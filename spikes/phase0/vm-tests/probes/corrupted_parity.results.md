# Probe result: the corrupted-parity negative control of S2 (task T064)

Measured on 2026-10-21 with the probe `probe-corrupted-parity` (`guest_s2.nix` with `probe_corrupted_parity.py`),
one VM test at a time, KVM, machine state on an ext4 disk. The probe runs `controls/corrupted-parity.sh` (on the
node the command `corrupted-parity`) after a completed sync, fails a data disk and runs the recovery sequence of
`guest/recover.sh` against the real kernel, SnapRAID 14.4, snapper 0.13.1 and snapraid-btrfs `6492a45` of the
`server` node (profile `small`, the pins of probe T057), with the seeded files of probe T062. It measures and
never writes a record; the full output is `probe-corrupted-parity.json` in the output directory of the driver.
The definition and the refusals are unit-tested in `tests/test_controls.py`.

Runs: 3 VM runs (runs 2 and 3: 132 s and 163 s, on a host busy with other work). Run 1 found two defects of the probe (below),
fixed before run 2. Runs 2 and 3 ran the committed code; run 3 reproduced every outcome of run 2 (differences
only in the parity hashes before the control, which follow the random seed data).

```sh
nix build ./spikes/phase0#driver-probe-corrupted-parity
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-corrupted-parity
```

## Phase `snap`: snapshot workflow, arch0 fails (runs 2, 3)

| Step | Result |
| --- | --- |
| `snapsync sync`, then `snapraid check` | rc 0, `Everything OK` |
| `corrupted-parity` | rc 0, one line: `corrupted /mnt/parity/snapraid.parity: sha256 <before> -> 392ed4b2...`; the sha256 of the parity file afterwards equals the reported one |
| `snapraid check` | rc 1, `DANGER! Unexpected silent data errors!`, `14 data errors`; the log has 14 `parity_error:` lines |
| fault `device-ioerror` on arch0; `detect`, `lock`, `replace arch0 <spare3> 671088640` | rc 0 each |
| `recover arch0` | rc 3; `unrecoverable branch/f0.bin`, `f1.bin`, `f2.bin`, `9 UNRECOVERABLE errors`, fix log `status:unrecoverable:arch0:` for all three; the step lists them with its guidance |
| `verify arch0` | rc 1, `unrecoverable files remain` |
| files | arch0 0 of 3 bit-exact, all three only as `*.unrecoverable`; every other file unchanged |
| `verify arch0 --accept-unrecoverable` | rc 0 |
| `resync arch0 --accept-unrecoverable` | rc 1: SnapRAID refuses the sync, `All the files previously present in disk 'arch0' ... are now missing or have been rewritten!` and `use 'snapraid --force-empty sync'`; `snapsync: sync exited 1; every snapshot is kept` |
| `unlock arch0` | rc 0 |

## Phase `plain`: without the snapshot workflow, arch2 fails (runs 2, 3)

| Step | Result |
| --- | --- |
| `snapraid -E --force-full sync`, then `snapraid check` | rc 0, `Everything OK`: every parity block rebuilt after the control of phase `snap` (`-E`, because every file of arch0 was replaced) |
| `archive-lock lock probe`, `corrupted-parity` | rc 1, `dd: failed to open '/mnt/parity/snapraid.parity': Read-only file system`; the parity sha256 unchanged |
| `archive-lock unlock`, `corrupted-parity` | rc 0, the parity sha256 changed as reported |
| fault `device-ioerror` on arch2; `detect`, `lock`, `replace arch2 <spare2> 536870912` | rc 0 each |
| `recover arch2 --plain` | rc 3; `unrecoverable` `branch/f0.bin` to `f3.bin`, `14 UNRECOVERABLE errors`; fix log `status:unrecoverable:arch2:` for all four |
| `verify arch2` | rc 1, `unrecoverable files remain` |
| files | arch2 0 of 4 bit-exact, all four only as `*.unrecoverable`; every other file unchanged |

## Outcome

With the control, the recovery restores none of the failed disk's files bit-exact, in both variants, so a
check of S2.1 that compares the restored bytes with the expected hashes judges every file of the failed disk
not recovered and S2.1 `fail`. SnapRAID does not write wrong content under the original name: it renames
every affected file `*.unrecoverable`, and `recover` (rc 3) and `verify` (rc 1) report the loss. The same setup
without the control restored every file (probe T062). While the archive is locked, the control cannot run:
the parity filesystem is read-only, and the parity is unchanged.

Finding for the recovery sequence (T072, T073), not decided here: when every file of the failed disk is lost
and the loss is accepted, the resync step fails, because SnapRAID refuses a sync of a disk whose every file is
missing or rewritten unless `-E` is given, and `recover resync` never passes `-E` (S2.5 hazard). The lock status
after the failed resync was not measured.

## Defects of the probe found in run 1 and fixed

1. **The plain phase started from the corrupted parity.** The resync of phase `snap` failed (the finding above),
   so the parity of phase `snap` stayed corrupted, the plain sync refused without `-E`, and the control was
   refused as `unchanged`. Fixed: phase `plain` starts with `snapraid -E --force-full sync` and a clean
   `snapraid check`.
2. **The output of failing tool calls was lost.** The test driver runs every command with `set -euo pipefail`,
   so a command list that redirected the output to a file stopped before printing it. Fixed: `cmd 2>&1 | tail`,
   whose exit status is the command's own under `pipefail`.

## UNVERIFIED

- A partly corrupted parity (only the positions of some files): the control corrupts every byte.
- A config with more than one parity level (unit-tested only; the S2 node has one).
