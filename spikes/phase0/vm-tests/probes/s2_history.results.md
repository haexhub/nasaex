# Probe result: the S2 history helper on the S2 node (task T065)

Measured on 2026-10-22 with the probe `probe-s2-history` (`vm-tests/s2/default.nix` with
`probe_s2_history.py`, budget `probe`), one VM test at a time, KVM, machine state on an ext4 disk. It runs
`vm-tests/s2/history.py` against the real kernel, mergerfs, the mover primitives of `guest/mover-effects.sh`,
SnapRAID 14.4, snapper 0.13.1 and snapraid-btrfs `6492a45` of the `server` node (profile `small`). It measures and
never writes a record; the full output is `probe-s2-history.json` in the output directory of the driver. The
bookkeeping is unit-tested in `tests/test_s2_history.py`.

Runs: 2 VM runs, 115 s and 107 s, both with the committed code; run 2 reproduced every outcome of run 1 (the
only difference is the print order of one set).

```sh
nix build ./spikes/phase0#driver-probe-s2-history
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-s2-history
```

## Reset (runs 1, 2)

| Reset | Seconds (run 1 / run 2) | Result |
| --- | --- | --- |
| first boot | 48.3 / 39.7 | blank images, `spike-mkfs all`, every mount, both pools, the snapper subvolumes |
| after `device-ioerror` of arch2 and its replacement by spare2 | 25.9 / 23.0 | `find /mnt/archive/*/branch -mindepth 1` empty; a direct 1 MiB read of arch0, arch1, arch2 and every spare succeeds; `blkid -p` rc 2 (no signature) on every spare |
| after `device-unplug` of arch0 and its replacement by spare1 | 25.2 / 25.0 | the same: arch0 is attached again, every spare blank |

A reset after the first costs about 25 s, so the roughly 14 disk failures of S2 need about 6 min of the
25-minute budget `s2` for resets.

## History (runs 1, 2)

| Step | Result |
| --- | --- |
| populate 7 files on the three disks (one of 0 bytes), plain sync | rc 0; `layout_differences` empty: the model's layout equals `snapraid locate` for every file; `compare` bit-exact on every disk |
| `add` of two files with `not_on=arch2` | both landed on arch1 (the base pool's `mfs` picks the disk with the most free space); visible through the share |
| `modify` of a synced arch2 file at two offsets, `delete` of a synced arch1 file, a file written to arch0, all before a snapshot sync | `changed`: arch1 `b/two.bin`, arch2 `c/two.bin`; `unsynced`: arch0 `a/three.bin`, arch1 the two added files, arch2 `c/two.bin` |
| snapshot sync (`snapsync sync`), `snapsync complete` | rc 0, rc 0; `layout_differences` empty: the deleted file's position 4 on arch1 went to the first added file, the changed arch2 file took its freed positions again (removal before insertion, by physical start) |
| `modify` of `c/one.bin` and `delete` of an arch0 file after that sync | `changed`: arch0 `a/two.bin`, arch2 `c/one.bin`; `unsynced`: arch2 `c/one.bin` |
| `parity_model.after_sync(..., ["arch2"], changed, unsynced, snapshot=True)` | the three synced arch2 files recoverable (`intact`), the unsynced version of `c/one.bin` not |
| `fail("arch2")` (`device-ioerror`), `replace("arch2")` (detect, lock, replace by spare2), `recover recover arch2` | rc 0; `recovered` for all three; `compare("arch2")`: 3 of 3 bit-exact as of the sync (the change after the sync is reverted); no `*.unrecoverable` file |
| after the second reset: populate, plain sync, `fail("arch0", "device-unplug")`, `replace("arch0", "larger")` | rc 0; `recover replace` checked that the data mount of arch0 comes from spare1 (`findmnt` source `/dev/vdi[/data]`) |

## UNVERIFIED

- An `add` that the base pool would place on the disk that is to fail: refused by the helper (unit-tested),
  not provoked in the VM; a scenario that needs a new file on a given disk picks the failing disk so that `mfs`
  never chooses it.
- A sync interrupted mid-history (S2.4): `attempted` holds the layout the sync recorded (unit-tested), the
  model's use of it belongs to T069.
