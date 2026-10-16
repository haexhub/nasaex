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

Concurrency rule: **the mover takes no lock that clients would have to honour, and no client write waits
through any step, in particular not through the sync step M3.** A lock held by the mover cannot bind SMB
clients, so safety against a client write that races the mover rests on three things only: the selection
rule (idle files, no open handles), the identity comparison at M2 and M5 (a changed file keeps its pool
copy) and the named residual-window fault point of M5 that measures what that comparison cannot close
(S3 scenario 7). An earlier text of this contract let clients honour a per-item source-version lock; that
rule was dropped by the clarification of 2026-10-16 (task T081).

## Terms

- **Pool branch**: the btrfs subvolume of the pool that is the first branch of the cache pool (holds new
  files).
- **Archive branches**: the data subvolume of each archive disk (each its own btrfs filesystem).
- **Base pool**: the mergerfs pool over the archive branches only. The mover writes into the archive
  through the base pool, never through the share.
- **Cache pool**: the mergerfs pool (pool branch first, archive branches after it, no-create) that is
  the share.
- **Selection**: the first part of a run, before M1, with no durable effects (a kill during selection is
  the point `pre:M1`). The mover considers the files of the pool branch and selects a file only if (1) it
  was not modified for the **minimum age**, a parameter fixed in the S3 criteria: its age, measured with the
  clock of the machine the mover runs on, is the time since the later of its mtime and ctime, and a file with
  a timestamp in the future has no valid age and is not selected; and (2) no process and no SMB client holds
  it open when it is examined (open handles are determined on the server). A file that fails either test stays
  in the pool untouched and is considered again by the next run. The S3 fixture contains at least one file
  that is too young and one that is held open, and both must stay in the pool.
- **Item**: a file selected by the run. A run has `n` copy items (pool-only files, including exactly one
  that replaces an older archived version at the same path). The S3 fixture uses `n = 3`. There are no
  deletion items: client deletions of archived files happen in place on the archive disk (S1 scenario 4)
  and the mover never applies deletions.
- **Item order and the instrumented item**: the items of a loop step are processed in ascending order of
  their pool-relative path (byte order). The S3 fixture names its three items so that the **replacing item**
  (the one that replaces an older archived version) sorts first; the run refuses to start a case when the
  item with the lowest path is not the replacing one. **Item 1** is that first item. In every loop step the
  mid-step points and the `item-1-done` points fire for item 1 only, and the injections of S3 scenario 7
  act on item 1. The replacement of S3 scenario 8 is therefore instrumented by the M1 mid-step points
  (`tmp-created` to `item-1-done`): the kill lands before, during and after the atomic rename that replaces
  the old archived version.
- **Copy-time identity**: ctime, size and inode of the item's pool file, read (without opening the file for
  writing) immediately before the first byte of that item is copied in M1. The mover keeps it in memory
  only, per run; it is never persisted, so the design rule above holds: after a crash the next run selects,
  copies and records again. M2 and M5 compare against it.
- **Changed item**: an item whose pool file no longer has its copy-time identity at M2 or M5 (ctime, size or
  inode differ, or the file is gone). A changed item is not a failure: the run keeps its pool copy, takes
  it out of the remaining steps of this run, reports it on standard error as `changed` and carries on with
  the other items. Its archive copy, if M1 already created one, stays and is stale; the next run, which
  selects the file again once it is old enough, supersedes it.
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
| M1 | copy | For each copy item: record its copy-time identity, then copy the pool file through the base pool to a temporary name in the target archive directory together with the metadata listed under "Metadata preservation", fsync, rename to the final name (for a replacement the rename atomically replaces the old archived version), fsync the directory | `tmp-created`, `tmp-partial` (half of the bytes written, not yet synced), `tmp-data-synced`, `renamed`, `parent-synced` (for item 1), `item-1-done` | Every copy item has a complete archive copy under its final name with the metadata preserved; pool copies are untouched; the copy-time identity of every item is held in memory |
| M2 | checksum | For each copy item: hash the pool file, then read its identity again; if it differs from the copy-time identity the item is a changed item and is skipped; otherwise hash the archive copy (archive side read after dropping caches so it comes from the disk) and compare the content hash and the metadata fields | `pool-hashed`, `archive-hashed` (no durable effects; read only) | For every unchanged item the hashes and the metadata fields are equal; changed items are reported and out of the run; a content or metadata mismatch of an unchanged item stops the run at M2, reports it, and changes nothing else |
| M3 | sync | Snapshot-supported `snapraid sync` for the archive, if at least one unchanged item remains. The mover holds no lock on the pool; client I/O is neither blocked nor delayed while it runs | `sync-pos` (`snapraid -l` progress position reached, or a sync limited with `-B`), see the fault-points contract | Sync completed with exit status 0; content file saved; the snapshot set of this sync is marked complete; no client write waited for it (S3 scenario 7 (c)) |
| M4 | verification | Verify that every unchanged copy item is covered by completed parity: the item exists with the expected hash in the snapshot set of the completed sync, there are no pending changes for the item paths, and a check of those paths succeeds | `checked` (no durable effects) | Parity coverage verified for every unchanged copy item |
| M5 | pool copy removal (compare and delete) | For each unchanged copy item: read the identity of the pool file by its path on the pool branch and compare ctime, size and inode with the copy-time identity (the final check); if anything differs or the file is gone, keep the pool copy, report the item as changed and leave it for a later run; otherwise unlink the pool copy. The window between the final check and the unlink is the **residual window**: a write that lands inside it is not seen by the check | `item-validated` (the final check passed, the unlink has not happened; the residual-window point), `unlinked` (item 1), `item-1-done` | Every unchanged item exists only in the archive; changed items still in the pool, their archive copies stale until the next run. A path-only check followed by unlink is not sufficient; the check is the identity comparison |

Spec crash boundaries (FR-018): after copy, after checksum, after sync, after verification, after pool
copy removal = `post:M1` .. `post:M5`. The start `pre:M1` is added; mid-step points are the effects in
the table, for item 1 of a loop step (see "Item order and the instrumented item").

## Metadata preservation (M1 and M2)

The archive copy of an item must carry the same metadata as the pool file. M1 copies it, M2 compares it,
and S3 scenario 1 and the oracle check it. The checked fields are exactly these:

| Field | What is compared |
| --- | --- |
| mtime | Modification time with nanosecond precision, equal on pool file and archive copy (S1 scenario 7 and the Clock skew edge case: timestamps survive the move unchanged) |
| mode | Permission bits and file type |
| xattrs | The full set of extended attribute names and values (including the attributes SMB clients store, S1 scenario 7) |
| hard links | Items that share an inode in the pool share one inode in the archive, and the link count of each archive copy equals that of its pool file |
| sparse files | Logical size equal and the hole map (the data and hole extents) equal; a sparse pool file does not become fully allocated in the archive |

Content is compared by hash, as before. Ownership and atime are not part of the contract. A mismatch of any
field of an unchanged item is an M2 failure.

## Point names

All points are enumerated here; the criteria file freezes this list, so changing this table changes the
criteria version.

| Group | Points | Count |
| --- | --- | --- |
| Boundaries | `pre:M1`, `post:M1`, `post:M2`, `post:M3`, `post:M4`, `post:M5` | 6 |
| Mid-step M1 (item 1) | `mid:M1:tmp-created`, `mid:M1:tmp-partial`, `mid:M1:tmp-data-synced`, `mid:M1:renamed`, `mid:M1:parent-synced`, `mid:M1:item-1-done` | 6 |
| Mid-step M2 (item 1) | `mid:M2:pool-hashed`, `mid:M2:archive-hashed` | 2 |
| Mid-step M3 | `mid:M3:sync-pos` | 1 |
| Mid-step M4 | `mid:M4:checked` | 1 |
| Mid-step M5 (item 1) | `mid:M5:item-validated`, `mid:M5:unlinked`, `mid:M5:item-1-done` | 3 |
| **Total crash points** | | **19** |

- Kill modes: `kill` (process kill) and `poweroff` (QEMU process termination; host-power-loss semantics
  unverified). Every point is exercised in both modes: 19 x 2 = **38 cases**. SC-005 requires 100% of the
  enumerated points.
- Case id: `S3.2:<point>:<kill|poweroff>`, for example `S3.2:post:M5:poweroff`. The residual-window point
  `mid:M5:item-validated` is also a crash point (a kill or power-off right after the final check).
- The amendment of 2026-10-16 (no client lock, selection, compare-and-delete at M5) adds no durable effect
  and no step: selection and the copy-time identity are read-only and in memory, the identity comparisons of
  M2 and M5 read only, so the count of crash points and cases is unchanged by it. Selection is covered by
  `pre:M1`. What it changes is where item 1 comes from (the replacing item, which also instruments S3
  scenario 8) and the injection points below, which are not crash points and are not counted in SC-005.
- Compared with the first plan (nine steps, 34 points, 68 cases) the steps M6 to M9 (archive deletion,
  second sync, second verification, whiteout cleanup) and the `view-refreshed` points are gone: there are
  no whiteouts in the baseline (see the appendix for where they went).
- A first item that is a plain new file instead of the replacing item would leave the replacement
  uninstrumented; instrumenting both would add the six M1 mid-step points for a second item (25 points,
  50 cases). That is not part of this contract.

Injected faults and concurrent changes (S3 scenarios 6 to 9) use named injection points in the same
namespace; they are enumerated here, are not crash points and are not counted in SC-005:

| Point | Scenario | Meaning |
| --- | --- | --- |
| `fault:M1:enospc` | S3.9 | Archive out of space during the copy of item 1 |
| `fault:M2:corrupt-archive-copy` | S3.6 | Archive copy of item 1 corrupted before M2 hashes it |
| `fault:M3:parity-write-fail` | S3.6 | Failed parity write during the sync |
| `fault:M4:verify-fail` | S3.6 | Verification fails for item 1 |
| `inject:modify-pool-after:M1` | S3.7 (b) | Client write to the pool file of item 1 after M1 (found at M2) |
| `inject:modify-pool-after:M2` | S3.7 (b) | Client write after M2 (found by the final check of M5) |
| `inject:modify-pool-during:M3` | S3.7 (c) | Client write while the sync of M3 is in progress; must complete without delay |
| `inject:modify-pool-at:M5:residual-window` | S3.7 (d) | Client write between the final check of M5 and the unlink of item 1; the outcome (data lost or not lost) is recorded |

and the variant selector `control:stock-remove-source` for S3 scenario 5 (the stock pattern: unlink each pool
copy right after its copy, before M3). The control is a separate mover variant, not a point of the list.
See [fault-points.md](fault-points.md).

## Hook protocol (reference mover)

1. The mover calls `spike_hook <point>` at every point of the table. With no armed point the call
   returns immediately.
2. The host arms a point by creating the file `armed/<point>` in the guest hook directory (a tmpfs, so a
   power-off clears it; the host re-arms after boot where needed).
3. An armed hook writes `reached/<point>` and then blocks reading the FIFO `go/<point>`. It never calls
   `sync` or touches any disk.
4. The host waits for `reached/<point>`, then does one of: kill the mover process (`kill`), power the
   machine off (`poweroff`), fail a device, request a client write (`inject:`), or write to the FIFO
   (no interruption; the run continues). For an `inject:` point the host starts the client write through
   the share at the pool path of item 1 and does **not** wait for the mover: no lock exists, so the write
   is never held back by the mover. The host measures how long the write takes to complete, then releases
   `go/<point>` (for `inject:modify-pool-during:M3` the host pauses the sync process with a stop signal at
   `mid:M3:sync-pos` while the write runs, so that the write provably overlaps M3, and resumes it
   afterwards; a write that has not completed within the bound while the sync is paused counts as having
   waited through M3). The injection points are armed on these hook points: `inject:modify-pool-after:M1` on
   `post:M1`, `inject:modify-pool-after:M2` on `post:M2`, `inject:modify-pool-during:M3` on
   `mid:M3:sync-pos`, `inject:modify-pool-at:M5:residual-window` on `mid:M5:item-validated`. The mover
   then completes the run, and the checks of S3 scenario 7 run: after `inject:modify-pool-after:M1` and
   `inject:modify-pool-after:M2` the pool copy of item 1 was kept as a changed item, the newer pool version
   is available through the share and is not deleted or hidden by the stale archive copy, and a later run
   supersedes the archive copy; after `inject:modify-pool-during:M3` the write completed within the bound;
   after `inject:modify-pool-at:M5:residual-window` the oracle searches for the content of the completed
   write in the pool and in the archive, and the case records `data_lost: true` or `data_lost: false`.
5. After `kill` or `poweroff` the machine is restarted and the check sequence below runs.

## Check sequence after every interruption (the oracle, FR-019, FR-020)

Run in this order, each as its own recorded check of case `S3.2:<point>:<mode>`:

1. **Invariants** (oracle, read only, no mover running):
   (a) every live file reads back through the share with the expected content hash;
   (b) every file whose pool copy is gone has an archive copy with the expected hash;
   (c) no file that no client deleted is missing from the share;
   (d) no file is visible with partial content (S3.8: a reader sees complete old or complete new);
   (f) where the case injected a client write, the content of every client write that completed exists in
   the pool or in the archive (a case that ends with `data_lost: true` fails S3 scenario 7 (d)).
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
| during M5 | present for items not yet unlinked and for changed items, absent only for unchanged items already verified and parity-covered (M3, M4 completed) | complete |
| `post:M5` | absent for unchanged items; present for changed items | complete and parity-covered for unchanged items; stale (older than the pool copy) for changed items until the next run |

A pool copy absent before `post:M4` has completed is a violation of FR-019 (b).

## Failure behaviour (S3 scenarios 6 and 9, FR-021)

A failure of M1 (including out-of-space), M2 (content or metadata mismatch of an unchanged item), M3 (sync
failure) or M4 (verification failure) stops the run, reports the failing step on standard error and in the
exit status, and leaves every pool copy in place. A changed item is not a failure (see "Changed item"). A
later run starts again from the on-disk state.

## Appendix: where the whiteout steps went

The first plan had four more steps for the overlayfs design (M6 archive deletion, M7 second sync, M8
second verification, M9 whiteout cleanup) and the invariants "no whiteout removed before its archive
deletion was synced" and "no deleted file reappears". They are not part of this contract. If the optional
experiment X1 is run, its criteria define whatever mover extension it needs; those points are counted
under X1 only and never under S3. The first plan's text is in the git history of this file (commit
`053dd91` and earlier).

## Appendix: where the source-version lock went

An earlier text of this contract defined a per-item source-version lock, held from the M2 source hash
through the M5 removal decision, that every writer of the pool had to honour, so that a client write waited
through M3 to M5. A lock of the mover cannot bind SMB clients, so the rule was dropped by the clarification
of 2026-10-16 (task T081). It is replaced by the selection rule, the copy-time identity and the identity
comparison at M2 and M5, with the residual window measured by
`inject:modify-pool-at:M5:residual-window`. If that window can lose a completed client write, S3 fails and
the fallback is the rename-to-quarantine-and-recheck protocol (spec FR-031), which would add its own points
to this list by a reviewed amendment.
