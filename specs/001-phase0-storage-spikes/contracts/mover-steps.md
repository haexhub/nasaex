# Contract: mover step list and hook points

**Feature**: `001-phase0-storage-spikes` | **Used by**: S3 (mover crash safety), S1 scenario 10, S2 recovery drill

This is the artifact the spec calls "the mover step list" (spec, Assumptions): the product mover will
later implement it, and a change to it after Phase 0 reopens S3. In Phase 0 it is implemented by a
throwaway reference mover (`spikes/phase0/guest/`, shell). The step list, the hook protocol and the
invariants below are the contract; the script is not.

The mover follows the pattern documented by mergerfs for a tiered cache (move files from the cache
**branch**, not from the cache pool, into the base pool, on a schedule) with the project's safety rule:
the source is kept until a snapshot-supported `snapraid sync` has been verified. The stock scripts of
mergerfs delete the source right after the copy (`rsync --remove-source-files`) and are therefore not
used; they appear only as the negative control of S3 scenario 5 (research sections 7.2 and F11).

Design rule under test: **the mover keeps no state that must survive a crash.** Every run derives its
plan from the on-disk state (pool branch, archive branches, SnapRAID content and snapshot state). S3
shows whether that is sufficient. If a journal turns out to be necessary, that is an S3 finding and a
correction of this list, not a silent addition.

## Terms

- **Pool branch**: the btrfs subvolume of the pool that is the first branch of the cache pool (holds new
  files).
- **Archive branches**: the data subvolume of each archive disk (each its own btrfs filesystem).
- **Base pool**: the mergerfs pool over the archive branches only. The mover writes into the archive
  through the base pool, never through the share.
- **Cache pool**: the mergerfs pool (pool branch first, archive branches after it, no-create) that is
  the share.
- **Item**: a file the run acts on. A run has `n` copy items (pool-only files, including at least one
  that replaces an older archived version at the same path). The S3 fixture uses `n = 3`. There are no
  deletion items: client deletions of archived files happen in place on the archive disk (S1 scenario 4)
  and the mover never applies deletions.
- **Source-version lock**: per-item coordination held from the M2 source hash through the M5 removal
  decision. Every writer that can replace a pool item must honour the same lock, or use an atomic
  handoff with equivalent semantics. If a stable version cannot be held through removal, the mover
  keeps the pool copy.
- **Snapshot-supported sync**: `snapraid sync` run through the snapshot tooling selected in the plan
  (open item O15): new read-only btrfs snapshots of the data disks are taken, the sync runs against
  them, and the snapshots are kept until the next sync completes.
- **View**: what the share shows. S1 scenario 10 records whether the view is consistent without
  intervention within a bounded time after a branch was changed directly (mergerfs caches lookups and
  attributes for 1 second by default) or which exact procedure makes it consistent. If a refresh step
  is needed it becomes a point of this list before the criteria are frozen.

## Steps

| Step | Name | Action | Durable effects (each is a mid-step point) | Postcondition at `post:M<k>` |
| --- | --- | --- | --- | --- |
| M1 | copy | For each copy item: copy the pool file through the base pool to a temporary name in the target archive directory, fsync, rename to the final name (for a replacement the rename atomically replaces the old archived version), fsync the directory | `tmp-created`, `tmp-partial` (half of the bytes written, not yet synced), `tmp-data-synced`, `renamed`, `parent-synced` (for the first item), `item-1-done` | Every copy item has a complete archive copy under its final name; pool copies are untouched |
| M2 | checksum | For each copy item: acquire its source-version lock, hash the pool file through the locked version, capture that version's identity (size, mtime, ctime, inode), then hash the archive copy (archive side read after dropping caches so it comes from the disk) and compare | `pool-hashed`, `archive-hashed` (no durable effects; read only) | All hashes equal and the source version remains held for M5; otherwise the run stops at M2, reports it, and changes nothing else |
| M3 | sync | Snapshot-supported `snapraid sync` for the archive | `sync-pos` (`snapraid -l` progress position reached, or a sync limited with `-B`), see the fault-points contract | Sync completed with exit status 0; content file saved; the snapshot set of this sync is marked complete |
| M4 | verification | Verify that every copy item is covered by completed parity: the item exists with the expected hash in the snapshot set of the completed sync, there are no pending changes for the item paths, and a check of those paths succeeds | `checked` (no durable effects) | Parity coverage verified for every copy item |
| M5 | pool copy removal | For each copy item, while still holding its source-version lock: re-read the pool file identity; if it differs from M2 (the file changed meanwhile), release the lock, keep the pool copy and leave the item for a later run; otherwise unlink the pool copy before releasing the lock | `item-validated`, `unlinked` (first item), `item-1-done` | Every unchanged item exists only in the archive; changed items still in the pool. A path-only check followed by unlink is not sufficient |

Spec crash boundaries (FR-018): after copy, after checksum, after sync, after verification, after pool
copy removal = `post:M1` .. `post:M5`. The start `pre:M1` is added; mid-step points are the effects in
the table.

## Point names

- Boundaries: `pre:M1`, `post:M1` .. `post:M5` (6 points).
- Mid-step: `mid:M<k>:<effect>` with the effects of the table, for the first item of a loop step
  (13 points: M1 6, M2 2, M3 1, M4 1, M5 3).
- Total as currently enumerated: **19 points**, each exercised by `kill` (process kill) and `poweroff`
  (QEMU process termination; host-power-loss semantics unverified): **38 cases**. SC-005 requires 100% of
  the enumerated points; the criteria file freezes the list, so changing this table changes the criteria
  version.
- Case id: `S3.2:<point>:<kill|poweroff>`, for example `S3.2:post:M5:poweroff`.

Compared with the first plan (nine steps, 34 points, 68 cases) the steps M6 to M9 (archive deletion,
second sync, second verification, whiteout cleanup) and the `view-refreshed` points are gone: there are
no whiteouts in the baseline (see the appendix for where they went).

Injected faults and concurrent changes (S3 scenarios 6 to 9) use named injection points in the same
namespace: `fault:M1:enospc`, `fault:M2:corrupt-archive-copy`, `fault:M3:parity-write-fail`,
`fault:M4:verify-fail`, `inject:modify-pool-after:M1`, `inject:modify-pool-after:M2`, and the variant
selector `control:stock-remove-source` for S3 scenario 5 (the stock pattern: unlink each pool copy right
after its copy, before M3). The control is a separate mover variant, not a point of the list. See
[fault-points.md](fault-points.md).

## Hook protocol (reference mover)

1. The mover calls `spike_hook <point>` at every point of the table. With no armed point the call
   returns immediately.
2. The host arms a point by creating the file `armed/<point>` in the guest hook directory (a tmpfs, so a
   power-off clears it; the host re-arms after boot where needed).
3. An armed hook writes `reached/<point>` and then blocks reading the FIFO `go/<point>`. It never calls
   `sync` or touches any disk.
4. The host waits for `reached/<point>`, then does one of: kill the mover process (`kill`), power the
   machine off (`poweroff`), fail a device, request a client write (`inject:`), or write to the FIFO
   (no interruption; the run continues). For `inject:modify-pool-after:M2`, the client write acquires
   the same source-version lock before opening or replacing the path. The host starts that write
   asynchronously, verifies that it is waiting for the lock, and releases `go/<point>`; the mover then
   completes M3 through M5 and releases the lock, after which the client write creates the newer pool
   version. The check must verify that the write waited through M5 and that the newer version remains
   available in the pool rather than being deleted or hidden by the stale archive copy.
5. After `kill` or `poweroff` the machine is restarted and the check sequence below runs.

## Check sequence after every interruption (the oracle, FR-019, FR-020)

Run in this order, each as its own recorded check of case `S3.2:<point>:<mode>`:

1. **Invariants** (oracle, read only, no mover running):
   (a) every live file reads back through the share with the expected content hash;
   (b) every file whose pool copy is gone has an archive copy with the expected hash;
   (c) no file that no client deleted is missing from the share;
   (d) no file is visible with partial content (S3.8: a reader sees complete old or complete new).
2. **Retry** the mover (S3.3): the run must converge to the final state of an uninterrupted run, without
   duplicates and without manual cleanup. A second case kills the **retry** at a point chosen by a fixed
   rule (the next point after the one just exercised) and retries again.
3. **Device failure drill** (S3.4, FR-019 (e) and the parity-coverage proof): on a copy of the
   interrupted machine state, fail one pool device in one drill and the archive disk holding a moved file in
   another; apply the S2 recovery sequence (with the snapshot workflow where S2 prescribes it); every live
   file must come back with the expected hash. This is how "pool copy removed implies parity-covered"
   (FR-019 (b)) is proven without trusting the mover.

Negative control (S3.5): the same invariants (a) and (b) and the drill are run against the
`control:stock-remove-source` variant. At the first point after its copy and before M3, a `poweroff` plus
device failure of the archive disk must lose the file, and the oracle must flag the violation of (b).
S3.5 passes only if it does.

## Permitted state per phase (what the oracle expects between the points)

| Phase | Pool copy of a copy item | Archive copy of a copy item |
| --- | --- | --- |
| before `post:M1` | present | absent, partial under a temporary name, or complete |
| `post:M1` to `post:M4` | present | complete; hash-equal if unchanged, but may be stale while a newer pool copy remains |
| during M5 | present for items not yet unlinked, absent only for items already verified and parity-covered (M3, M4 completed) | complete |
| `post:M5` | absent for unchanged items | complete, parity-covered |

A pool copy absent before `post:M4` has completed is a violation of FR-019 (b).

## Failure behaviour (S3 scenarios 6 and 9, FR-021)

A failure of M1 (including out-of-space), M2 (checksum mismatch), M3 (sync failure) or M4 (verification
failure) stops the run, reports the failing step on standard error and in the exit status, and leaves
every pool copy in place. A later run starts again from the on-disk state.

## Appendix: where the whiteout steps went

The first plan had four more steps for the overlayfs design (M6 archive deletion, M7 second sync, M8
second verification, M9 whiteout cleanup) and the invariants "no whiteout removed before its archive
deletion was synced" and "no deleted file reappears". They are not part of this contract. If the optional
experiment X1 is run, its criteria define whatever mover extension it needs; those points are counted
under X1 only and never under S3. The first plan's text is in the git history of this file (commit
`053dd91` and earlier).
