# Probe result: SnapRAID allocation, killed sync, snapshot workflow and headroom (task T057)

Measured on 2026-10-20 with the probe `snapraid.nix` (`snapraid_probe_setup.py` +
`probe_snapraid_snapshots.py` + `probe_snapraid.py`, pure helpers in `snapraid_observe.py`, unit tests in
`tests/test_probe_snapraid.py`), one VM test at a time, KVM, machine state on an ext4 disk. Research
section 6 items 4, 15, 16, 17 and 19. A probe never writes a record; the full output is
`probe-snapraid.json` and the SnapRAID `-l` logs (`<phase>/logs/`) in the output directory of the driver
(evidence, not committed). Nothing below edits the spec, the research or the criteria; S2.yaml does not
exist yet, so the changelog text for T066 is the last section.

Runs: 7 VM runs. Run 1 phase `alloc` (31 s). Runs 2 to 4 phase `kill`, each stopped by a probe defect that
was fixed before the next run (the process name of the kill, the driver waiting for the detached sync, the
size of one guest argument); run 4 reached the state after the kill. Run 5 phases `kill`, `snap`,
`headroom` (103 s; its headroom part was confounded, see item 19). Run 6 all four phases (109 s). Run 7 all
four phases with the committed probe (commit `27994c3`, 119 s). Repetitions are named per finding; run 7
reproduced every outcome of run 6 except the kill boundary (one position apart, item 4c).

## Environment

- Profile `small`, VM `server` only (1536 MiB, 2 vCPUs). Kernel 6.18.56; `SnapRAID CLI v14.4`; snapper
  0.13.1; `snapraid-btrfs 0.14.1+git` at `6492a45` (`nix/snapraid-btrfs.nix`); btrfs-progs v6.19.1; bash 5.3.9.
- Pinned sources read: SnapRAID 14.4 (`nixpkgs#snapraid.src`), snapper 0.13.1 (`nixpkgs#snapper.src`),
  snapraid-btrfs `6492a45ad55c389c0301075dcc8bc8784ef3e274`, nixpkgs `a033e66` (NixOS modules). Citations
  below are `file:line` in these trees; "manual" is `doc/snapraid.d` of SnapRAID 14.4.
- The nixpkgs `snapraid` is a makeWrapper script that execs `.snapraid-wrapped` with argv[0] set to the
  script path: the process name is `.snapraid-wrapp`, its command line starts with
  `/run/current-system/sw/bin/snapraid` (direct) or `/nix/store/...-snapraid-14.4/bin/snapraid` (from
  snapraid-btrfs). `pkill -x snapraid` finds nothing (run 2); the probe kills `pkill -9 -f '^/[^ ]*/bin/snapraid '`.
- The driver shell has `LANG=en_US.UTF-8` and `LOCALE_ARCHIVE` set; a systemd service gets `LANG=en_US.UTF-8`
  (`systemd-run ... env`; NixOS `nixos/modules/config/i18n.nix:201-218`).
- Array: `data archN /mnt/archive/archN/` (the whole `data` subvolume), `parity /mnt/parity/snapraid.parity`
  (ext4), `content` on the system disk first, then the parity disk and the `content` subvolume of each archive
  disk, `exclude /.snapshots/`, default block size 256 KiB. Snapper: `services.snapper.configs.archN =
  { SUBVOLUME = "/mnt/archive/archN"; FSTYPE = "btrfs"; }`, which writes `FSTYPE="btrfs"` and
  `SUBVOLUME="/mnt/archive/archN"` to `/etc/snapper/configs/archN` and `SNAPPER_CONFIGS="arch0 arch1 arch2"`.
- A disk "fails" by being unmounted and wiped (`wipefs -a`) in the running VM and is "replaced" by a new
  btrfs filesystem with the same label on the same blank device (`data`, `content`, `branch`, `trash`, and
  where named `.snapshots`). This is not the harness path `device_failed_at_boot` (image swap across a power
  off); for SnapRAID and snapper the effect is the same blank filesystem with a new UUID.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-snapraid
SPIKE_STATE_DIR=<short directory on an ext4 disk> nix run ./spikes/phase0#probe-snapraid
SPIKE_STATE_DIR=<short directory on an ext4 disk> PROBE_PHASES=kill nix run ./spikes/phase0#probe-snapraid
```

## Item 4a: the block allocation model (measured, 3 runs of `alloc`, 3 of `kill`, 3 of `snap`)

Rule (SnapRAID 14.4): in each sync, per data disk, removed and rewritten files are removed first
(`cmdline/scan.c:1697-1718`; a rewritten file is a removal plus an insertion, `scan.c:1085-1110`); then the
new and rewritten files, sorted by their FIEMAP physical start (`scan.c:1755-1771`, default order
`cmdline/state.c:558-559`, offset `cmdline/unix.c:1020-1040`), each take the lowest positions without a live
block of that disk, starting at position 0 (`scan.c:207-293`; `first_free_block` is 0 per process,
`cmdline/elem.c:778`). Blocks of deleted files are reused, holes are filled and a file is split across
holes. `snapraid --test-fmt disk locate` reports first offset, span and fragment count per file
(`cmdline/locate.c:47-55`); the probe compares that with first-fit in physical order and checks the exact
positions against the parity file (the first parity is the XOR of the data blocks, `raid/int.c:8-34`; a
short last block is zero padded, `cmdline/handle.c:278-279`).

| Sync (alloc) | Disk | Files in write order (physical start, 4 KiB blocks) | Positions (`locate`) |
| --- | --- | --- | --- |
| 1 | arch0 | z0 4 blocks (3328), m0 2.5 (3584), c0 1 (3744), q0 3 (3808) | z0 0-3, m0 4-6, c0 7, q0 8-10 |
| 1 | arch1 | y1 6 (3328), d1 2 (3712), k1 5 (3840) | y1 0-5, d1 6-7, k1 8-12 |
| 1 | arch2 | x2 3 (3328), e2 4 (3520), n2 2 (3776), t2 1.25 (3904) | x2 0-2, e2 3-6, n2 7-8, t2 9-10 |
| 2 | arch0 | z0 deleted; c0 rewritten (4000); f0 6 added (4064) | c0 0, f0 1-3 + 7 + 11-12 (3 fragments), m0, q0 kept |
| 2 | arch1 | d1 deleted; g1 1 added (4160) | g1 6, y1, k1 kept |

- First-fit in physical order matched `locate` for every file of both syncs (0 differences, runs 1, 6, 7);
  the alphabetical order mismatched 11 of 11 files. The parity file equalled the XOR of the hypothesised
  blocks at all 15 positions 0 to 14 (runs 1, 6, 7), so the positions are exact, including the three
  fragments of f0. A rewritten file moves: c0 went from 7 to 0.
- Physical order is not write order on btrfs once space was freed: in `kill`, `n2.bin` was written after
  `n1.bin` but got a lower physical start (the extents freed by the deleted `h.bin`), so n2 took positions
  160-319 and n1 640-759 (runs 4 to 7, matched by `locate` after the kill).
- SnapRAID's own logs give the positions too: `fixed:<pos>:<disk>:<path>: Fixed data error at position
  <file block>` and `unrecoverable:<pos>:...` (`cmdline/check.c:1319, 1337, 1408`); every `fixed:` position
  matched the hypothesis.

## Item 4b: the `-l` log and the `run:pos` frequency (measured, runs 4 to 7)

- `run:pos` and `run:begin` lines exist only with the undocumented `--gui` option (`cmdline/state.c:4753-4756,
  5231-5257`; `snapraid.c:737`). Without it the `-l` log of a sync had 0 such lines (alloc sync 1, kill sync
  0, runs 1, 4 to 7).
- With `--gui` a line is written at most once per wall-clock second (a new line only when `time(0)` changed,
  `state.c:5102-5110`) and flushed at once (`log_flush`, `state.c:5257`; other tags are not flushed,
  `cmdline/support.c:390-417`); the probe saw each new line 0.87 to 1.20 s after the previous one.
- Format: `run:begin:<blockstart>:<blockmax>:<countmax>`, `run:pos:<parity position>:<positions
  processed>:<bytes>:<percent>:<eta>:<MB/s>:<cpu %>:<elapsed s>:<temp>:<steady>`. Real lines (run 6):
  `run:begin:0:760:600`, `run:pos:160:1:786432:0::::0::`, `run:pos:397:238:187170816:39:18:15:0:13::`,
  `run:pos:416:257:202113024:42:17:15:0:14::`.
- Steps: with `--bw-limit=20M` (a global limit on bytes read, manual 846-849, `cmdline/bw.c`) and 2 to 3 data
  blocks per position, consecutive lines were 19 to 20 positions apart (min 8 to 11 for the first step; 14 to
  15 lines per run). Unthrottled, a forced full sync of 640 positions wrote 1 or 2 lines (positions 0 and 69)
  and took 2.5 s including SnapRAID's fixed 2 s pause before syncing (`snapraid.c:1892-1900`). A kill at a
  position K is therefore only meaningful with a bandwidth limit in the `small` profile.

## Item 4c: the state a killed sync leaves, and recovery (measured, runs 5, 6, 7; run 4 to the state)

Plain SnapRAID. Before the killed sync: arch1 `q1` (positions 480-639) and arch2 `h` (160-319) deleted;
added arch0 `f` (160-639), arch2 `n2` (160-319), `n1` (640-759); arch2 keeps `a` (0-159), `c` (320-479), `d`
(480-639). Sync range 160-759 (`run:begin:0:760:600`). Killed with SIGKILL when the log showed
`run:pos` >= 400.

- Kill: after `run:pos:400` (run 5) and `run:pos:416` (runs 6, 7); exit 137; no SnapRAID process left; the
  lock file `snapraid.content.lock` stays (0 bytes) and the next commands run.
- Parity per position (XOR of the old and of the new layout): 0-159 unchanged, 160-402 / 160-418 / 160-417
  new (runs 5 / 6 / 7), the rest old, no position matching neither. The true boundary was 1 to 2 positions
  past the last logged line.
- Content file: written once, before any parity (`cmdline/sync.c:1562-1587`; no autosave, whose unit is
  whole GB, manual "autosave"): content mtime = sync start, parity mtime = kill time; `locate` after the kill
  shows the new layout. `snapraid status`: `WARNING! The array is NOT fully synced.` / `You have a sync in
  progress at 21%.` with `summary:exit:unsynced` and `content_info:block_unsynced:600`: all 600 positions stay
  unsynced although 241 to 257 were processed by the last logged line; the 21 % is (positions without
  unsynced blocks)/(all positions) (`cmdline/status.c:373-375`, `state.c:2010-2028`), not the progress of the
  killed run. `snapraid diff`:
  exit 2, `No differences`, `msg:error: The last sync was interrupted. Run it again!`, `summary:exit:unsynced`
  (`scan.c:2029-2062`).
- The next sync redoes the whole range, including positions done before the kill (after the fix:
  `run:begin:0:640:480`, positions 160-639; snapshot variant 251 of 255) and ends with `diff` exit 0.
- `snapraid -d arch2 fix` after arch2 failed (single parity), identical in runs 5, 6, 7:

| arch2 file | Positions | Block state in content | Result |
| --- | --- | --- | --- |
| a | 0-159 | BLK, no change at its positions | recovered, bit-exact |
| c | 320-479 | BLK; arch0 `f` new (CHG over empty) at the same positions | recovered, bit-exact: below the boundary with the "parity updated" strategy, above it the "parity not updated" strategy zeroes the CHG block (`repair_hash_error`, `recover_sync:430:1: Failed with 1 attempts`, `fixed:430:...`; `check.c:341-580`) |
| d | 480-639 | BLK; arch1 `q1` DELETED, positions not processed | unrecoverable (`recover_strategy_error:480: No strategy to recover from 2 failures with 1 parity with hash`); `-i` with a copy of q1 recovered nothing; `--test-import-content` with the same copy recovered it bit-exact |
| n2 | 160-319 | CHG (new), all positions processed | unrecoverable (`recover_strategy_error:160: No strategy to recover from 1 failures with 1 parity without hash`) |
| n1 | 640-759 | CHG (new), not processed | unrecoverable |

  Why (code): a CHG block has no hash of its new content (`cmdline/elem.h:722-727`), and with one parity
  level a single failure without a hash has no strategy (`check.c:183-194, 234-278`). A DELETED block of
  another disk is taken as zero for "parity updated" and needs its old data for "parity not updated"
  (`check.c:1017-1033, 497-510`); that data comes only from the hash import set (`state_import_fetch`), which
  `-i` does not fill: `-i` feeds `state_search` (match by size and mtime, then hash), used only for BLK/REP
  blocks (`snapraid.c:2009-2013`, `check.c:366-369`, `cmdline/search.c:112-134`); the hash import set comes
  from the hidden test option `--test-import-content` only.
- After a completed sync, `-i` does work for a deleted file: in the S2.2 history (next section), a retry
  with a copy of the deleted `y1` (timestamps kept) fixed the positions only `y1` blocked (x2 0, e2 4-5;
  runs 6, 7).

## The S2.2 history, plain SnapRAID (measured, runs 1, 6, 7)

After sync 2 of item 4a: arch0 `f0` rewritten in place (positions 1-3, 7, 11-12), arch1 `y1` deleted (0-5),
a file added on arch0, on arch2 `w2` added and `t2` rewritten; arch2 fails; `snapraid -d arch2 fix`. Changed
positions of the surviving disks: 0-5, 7, 11-12. The per-position rule marks x2 (0-2), e2 (3-6) and n2 (7-8)
affected and t2 (9-10) not. Result: t2 recovered bit-exact in its synced version; x2, e2, n2 renamed
`.unrecoverable` (exit 1, `summary:exit:unrecoverable`), with their unaffected blocks fixed (`fixed:` e2 6, n2
8); `w2` is unknown to the content and not named. The fix log also names the changed blocks of the surviving
disks (`unrecoverable:1:arch0:branch/a/f0.bin: ...`, `error:0:arch1:branch/y1.bin: Open error ...`).

## Item 4d: which command says "parity-covered" (measured, runs 1, 5, 6, 7)

- Whole array, plain SnapRAID: `snapraid -l LOG diff` exit 0 with `summary:exit:equal` and
  `content_info:block_unsynced:0` (after every completed sync). Exit 2 means a difference
  (`summary:exit:diff`, the changed paths as `scan:add|remove|update|move|copy|relocate|restore:<disk>:<path>`
  tags, e.g. `scan:update:arch0:branch/a/c0.bin: 262144 1700000003.21 -> 262144 1700000012.84`) or an
  interrupted sync (`summary:exit:unsynced`, no path named). Manual: diff 491-526, exit codes 523-524.
- A path set: `diff` takes no path filter (`-f` is for `check`/`fix`, manual 749-758). Derived from the
  measured outputs (coverage itself is proven by recovery, research D7): with `summary:exit:equal` for the
  whole array, a path is covered if `snapraid list` lists it (manual 647-653) with the expected size and
  mtime. With `summary:exit:unsynced` no path can be claimed from SnapRAID's output (the content lists the
  interrupted run's files, `locate` included).
- Snapshot workflow (item 16) below.

## Items 15, 16, 17: snapraid-btrfs and snapper on NixOS (measured, runs 5, 6, 7)

Runtime needs (each measured as a failure first):

1. A non-UTF-8 locale for snapraid-btrfs. It reads `snapper -c <config> get-config` with the separator `|`
   (`snapraid-btrfs:689-690`); in a UTF-8 locale snapper prints box-drawing tables (`client/utils/Table.cc:409-411`).
   In the driver shell, with an explicit UTF-8 locale and in a systemd service, `get-config` printed
   `SUBVOLUME │ /mnt/archive/arch0` and `snapraid-btrfs -c <conf> ls` exited 66 with `No snapper configs
   found for any data drives in /root/snapraid.conf`; with `env LC_ALL=C` it printed `SUBVOLUME |
   /mnt/archive/arch0` and exited 0. A systemd timer would fail the same way unless it sets `LC_ALL=C`.
2. A `.snapshots` subvolume in each data subvolume (root-owned, not world-writable, `snapper/Btrfs.cc:265-298`);
   the NixOS module only writes the config files (`nixos/modules/services/misc/snapper.nix:47-55, 246-275`).
   Without it: `snapraid-btrfs ... fix -d arch2` exited 66 with `/mnt/archive/arch2/.snapshots is not a valid
   btrfs subvolume` (`snapraid-btrfs:216-234`).
3. `--noninteractive`, otherwise a missing snapshot opens a menu that reads stdin (`snapraid-btrfs:1652-1692`);
   with it a missing snapshot exits 68 (`E_SNAPSHOT_NOT_FOUND`).
4. Content files outside the snapshotted subvolume (checked, `snapraid-btrfs:178-204`) and `exclude /.snapshots/`.
   It warns when run as root and runs.

Workflow, same history as `alloc` (item 4a and the S2.2 changes):

- `sync`: new read-only snapshots per config, userdata `snapraid-btrfs=created`, then `syncing`, then `synced`
  on exit 0 (`snapraid-btrfs:1132-1147, 1334-1362`); SnapRAID ran on a temporary config with
  `data archN /mnt/archive/archN/.snapshots/<n>/snapshot/`.
- Evidence of a completed snapshot-supported sync (item 16): (a) the newest snapraid-btrfs snapshot of every
  config has `snapraid-btrfs=synced` (`snapper --jsonout -c archN list --disable-used-space`); (b) `env LC_ALL=C
  snapraid-btrfs -c <conf> -u sync config > synced.conf` gives data lines pointing at that set and changes no
  userdata; (c) `snapraid -c synced.conf -l LOG diff` exits 0 with `summary:exit:equal`, before and after live
  changes (the live `diff` exited 2 with 2 added, 1 removed, 2 updated); (d) each file read from
  `/mnt/archive/archN/.snapshots/<n>/snapshot/<path>` matched its expected hash (11 of 11). Hazard:
  `snapraid-btrfs -u sync diff` re-tagged the synced snapshots `synced` -> `diff` on all three configs
  (`snapraid-btrfs:1350-1355, 1092-1106`), so the marker is not a stable record; a plain `snapraid-btrfs diff`
  takes new snapshots (`snapraid-btrfs:1503-1504`, read, not run), and `dsync` passes `--force-empty`
  (`snapraid-btrfs:1285-1301`, read, not run).
- Replaced disk (item 15): the NixOS config file stays valid (path-based); the new filesystem needs
  `.snapshots`; and snapperd keeps the old disk's snapshot list while the config stays loaded (unloaded after
  30 s unused, `server/snapperd.cc:54-55, 164-168`): right after the replacement `snapper -c arch2 list` still
  showed snapshots 1 and 2 `synced` of the old disk, also after creating `.snapshots`; after `systemctl stop
  snapperd` it showed only 0. Without that stop (killed-sync variant), the fix's pre/post snapshots on the new
  disk got numbers 5 and 6, and `resume` exited 68 with `Invalid snapshot /mnt/archive/arch2/.snapshots/4/snapshot`.
- S2.9 fix: `env LC_ALL=C snapraid-btrfs -c <conf> --noninteractive -v fix -d arch2 -l LOG` used arch2 live
  and arch0/arch1 from their `synced` snapshot 2 (`use_snapshot_fix`, `snapraid-btrfs:1904-1963`; `scrub`
  selection `snapraid-btrfs:742-744`), created pre/post snapshots 1 and 2 on arch2 only, and restored 4 of 4
  arch2 files bit-exact as of the sync (`x2`, `e2`, `n2`, and `t2` in its synced version; the later rewrite of
  t2 and the file `w2` written after the sync are gone, w2 is not named by fix). Plain SnapRAID on the same
  history (S2.2): 1 of 4 (t2), x2/e2/n2 unrecoverable as the per-position rule predicted (changed positions
  0-5, 7, 11-12). Difference, the measured value of the workflow here: 3 files.
- Resync and cleanup: `sync` -> snapshot 3 `synced` on all configs; `cleanup` ran `snapper -c archN rm 1 2` on
  all three, including arch2's `pre-fix`/`post-fix`.
- Killed snapshot-supported sync (threshold 135, killed after `run:pos:154`/`148`/`148`): wrapper exit 137
  (`line 1310: <pid> Killed "${snapraid_command[@]}"`), the new set stays `syncing` (snapshot 4), `status`
  `block_unsynced:251`. After the kill arch0 `c0` (position 0, shared with arch2 `x2`) was rewritten; arch2
  failed; the fix used snapshot 4 (`syncing`) for arch0/arch1 and restored x2, e2, n2, t2 bit-exact; the new
  `n3` (CHG, positions 11-90, processed) was unrecoverable, as in item 4c. `resume` with a fresh snapperd
  exited 68 (`Snapshot resume not found for config arch2`: the new disk has no `syncing` or `synced`
  snapshot); a new `sync` processed 251 of 255 positions and ended with `diff` exit 0.

## Item 19: headroom on the `small` archive disks (measured, runs 6, 7; run 5 confounded)

- Free after mkfs and the subvolumes (`df` avail): arch0 586153984 B of 640 MiB, arch1 948568064 B of
  1024 MiB, arch2 451936256 B of 512 MiB; data `single`, metadata `DUP` (32 MiB on arch0/arch2, 51 MiB on
  arch1).
- arch2 alone, own config, snapshot retained: 6 x 32 MiB written and synced (snapshot 1 `synced`), avail
  250593280; 4 of the 6 deleted: avail unchanged; new data: 7 x 32 MiB complete, the 8th failed with
  `ENOSPC` at `write()` after 15712256 bytes and left a partial file of that size; total 250593280 bytes =
  the avail. Deleted data stays allocated until a later sync and `cleanup`, so with every byte rewritten
  before the next sync a disk holds half its free space (derived from these numbers).
- At ENOSPC: `snapper -c arch2 create` worked (rc 0). `snapraid-btrfs sync` created its snapshot (`syncing`),
  scanned, and failed writing the content copy on arch2's own `content` subvolume: `Error writing the content
  file '/mnt/archive-content/arch2/headroom.content.tmp' (in flush before crc). No space left on device.`,
  rc 1. The content stayed the previous one everywhere (`status`: 6 files, `block_unsynced:0`): all copies are
  written as `.tmp`, verified, then renamed (`state.c:4601-4614`), and the content precedes any parity write.
- Deleting the partial file freed nothing (the snapshots of the failed attempts hold it); the next sync
  failed the same way with one more `syncing` snapshot; `cleanup` exited 0 and deleted nothing (no `synced` set
  newer than 1); avail stayed 0. A full data disk with retained snapshots and a content copy on that disk
  cannot complete the sync that would release space.
- Run 5 deleted all six files instead of four: the sync refused (`WARNING! All the files previously present in
  disk 'arch2' at dir '/mnt/archive/arch2/.snapshots/10/snapshot/' are now missing or have been rewritten!`,
  rc 1, `scan.c:1846-1891`, finding F3) before any space question.

## Contradictions with the spec, research and tasks

- Research 3.4: "the progress log of `-l` carries tag lines `run:pos:<blockpos>:...`" and the fault-points
  contract (`kill-sync-at-position`: "Wait until the `snapraid -l` log shows `run:pos:<block>`"). Measured:
  only with `--gui`, at most one line per second, and the processed boundary is 1 to 2 positions past it.
- Spec, prediction S2.4: "Files whose parity blocks (at every position they occupy) were completed and verified
  before the interruption, and whose positions were not otherwise changed, are recoverable". Measured with
  single parity: a file added in the interrupted sync (CHG) is unrecoverable even when every position was
  processed (n2, n3); an old file is recoverable on both sides of the boundary unless another disk deleted
  data at its unprocessed positions (d). The prediction needs block states, not only positions.
- Research 3.4: "`-i/--import` lets `fix` use files moved out of the array". Measured: not for deletions the
  interrupted sync already recorded (d); it works after a completed sync (y1).
- Research 7.3: "the snapper userdata that the tool uses to record completion is the candidate evidence".
  Measured: `-u sync diff` rewrites it; the evidence needs the `diff` against the synced set too (item 16).
- Research 3.4 ("one [content file] on the system disk and one on each archive disk and the parity disk") with
  D3 (retained snapshots): measured, the copy on a full data disk blocks the sync (item 19).
- Research D7 and plan O2: "The block-allocation model of SnapRAID is UNVERIFIED". Measured as above (item 4a).
- Tasks T057: "write the findings into the S2 criteria `changelog`"; S2.yaml does not exist (T066), so the note
  is below, as for S1.

## What T059 to T065 need

- T059: the rule of item 4a with physical starts from FIEMAP (`filefrag -e`) at sync time, removals before
  insertions, rewritten files moving; recoverability per position and per block state (BLK, CHG, DELETED) as in
  item 4c; checks: `snapraid --test-fmt disk locate`, `fixed:`/`unrecoverable:` tags, parity XOR (`XOR` in
  `snapraid_observe.py`).
- T060: `LC_ALL=C` for every snapraid-btrfs call; `.snapshots` per data subvolume (root:root, 0750);
  `--noninteractive`; after a disk replacement stop snapperd before any snapper call, then `fix -d`, then
  `sync` (not `resume`); never `dsync` or `-u ... diff`; a completed set = exit 0 and `synced` plus the item 16
  check.
- T061: snapshot create, delete and cleanup go through snapperd (DBus), and NixOS starts `snapper-cleanup`
  10 min after boot and `snapper-timeline` hourly (`snapper.nix:296-324`; no timeline snapshots with
  `TIMELINE_CREATE` off): the lock has to cover these routes too.
- T062: the measured sequence: lock; replace (`mkfs`, `data`, `content`, `.snapshots`); stop snapperd;
  `fix -d <disk> -l LOG`; compare; `sync`; `diff` exit 0. After an interrupted sync: keep deleted files of
  surviving disks until the next completed sync (or snapshots), `-i` will not bring them back.
- T063: `kill-sync-at-position` needs `--gui` and `--bw-limit`, matches `^/[^ ]*/bin/snapraid ` (not the
  name), and must take the boundary from the parity, not from the log line; under snapraid-btrfs kill only
  the SnapRAID process (the wrapper then exits 137 and leaves `syncing`).
- T065: phase times 8 to 37 s; a sync of 640 positions 2.5 s; each single command of a failure and
  replacement in the running VM (wipefs, mkfs, subvolume create, mount) took under 1 s.

## UNVERIFIED

- Double parity (with two levels a CHG block could be checked against the second parity, `check.c:192-230`),
  autosave, a device failure or power-off during the sync, `scan:move`/`copy` placement and concurrent
  writers: not run.
- Per-path coverage while another part of the array is unsynced (for example `check -f`): not run.
- The headroom of arch0 and arch1 under the same sequence (only arch2 run), and ENOSPC with all content copies
  off the data disks.
- A replacement across a reboot (snapperd starts fresh) and the snapper timers within a run: not run.
- `--test-import-content` is a test option; it confirmed the cause in item 4c and is no procedure.

## Criteria changelog note (for T066 `S2.yaml`)

> Probe T057 (2026-10-20; SnapRAID 14.4, snapper 0.13.1, snapraid-btrfs 0.14.1+git `6492a45`, kernel 6.18.56,
> btrfs-progs 6.19.1; runs 1 and 4 to 7, outcomes identical across runs except the kill boundary). Allocation:
> per sync and disk, removals (deleted and rewritten files) first, then new and rewritten files in FIEMAP
> physical order take the lowest free positions from 0, filling holes and splitting files; matched by `locate`
> for every file and by the parity XOR at every position; physical order is not write order after deletes.
> `run:pos` lines exist only with `--gui`, one per second (19 to 20 positions apart at `--bw-limit=20M`), and
> the processed boundary was 1 to 2 positions past the last line (3 kills). A killed sync leaves the content
> of its start (all 600 positions unsynced, `diff` exit 2 `summary:exit:unsynced`), parity new below the
> boundary and old above it; the next sync redoes the whole range. Single-parity `fix` after the kill: old
> files recovered on both sides of the boundary; files added by the interrupted sync unrecoverable even when
> fully processed; an old file at unprocessed positions of a block deleted on another disk unrecoverable, and
> `-i` does not help there (it does after a completed sync). Same S2.2 history: plain 1 of 4 failed-disk files
> recovered, snapshot-supported 4 of 4. snapraid-btrfs needs `LC_ALL=C` (exit 66 in UTF-8 locales and in
> systemd services), a `.snapshots` subvolume per data disk, `--noninteractive`, a snapperd stop after a disk
> replacement (stale snapshot list, wrong numbering, `resume` exit 68) and `sync` instead of `resume` after a
> replacement; the `synced` userdata is rewritten by `-u sync diff`, so coverage is the synced userdata plus
> `snapraid -c <config of the synced set> diff` exit 0 `summary:exit:equal`. Headroom: arch2 (512 MiB) has
> 451936256 B free after mkfs; with a retained snapshot new data fails cleanly with ENOSPC at `write()`, a
> snapper snapshot still works, but the snapshot-supported sync fails writing the content copy on that disk and
> `cleanup` frees nothing. The S2 expected values are not changed by this note; the S2.4 prediction and the
> content-file placement are for the maintainer to decide.
