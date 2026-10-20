# Probe result: the S2 fault kinds kill-sync-at-position and write-error (task T063)

Measured on 2026-10-21 with the probe `probe-s2-faults` (`guest_s2.nix` with `probe_s2_faults.py`), one VM
test at a time, KVM, machine state on an ext4 disk. It runs `kill_sync_at_position`, `wait_sync_position` and
`write_error` of `testlib/faults.py` against the real SnapRAID 14.4 and snapraid-btrfs `6492a45` of the
`server` node (profile `small`, the pins of probe T057), with the archive-lock wrappers of `nix/archive-lock.nix`
in PATH. It measures and never writes a record; the full output is `probe-s2-faults.json` in the output
directory of the driver. The unit tests are in `tests/test_faults.py`.

Each phase adds 64 MiB per archive disk (256 positions of 256 KiB) and syncs it with `--gui --bw-limit=20M`;
K = 120 positions into each phase's new data (phase `plain` from position 0, `snap` from 256, `write` from 512).

Runs: 4 VM runs, 62 to 73 s. Run 1 used K as an absolute position, so the syncs of phases `snap` and `write`
were stopped at their first logged line (`run:pos:256:1`, `run:pos:512:1`); fixed in the probe. Runs 2 to 4:
`write-error` on parity0 (run 2), on arch1 (run 3), on arch1 with the page cache emptied before the sync
(`PROBE_DROP_CACHES=1`, run 4).

```sh
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-s2-faults
SPIKE_STATE_DIR=<...> PROBE_WRITE_ERROR=arch1 PROBE_DROP_CACHES=1 nix run ./spikes/phase0#probe-s2-faults
```

## kill-sync-at-position (runs 1 to 4)

| Phase | Processes before the kill | Kill | Result |
| --- | --- | --- | --- |
| `plain`: `snapraid -l LOG --gui --bw-limit=20M sync` through the archive-lock wrapper | the detached shell and one `/nix/store/...-snapraid-14.4/bin/snapraid -l ... sync` | one process matched `^/[^ ]*/bin/snapraid `; killed after `run:pos:153`, `134`, `130` (runs 2, 3, 4) | exit 137; no SnapRAID process left; `snapsync covered --plain`: `diff exit 2, summary:exit:unsynced, content_info:block_unsynced:256`; the next sync `Everything OK` and `covered` |
| `snap`: `snapsync sync --log LOG -- --gui --bw-limit=20M` | the shell, `bash .../snapsync sync`, `bash - .../.snapraid-btrfs-wrapped -c /etc/snapraid.conf --noninteractive sync -l LOG --gui --bw-limit=20M`, and one `/nix/store/...-snapraid-14.4/bin/snapraid -c /tmp/.snapraid-btrfs-wrapped.XXXX sync ...` | only the SnapRAID process matched; killed after `run:pos:376`, `394`, `389` | snapraid-btrfs `line 1310: <pid> Killed`, `snapsync: sync exited 137; every snapshot is kept`, exit 137; `snapsync complete` rc 1, `newest snapraid-btrfs snapshot 2 is syncing` on all three configs; the next `snapsync sync` rc 0, set 3 `synced`, `complete` and `covered` rc 0 |

The kill came at the first logged line at or past K: lines are at most one per second, 19 to 20 positions
apart at this bandwidth (probe T057), so the kill lands up to about 20 positions past K. The processed
boundary lies 1 to 2 positions further (probe T057); the later S2.4 check must derive that boundary from
the parity file rather than from the log.

## write-error (runs 2 to 4)

| Disk | Run | Position at the fault | Result |
| --- | --- | --- | --- |
| parity0 | 2 | `run:pos:639` | sync exit 1; `DANGER! Unexpected input/output error in disk parity.`, `msg:fatal_hardware: Error advising parity file ... Input/output error.`, then `Error writing parity file ... Read-only file system.` and `Stopping at block 702`; the kernel log: write I/O errors, `JBD2: I/O error when updating journal superblock`, `EXT4-fs error ... Detected aborted journal` (the parity filesystem turned read-only) |
| arch1, page cache warm | 3 | `run:pos:639` | sync **exit 0**, `Everything OK`, `summary:exit:ok`, no I/O error in the log or the kernel log: the sync only reads the data disks, and the data written just before came from the page cache |
| arch1, page cache emptied before the sync | 4 | `run:pos:632` | sync exit 1, `68 io errors`; from position 700 on: `error_io:700:arch1:branch/c.bin: Read error at position 188. Input/output error.` and `msg:fatal_hardware: DANGER! Unexpected input/output error in disk arch1.`; the kernel log: read I/O errors and the btrfs read error counter of the device rising |

Consequences, for the S2.4 device-failure variant (T069) to decide, not decided here:

- A sync never writes to a data disk, so `write-error` on an archive disk acts on the sync's reads, and only
  for data that is not in the page cache. With the cache warm the failure stays invisible to the sync and its
  parity is computed from the cached, correct data.
- With the cache emptied, positions between the fault and the first read error (632 to 699 in run 4) were
  still served from read-ahead.

## UNVERIFIED

- The single-block `blkdebug` form of `write-error` (not built).
- `write-error` on a data disk during a snapshot-supported sync, and on the parity disk during one.
- A kill of the snapraid-btrfs shell instead of the SnapRAID process (the contract kills SnapRAID).
