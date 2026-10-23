# Probe result: checks S2.1 and S2.3 on the S2 node (task T067)

Measured on 2026-10-23 with the probe `probe-s2-baseline` (`vm-tests/s2/default.nix` with
`probe_s2_baseline.py`, budget `probe`), one VM test at a time, KVM, machine state on an ext4 disk. It runs the two
checks of `vm-tests/s2/checks_baseline.py` through `testlib.recorder` against the real kernel, mergerfs, SnapRAID
14.4, snapper 0.13.1, snapraid-btrfs `6492a45`, the archive lock and `guest/recover.sh` (with the persistent lock of
T066a), on the criteria draft `docs/phase0/criteria/S2.yaml`. It measures what the checks conclude and writes no
record; the full output is `probe-s2-baseline.json` in the output directory of the driver, the evidence files of the
checks are `status/S2.1-complete-sync-recovery.json` and `status/S2.3-written-after-sync.json`. The judgement
(a prediction table against `History.observe`, the exit statuses of the steps) is unit-tested in
`tests/test_s2_context.py`, `tests/test_s2_checks_baseline.py` and `tests/test_s2_observe.py`.

Runs: 2 VM runs, 101 s and 153 s (run 2 adds the control). Run 2 reproduced every outcome of run 1; the evidence
differs only in the text of the fault event and the output of the steps.

```sh
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-s2-baseline
```

## S2.1: a failure after a complete sync (runs 1, 2)

Base set (16 files on three disks), snapshot sync, `snapsync complete` exit 0, the model's layout equal to
`snapraid locate`; arch0 fails by `device-ioerror`; steps 1 to 3 (detect, lock, replace by spare0); graceful
shutdown, the failed disk's image replaced by an empty one, boot; recover, verify, resync, unlock.

| Observable | Result |
| --- | --- |
| every step exits 0 | yes: `every file of arch0 recovered`, `Everything OK`, `covered`, `unlocked` |
| the 6 files of `predictions.S2.1` | bit-exact under their paths on the replacement; no mismatch |
| arch1 and arch2 | unchanged |
| after the resync | `snapsync complete` 0, `snapsync covered` 0 |
| after the unlock | no location of the SnapRAID config on a read-only mount |
| outcome | `pass` (50 s each) |

## S2.3: files written after the sync (runs 1, 2)

The same array, then `new/w0.bin` and `new/w1.bin` through the mover path (both landed on arch1); arch1 is
unplugged (`device-unplug`); the sequence as above.

| Observable | Result |
| --- | --- |
| steps | all exit 0; `recover` exit 0 |
| the 5 synced files of arch1 | bit-exact |
| `new/w0.bin`, `new/w1.bin` | not on the replacement; the check reports both unrecoverable from the history (`branch/new/w0.bin`, `branch/new/w1.bin`) |
| does the `recover` step name them | no (`recover_names_them` is empty): SnapRAID does not know them, as probe T057 measured |
| arch0 and arch2 | unchanged |
| outcome | `pass` (51 and 52 s) |

## Negative control: S2.1 with `corrupted-parity` (run 2)

The control overwrites the parity file after the sync and before the failure of arch0 (`controls/corrupted-parity.sh`,
`flagged-by: S2.1`).

| Observable | Result |
| --- | --- |
| `recover` | exit 3, lists all six files of arch0 as unrecoverable |
| `verify` | exit 0 |
| `resync` | exit 1: `snapsync: sync exited 1; every snapshot is kept` |
| the 6 files | observed `differs` (renamed `*.unrecoverable`), the prediction says recoverable |
| outcome | `fail` (50 s), as the control requires; the failures name the exit statuses of `recover` and `resync` and every file |

## Findings

1. S2.1 and S2.3 reach the verdicts the draft predicts on the real stack, twice with identical evidence; the
   persistent lock of T066a and the boot at `boot:after-failure` do not disturb the sequence.
2. The checks can fail: the corrupted-parity control makes S2.1 `fail`, and the resync refuses to make the loss
   final (sync exit 1, every snapshot kept).
3. The recover step does not name files written after the last sync (S2.3), so the "reported as unrecoverable" of the
   spec is met by the check from the history, as the criteria draft reads it.

## UNVERIFIED

- S2.3 with the unsynced files on a disk that fails by `device-ioerror` instead of `device-unplug`.
- The checks under `--mode record` of the driver (task T074 wires them).
