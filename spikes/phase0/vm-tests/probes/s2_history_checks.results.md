# Probe result: checks S2.2 and S2.9 on the S2 node (task T068)

Measured on 2026-10-23 with the probe `probe-s2-history-checks` (`vm-tests/s2/default.nix` with
`probe_s2_history_checks.py`, budget `probe`), one VM test at a time, KVM, machine state on an ext4 disk. It runs the
two checks of `vm-tests/s2/checks_history.py` through `testlib.recorder` with one lab for both, against the real
kernel, mergerfs, SnapRAID 14.4, snapper 0.13.1, snapraid-btrfs `6492a45`, the archive lock and `guest/recover.sh`,
on the criteria draft `docs/phase0/criteria/S2.yaml`. It measures what the checks conclude and writes no record; the
full output is `probe-s2-history-checks.json` in the output directory of the driver, the evidence files of the checks
are `status/S2.2-history-plain.json` and `status/S2.9-history-snapshot.json`. The judgement (a prediction table
against `History.observe`, the exit statuses, the listing, the survivors, the value of the workflow) is unit-tested in
`tests/test_s2_checks_history.py` and `tests/test_s2_context.py`.

Runs: 2 VM runs, 98 s and 103 s. Run 2 reproduced every outcome of run 1; the evidence differs only in the text of the
fault event and the output of the steps.

```sh
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-s2-history-checks
```

The history `changes_after_sync` (both runs): `b/b1.bin` changed at offsets 17 and 262161, `b/b2.bin` at offset 17,
`c/c2.bin` deleted, `a/a4.bin` changed at two offsets, `new/n0.bin` and `new/n1.bin` added through the mover path
(both landed on arch1); arch0 fails by `device-ioerror`; the sequence with the boot at `boot:after-failure`.

## S2.2: plain SnapRAID (runs 1, 2): `fail`

| Observable | Predicted (draft) | Measured |
| --- | --- | --- |
| `a/a0.bin` (positions 0, 1) | lost | lost: the fix log says `unrecoverable`, the file differs |
| `a/a1.bin` (positions 2 to 4) | lost | lost |
| `a/a2.bin` (position 5) | **lost** | **bit-exact**: the fix log says `recovered` |
| `a/a3.bin` (positions 6 to 8) | lost | lost |
| `a/a4.bin`, `a/a5.bin`, synced versions | recovered | recovered |
| `a/a4.bin`, unsynced version | gone | gone (the file holds its synced version) |
| the `recover` step | exit 3, lists every lost file | exit 3, lists `a/a0.bin`, `a/a1.bin`, `a/a3.bin`; nothing beyond them |
| `verify`, `resync`, `unlock` | exit 0 | exit 0 each; `covered` |
| survivors arch1, arch2 | keep the content of the history | keep it; the changed files hold their changed content, `c/c2.bin` stays deleted |

The check fails on exactly one file: the prediction says `a/a2.bin` is lost, SnapRAID recovers it, and the recover step
rightly does not list it (`mismatches`: `a/a2.bin (synced): predicted unrecoverable, observed bit_exact`; `failures`
add `recover did not list a/a2.bin`). The fix log and the observation agree.

## S2.9: the snapshot workflow (runs 1, 2): `pass`

Every step exits 0; all six synced files of arch0 are bit-exact; the unsynced content of `a/a4.bin` is gone; the
survivors keep the content of the history. The value of the workflow (SC-006), measured from the same history:

| | Files of arch0 |
| --- | --- |
| recovered by plain SnapRAID (S2.2) | `a/a2.bin`, `a/a4.bin`, `a/a5.bin` |
| recovered by the snapshot workflow (S2.9) | all six |
| recovered by the workflow and not by plain SnapRAID | `a/a0.bin`, `a/a1.bin`, `a/a3.bin` |

## Findings

1. **The block model of the draft is too coarse for S2.2.** `testlib/parity_model.py` counts a position as missing
   when the *file* of a surviving disk changed, at every position the file occupies. SnapRAID blocks a position
   only when the *block* changed. `b/b2.bin` (positions 3 to 5) was changed in its first block only, so position 5
   still rebuilds and `a/a2.bin` (position 5) comes back. This is the question the changelog of the criteria draft
   left for the shakedown (T075): the structure of the model changes before the freeze, if the observation shows it.
   By hand, with changed blocks only (b1: positions 1, 2; b2: position 3; c2 deleted: position 6), arch0 loses exactly
   `a/a0.bin`, `a/a1.bin` and `a/a3.bin` and recovers `a/a2.bin`, `a/a4.bin`, `a/a5.bin`: the measured set. This
   probe changes neither the model nor the predictions (rule 2 of the criteria); T075 owns it.
2. S2.9 reaches its predicted verdict twice; the snapshot workflow recovered three files that plain SnapRAID lost from
   the same history.
3. Both checks leave the array unlocked and covered (`resync` reports `covered`, `unlock` exit 0).

## UNVERIFIED

- Whether the changed block alone decides, as computed by hand, for the other predictions of the draft (S2.4 uses
  the same rule per position): the refined model is not built or compared here.
- The checks under `--mode record` of the driver (task T074 wires them).
