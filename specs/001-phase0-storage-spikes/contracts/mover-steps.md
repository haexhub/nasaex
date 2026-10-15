# Contract: mover step list and hook points

**Feature**: `001-phase0-storage-spikes` | **Used by**: S3 (mover crash safety), S1 scenario 10, S2 recovery drill

This is the artifact the spec calls "the mover step list" (spec, Assumptions): the product mover will
later implement it, and a change to it after Phase 0 reopens S3. In Phase 0 it is implemented by a
throwaway reference mover (`spikes/phase0/guest/`, shell). The step list, the hook protocol and the
invariants below are the contract; the script is not.

Design rule under test: **the mover keeps no state that must survive a crash.** Every run derives its
plan from the on-disk state (pool layer, archive branches, SnapRAID content). S3 shows whether that is
sufficient. If a journal turns out to be necessary, that is an S3 finding and a correction of this list,
not a silent addition.

## Terms

- **Pool layer**: the btrfs subvolume that is the overlay's upper layer (holds new files, copied-up
  files, whiteouts as 0/0 character devices).
- **Archive**: the mergerfs pool over the archive disks (read-only for share clients). The mover writes
  to the archive branches through its own controlled path, never through the share.
- **Item**: a file the run acts on. A run has `n` copy items (pool-only files, including at least one
  that replaces an older archived version) and `m` deletion items (whiteouts whose archived file is to be
  deleted). The S3 fixture uses `n = 3`, `m = 2`.
- **Source-version lock**: per-item coordination held from the M2 source hash through the M5 removal
  decision. Every writer that can replace a pool item must honour the same lock, or use an atomic
  handoff with equivalent semantics. If a stable version cannot be held through removal, the mover
  keeps the pool copy.
- **View refresh**: the procedure S1 scenario 10 records for making the share consistent after the
  archive or the pool layer was changed directly (finding F4 in research). The reference mover calls a
  `refresh` function at the end of the steps that need it; its content is whatever S1 verified.

## Steps

| Step | Name | Action | Durable effects (each is a mid-step point) | Postcondition at `post:M<k>` |
| --- | --- | --- | --- | --- |
| M1 | copy | For each copy item: copy the pool file to a temporary name in the target archive directory, fsync, rename to the final name (for a replacement the rename atomically replaces the old archived version), fsync the directory | `tmp-created`, `tmp-partial` (half of the bytes written, not yet synced), `tmp-data-synced`, `renamed`, `parent-synced` (for the first item), `item-1-done`, `view-refreshed` | Every copy item has a complete archive copy under its final name; pool copies and whiteouts are untouched |
| M2 | checksum | For each copy item: acquire its source-version lock, hash the pool file through the locked version, capture that version's identity (size, mtime, ctime, inode), then hash the archive copy (archive side read after dropping caches so it comes from the disk) and compare | `pool-hashed`, `archive-hashed` (no durable effects; read only) | All hashes equal and the source version remains held for M5; otherwise the run stops at M2, reports it, and changes nothing else |
| M3 | sync | `snapraid sync` for the archive | `sync-pos` (`snapraid -l` progress position reached, or a sync limited with `-B`), see fault-points contract | Sync completed with exit status 0; content file saved |
| M4 | verification | Verify that every copy item is covered by completed parity: no pending changes for the item paths and a successful check of those paths | `checked` (no durable effects) | Parity coverage verified for every copy item |
| M5 | pool copy removal | For each copy item, while still holding its source-version lock: re-read the pool file identity; if it differs from M2 (the file changed meanwhile), release the lock, keep the pool copy and leave the item for a later run; otherwise unlink the pool copy before releasing the lock | `item-validated`, `unlinked` (first item), `item-1-done`, `view-refreshed` | Every unchanged item exists only in the archive; changed items still in the pool. A path-only check followed by unlink is not sufficient |
| M6 | archive deletion | For each deletion item: delete the archived file or directory on its branch, fsync the directory | `unlinked` (first item), `parent-synced`, `item-1-done`, `view-refreshed` | No deletion item exists in the archive; whiteouts still present |
| M7 | second sync | `snapraid sync` | `sync-pos` | Sync completed with exit status 0 |
| M8 | second verification | Verify that the archive deletions are covered by completed parity (no pending changes for the deleted paths) | `checked` | Deletions parity-covered |
| M9 | whiteout cleanup | For each deletion item: remove the whiteout from the pool layer | `unlinked` (first item), `item-1-done`, `view-refreshed` | No whiteout of this run remains; deleted names stay invisible |

Spec crash boundaries (FR-018): after copy, after checksum, after sync, after verification, after pool
copy removal, after archive deletion, after the second sync, after the second verification, after
whiteout cleanup = `post:M1` .. `post:M9`. The start `pre:M1` is added; mid-step points are the effects
in the table.

## Point names

- Boundaries: `pre:M1`, `post:M1` .. `post:M9` (10 points).
- Mid-step: `mid:M<k>:<effect>` with the effects of the table, for the first item of a loop step
  (24 points: M1 7, M2 2, M3 1, M4 1, M5 4, M6 4, M7 1, M8 1, M9 3).
- Total as currently enumerated: **34 points**, each exercised by `kill` (process kill) and `poweroff`
  (QEMU process termination; host-power-loss semantics unverified): **68 cases**. SC-005 requires 100% of the enumerated points; the criteria file freezes
  the list, so changing this table changes the criteria version.
- Case id: `S3.2:<point>:<kill|poweroff>`, for example `S3.2:post:M5:poweroff`.

Injected faults and concurrent changes (S3 scenarios 5 to 9) use named injection points in the same
namespace: `fault:M1:enospc`, `fault:M2:corrupt-archive-copy`, `fault:M3:parity-write-fail`,
`fault:M4:verify-fail`, `inject:modify-pool-after:M1`, `inject:modify-pool-after:M2`. See
[fault-points.md](fault-points.md).

## Hook protocol (reference mover)

1. The mover calls `spike_hook <point>` at every point of the table. With no armed point the call
   returns immediately.
2. The host arms a point by creating the file `armed/<point>` in the guest hook directory (a tmpfs, so a
   power-off clears it; the host re-arms after boot where needed).
3. An armed hook writes `reached/<point>` and then blocks reading the FIFO `go/<point>`. It never calls
   `sync` or touches any disk.
4. The host waits for `reached/<point>`, then does one of: kill the mover process (`kill`), power the
   machine off (`poweroff`), fail a device, modify a file (`inject:`), or write to the FIFO (no
   interruption; the run continues).
5. After `kill` or `poweroff` the machine is restarted and the check sequence below runs.

## Check sequence after every interruption (the oracle, FR-019, FR-020)

Run in this order, each as its own recorded check of case `S3.2:<point>:<mode>`:

1. **Invariants** (oracle, read only, no mover running):
   (a) every live file reads back through the share with the expected content hash;
   (b) every file whose pool copy is gone has an archive copy with the expected hash;
   (c) every whiteout that is gone has its archived file deleted;
   (d) no name of the expected-deleted set is visible through the share;
   (e) no file is visible with partial content (S3.8: a reader sees complete old or complete new).
2. **Retry** the mover (S3.3): the run must converge to the final state of an uninterrupted run, without
   duplicates and without manual cleanup. A second case kills the **retry** at a point chosen by a fixed
   rule (the next point after the one just exercised) and retries again.
3. **Device failure drill** (S3.4, FR-019 (e) and the parity-coverage proof): on a copy of the
   interrupted machine state, fail one pool device in one drill and the archive disk holding a moved file in
   another; apply the S2 recovery sequence; every live file must come back with the expected hash. This
   is how "pool copy removed implies parity-covered" (FR-019 (b)) is proven without trusting the mover.

## Permitted state per phase (what the oracle expects between the points)

| Phase | Pool copy of a copy item | Archive copy of a copy item | Whiteout of a deletion item | Archived file of a deletion item |
| --- | --- | --- | --- | --- |
| before `post:M1` | present | absent, partial under a temporary name, or complete | present | present |
| `post:M1` to `post:M4` | present | complete; hash-equal if unchanged, but may be stale while a newer pool copy remains | present | present |
| during M5 | present for items not yet unlinked, absent only for items already verified and parity-covered (M3, M4 completed) | complete | present | present |
| `post:M5` | absent for unchanged items | complete, parity-covered | present | present |
| during M6 | absent (unchanged items) | complete | present | present or deleted |
| `post:M6` to `post:M8` | absent | complete | present | deleted (parity covered only from `post:M7` on) |
| during M9 | absent | complete | present or removed (removed only after `post:M8`) | deleted |
| `post:M9` | absent | complete | removed | deleted |

A pool copy absent before `post:M4` has completed, or a whiteout absent before `post:M8` has completed,
is a violation of FR-019 (b) or (c).

## Failure behaviour (S3 scenarios 6 and 9, FR-021)

A failure of M1 (including out-of-space), M2 (checksum mismatch), M3 (sync failure) or M4 (verification
failure) stops the run, reports the failing step on standard error and in the exit status, and leaves
every pool copy and every whiteout in place. A later run starts again from the on-disk state.
