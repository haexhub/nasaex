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
correction of this list, not a silent addition. The trash handling of a client-deleted file (below) is the
place where this rule is under the most pressure: see "Known limits of the trash handling".

Status of the text: everything in this contract that depends on the behaviour of mergerfs, btrfs, SnapRAID or
the kernel (including the trash handling below) is **expected behaviour** that S1 or S3 must prove and stays
UNVERIFIED until a spike has shown it.

Concurrency rule: **the mover takes no lock that clients would have to honour, and no client write is
required to wait through the sync step M3.** A lock held by the mover cannot bind SMB
clients, so safety against a client write that races the mover rests on three things only: the selection
rule (idle files, no open handles), the identity comparison at M2 and M5 (a changed file keeps its pool
copy) and the named residual-window fault point of M5 that measures what that comparison cannot close
(S3 scenario 7). An earlier text of this contract let clients honour a per-item source-version lock; that
rule was dropped by the clarification of 2026-10-16 (task T081).

## Terms

- **Pool branch**: the btrfs subvolume of the pool that is the first branch of the cache pool (holds new
  files).
- **Archive branches**: the branch directory of each archive disk, a directory inside the data subvolume of
  that disk (each disk its own btrfs filesystem). The data subvolume holds two sibling directories: the branch
  directory, which is a branch of both pools, and the trash directory (next entry).
- **Trash**: the trash directory of an archive disk, a sibling of the branch directory inside the same data
  subvolume, so that it is outside every pool branch and is expected not to appear in the view of the cache
  pool or the base pool, and so that a rename from the branch directory into it stays inside one subvolume and
  one filesystem (expected to be an atomic rename; a rename across btrfs subvolumes is not expected to work,
  which is why the trash is not a subvolume of its own). The data subvolume is covered by the parity of the
  archive, so trash content is expected to stay recoverable. A trash entry has the path `<pool-relative path>`
  suffixed with the copy-time ctime in nanoseconds and the pool inode number of the item, so that two trashed
  versions of one path never collide, a retry computes the same name, and no trash entry is ever overwritten.
  The retention period of the trash is a parameter fixed in the S3 criteria; purging the trash is out of
  Phase 0. That the trash is hidden from both pools, that the rename into it is atomic and that its content
  survives a single device failure are expected behaviours (UNVERIFIED): S1 scenario 1 and S3 scenario 7 (e)
  must prove them.
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
  that is too young and one that is held open, and both must stay in the pool. A selected file whose pool file is
  gone before its copy-time identity could be read is dropped from the run: no archive copy exists, so nothing
  can reappear.
- **Item**: a file selected by the run. A run has `n` copy items (pool-only files, including exactly one
  that replaces an older archived version at the same path). The S3 fixture uses `n = 3`. There are no
  deletion items: client deletions of archived files happen in place on the archive disk (S1 scenario 4)
  and the mover never applies deletions. The trash move of a vanished item (below) is not the application of a
  client deletion: it hides a copy the mover itself made and deletes nothing.
- **Item order and the instrumented item**: the items of a loop step are processed in ascending order of
  their pool-relative path (byte order). The S3 fixture names its three items so that the **replacing item**
  (the one that replaces an older archived version) sorts first; the first run of a case refuses to start when
  the item with the lowest path is not the replacing one. **Item 1** is that first item. The refusal applies to
  the first run of a case only: a retry selects whatever the on-disk state still offers, and its item 1 is the
  item with the lowest path among the items the retry selects (the replacing item may already be done, and a
  vanished item is never selected again); the mid-step points of a retry fire for that item 1. In every loop step the
  mid-step points and the `item-1-done` points fire for item 1 only, and the injections of S3 scenario 7
  act on item 1. The replacement of S3 scenario 8 is therefore instrumented by the M1 mid-step points
  (`tmp-created` to `item-1-done`): the kill lands before, during and after the atomic rename that replaces
  the old archived version.
- **Copy-time identity**: ctime, size and inode of the item's pool file, read (without opening the file for
  writing) immediately before the first byte of that item is copied in M1. The mover keeps it in memory
  only, per run; it is never persisted, so the design rule above holds: after a crash the next run selects,
  copies and records again. M2 and M5 compare against it.
- **Copy-time record of the archive copy**: after the final fsync of M1 the mover also records, in memory
  only, the inode, size and ctime of the archive copy under its final name and the content hash of the bytes it
  copied. M2 and the trash move of M5 compare the archive copy against it; it is how "the archive copy still
  matches the identity and checksum recorded at copy time" is decided.
- **Changed item**: an item whose pool file exists but no longer has its copy-time identity at M2 or M5 (ctime,
  size or inode differ). A changed item is not a failure: the run keeps its pool copy, takes
  it out of the remaining steps of this run, reports it on standard error as `changed` and carries on with
  the other items. Its archive copy, if M1 already created one, stays and is stale; the next run, which
  selects the file again once it is old enough, supersedes it.
- **Vanished item**: an item whose pool file is missing at M2 or at M5 after its copy was made in M1 (a client
  deleted it, or it vanished for another reason such as a device error; the mover cannot tell the two apart).
  A vanished item is not a failure and is not a changed item. Its archive copy must neither be deleted (it may
  be the only copy of a file lost by a device error) nor stay visible (a client delete must not be undone by
  the stale archive copy): at M5 it is moved into the trash if it still matches its copy-time record, otherwise
  left where it is and reported as `vanished-archive-changed` (it may be live data: a client may have
  changed the archive copy in place after the pool file vanished). A vanished item stays in M3 and M4, so the
  content that goes into the trash is parity-covered first. If a pool file exists again at the path at M5
  (a client created a new file there), the item is a changed item, not a vanished one: the new pool file is
  kept and the stale archive copy is superseded by the next run.
- **Vanished item without archive copy**: a vanished item whose archive copy is gone as well when the mover
  looks for it at M2 or M5. A client delete through the share is expected to cause this under the default
  unlink policy of mergerfs (`epall`, which includes the `NC` archive branches and so also unlinks the archive
  copy that M1 made; read from the pinned 2.41.1 source, UNVERIFIED until S1 scenario 4 and the probe of the
  delete fault kind have measured it). It is a valid outcome, not a failure: the item is reported on standard
  error as `vanished-archive-gone` and leaves the run (nothing to hash, to cover or to move); it is not an M2
  hash mismatch, nothing is trashed, the mover deletes nothing, and no data is lost because the client
  deleted the file. The oracle tells it from a mover deletion by the host's record of the branches that held
  item 1 right after the delete (see the hook protocol).
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
| M1 | copy | For each copy item: record its copy-time identity, then copy the pool file through the base pool to a temporary name in the target archive directory together with the metadata listed under "Metadata preservation", fsync, rename to the final name (for a replacement the rename atomically replaces the old archived version), fsync the directory; then record the copy-time record of the archive copy | `tmp-created`, `tmp-partial` (half of the bytes written, not yet synced), `tmp-data-synced`, `renamed`, `parent-synced` (for item 1), `item-1-done` | Every copy item has a complete archive copy under its final name with the metadata preserved; pool copies are untouched; the copy-time identity and the copy-time record of the archive copy of every item are held in memory |
| M2 | checksum | For each copy item: hash the pool file, then read its identity again; if the pool file is gone the item is a vanished item (no pool hash; the archive copy is hashed and compared with the copy-time hash only; if the archive copy is gone too, the item is a vanished item without archive copy, reported and out of the run, not a mismatch); if the identity differs from the copy-time identity the item is a changed item and is skipped; otherwise hash the archive copy (archive side read after dropping caches so it comes from the disk) and compare the content hash and the metadata fields | `pool-hashed`, `archive-hashed` (no durable effects; read only) | For every unchanged item the hashes and the metadata fields are equal; changed items are reported and out of the run; vanished items are reported, stay in the run and have an archive copy equal to its copy-time hash; a content or metadata mismatch of an unchanged item, or a hash mismatch of a vanished item's archive copy, stops the run at M2, reports it, and changes nothing else |
| M3 | sync | Snapshot-supported `snapraid sync` for the archive, if at least one unchanged or vanished item remains. The mover holds no lock on the pool; client I/O is neither blocked nor delayed while it runs | `sync-pos` (`snapraid -l` progress position reached, or a sync limited with `-B`), see the fault-points contract | Sync completed with exit status 0; content file saved; the snapshot set of this sync is marked complete; no client write waited for it (S3 scenario 7 (c)) |
| M4 | verification | Verify that every unchanged or vanished copy item is covered by completed parity: the item exists with the expected hash in the snapshot set of the completed sync, there are no pending changes for the item paths, and a check of those paths succeeds | `checked` (no durable effects) | Parity coverage verified for every unchanged and every vanished copy item |
| M5 | pool copy removal (compare and delete) and trash move | For each unchanged or vanished copy item: read the identity of the pool file by its path on the pool branch. **Pool file present** (an unchanged candidate): compare ctime, size and inode with the copy-time identity (the final check); if anything differs, keep the pool copy, report the item as changed and leave it for a later run; otherwise unlink the pool copy. The window between the final check and the unlink is the **residual window**: a write that lands inside it is not seen by the check. **Pool file missing** (a vanished item): if the archive copy is missing as well, report `vanished-archive-gone` and do nothing else for the item (a vanished item without archive copy); otherwise compare the inode, size and ctime of the archive copy with its copy-time record and hash it again against the copy-time hash; if both match, move the archive copy into the trash on the same archive disk (rename to the trash path, creating missing trash directories, never overwriting; then fsync the trash directory and the former parent directory of the archive copy); if either differs, leave the archive copy where it is and report `vanished-archive-changed`. Nothing is ever unlinked on the vanished path | `item-validated` (the final check passed, the unlink has not happened; the residual-window point; present-file path only), `unlinked` (item 1; present-file path only), `trash-renamed` (the archive copy was renamed into the trash, parent directories not yet synced; vanished path only), `trash-parent-synced` (both directories synced; vanished path only), `item-1-done` | Every unchanged item exists only in the archive; every vanished item exists only as one trash copy, except a vanished item without archive copy, which exists nowhere because the client deleted it; changed items still in the pool, their archive copies stale until the next run. A path-only check followed by unlink is not sufficient; the check is the identity comparison |

Spec crash boundaries (FR-018): after copy, after checksum, after sync, after verification, after pool
copy removal = `post:M1` .. `post:M5`. The start `pre:M1` is added; mid-step points are the effects in
the table, for item 1 of a loop step (see "Item order and the instrumented item"). The points of the present-file
path and those of the vanished path of M5 are different effects of one step: an uninterrupted run reaches the
first group for an unchanged item 1 and the second group for a vanished item 1.

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
| Mid-step M5 (item 1), present-file path | `mid:M5:item-validated`, `mid:M5:unlinked`, `mid:M5:item-1-done` | 3 |
| Mid-step M5 (item 1), vanished path | `mid:M5:trash-renamed`, `mid:M5:trash-parent-synced` | 2 |
| **Total crash points** | | **21** |

- Kill modes: `kill` (process kill) and `poweroff` (QEMU process termination; host-power-loss semantics
  unverified). Every point is exercised in both modes: 21 x 2 = **42 cases**. SC-005 requires 100% of the
  enumerated points.
- The boundary points `pre:M1` and `post:M1` to `post:M5` fire in every run, also when a step has no item to
  process (M3 then runs no sync), so that every run reaches `post:M5`. The **execution order** of the points is
  this fixed list: `pre:M1`, the M1 mid-step points in table order, `post:M1`, the M2 mid-step points,
  `post:M2`, `mid:M3:sync-pos`, `post:M3`, `mid:M4:checked`, `post:M4`, the present-file points of M5
  (`mid:M5:item-validated`, `mid:M5:unlinked`, `mid:M5:item-1-done`), the vanished-path points of M5
  (`mid:M5:trash-renamed`, `mid:M5:trash-parent-synced`), `post:M5`. A run reaches only one of the two M5
  groups for item 1.
- Case id: `S3.2:<point>:<kill|poweroff>`, for example `S3.2:post:M5:poweroff`. The residual-window point
  `mid:M5:item-validated` is also a crash point (a kill or power-off right after the final check).
- The two points `mid:M5:trash-renamed` and `mid:M5:trash-parent-synced` fire only when item 1 is a vanished
  item. A case of either point therefore arms `inject:delete-pool-after:M3` first (a client delete of the pool
  file of item 1 after the sync has covered it), then kills or powers off at the point. Their case ids have the
  same form, for example `S3.2:mid:M5:trash-renamed:poweroff`. Whether the harness can reach them under the
  measured unlink policy is open (see "Known limits of the trash handling", point 4).
- The amendment of 2026-10-16 (no client lock, selection, compare-and-delete at M5) adds no durable effect
  and no step: selection and the copy-time identity are read-only and in memory, the identity comparisons of
  M2 and M5 read only, so the count of crash points and cases is unchanged by it. Selection is covered by
  `pre:M1`. What it changes is where item 1 comes from (the replacing item, which also instruments S3
  scenario 8) and the injection points below, which are not crash points and are not counted in SC-005.
- The trash amendment of 2026-10-16/17 (a client delete of a selected pool file) adds one durable effect pair to
  M5, the rename of the archive copy into the trash and the sync of both directories. Both are durable effects
  and, by the rule that every durable effect is a point, two points: `mid:M5:trash-renamed` and
  `mid:M5:trash-parent-synced` (the move is one rename plus the directory syncs, so there is no further
  intermediate effect; creating missing trash directories is not a point, an empty directory being harmless,
  as with the parent directories of M1). The vanished-item handling of M2 to M4 adds reads only. The count goes
  from 19 to 21 crash points and from 38 to 42 cases; the enumeration of the table above gives 6 boundaries +
  6 mid-step points of M1 + 2 of M2 + 1 of M3 + 1 of M4 + 5 of M5 = 21.
- Compared with the first plan (nine steps, 34 points, 68 cases) the steps M6 to M9 (archive deletion,
  second sync, second verification, whiteout cleanup) and the `view-refreshed` points are gone: there are
  no whiteouts in the baseline (see the appendix for where they went).
- A first item that is a plain new file instead of the replacing item would leave the replacement
  uninstrumented; instrumenting both would add the six M1 mid-step points for a second item (27 points,
  54 cases). That is not part of this contract.

Injected faults and concurrent changes (S3 scenarios 6 to 9) use named injection points in the same
namespace; they are enumerated here, are not crash points and are not counted in SC-005:

| Point | Scenario | Meaning |
| --- | --- | --- |
| `fault:M1:enospc` | S3.6, S3.9 | Archive out of space during the copy of item 1; it is also the copy fault of S3.6 (a fault in the copy step), so S3.6 needs no further point |
| `fault:M2:corrupt-archive-copy` | S3.6 | Archive copy of item 1 corrupted before M2 hashes it |
| `fault:M3:parity-write-fail` | S3.6 | Failed parity write during the sync |
| `fault:M4:verify-fail` | S3.6 | Verification fails for item 1 |
| `inject:modify-pool-after:M1` | S3.7 (b) | Client write to the pool file of item 1 after M1 (found at M2) |
| `inject:modify-pool-after:M2` | S3.7 (b) | Client write after M2 (found by the final check of M5) |
| `inject:modify-pool-during:M3` | S3.7 (c) | Client write while the sync of M3 is in progress; must complete without delay |
| `inject:modify-pool-at:M5:residual-window` | S3.7 (d) | Client write between the final check of M5 and the unlink of item 1; the outcome (data lost or not lost) is recorded |
| `inject:delete-pool-after:M1` | S3.7 (e) | Client delete of the pool file of item 1 after M1 (found at M2 as a vanished item; the trash move happens at M5) |
| `inject:delete-pool-after:M3` | S3.7 (e) | Client delete of the pool file of item 1 after M3 (found at M5); also the precondition of the two trash crash points |
| `inject:modify-archive-after-delete:M4` | S3.7 (e) | Client write through the share to the path of item 1 after M4, in a case that also arms `inject:delete-pool-after:M3`. Where the delete left the archive copy in place, the write changes that archive copy in place (S1 scenario 3), so M5 finds it no longer matching its copy-time record and leaves it as `vanished-archive-changed`; where the delete removed the archive copy, the write creates a new pool file and M5 treats item 1 as a changed item. M4 is the only step: a client change of the archive copy before M4 has completed is expected to be found by M2 (hash mismatch) or M4 (verification) and to stop the run (failure behaviour) |

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
   `mid:M3:sync-pos`, `inject:modify-pool-at:M5:residual-window` on `mid:M5:item-validated`,
   `inject:delete-pool-after:M1` on `post:M1`, `inject:delete-pool-after:M3` on `post:M3`,
   `inject:modify-archive-after-delete:M4` on `post:M4` (an append of a known byte pattern through the share at
   the path of item 1; the host records on which branch the written file is found afterwards). For the two delete
   points the host deletes (unlinks) the pool path of item 1 through the share, again without waiting for the
   mover, then lists the path of item 1 directly on the pool branch and on every archive branch and records
   which branches still hold it (`branches_after_delete`), reads the effective unlink policy of the cache pool
   (`func.unlink`), and reads the share path of item 1 once before it releases the hook. The read
   (`visible_before_trash`) is recorded, not judged: it is true only where the delete left the archive copy in
   place (the archive copy then shows through until the trash move); under the default unlink policy `epall`
   the delete is expected to remove the archive copy too, and the read is expected to be false (UNVERIFIED,
   measured). The mover then completes the run, and the checks of S3 scenario 7 run: after `inject:modify-pool-after:M1` and
   `inject:modify-pool-after:M2` the pool copy of item 1 was kept as a changed item, the newer pool version
   is available through the share and is not deleted or hidden by the stale archive copy, and a later run
   supersedes the archive copy; after `inject:modify-pool-during:M3` the write completed within the bound;
   after `inject:modify-pool-at:M5:residual-window` the oracle searches for the content of the completed
   write in the pool and in the archive, and the case records `data_lost: true` or `data_lost: false`. After a delete injection the checks of S3 scenario
   7 (e) run: the deleted file is not visible through the share (the path does not exist), no archive copy of
   it exists in any archive branch, every other file is unchanged, and one of two outcomes holds: (trash) where
   `branches_after_delete` names an archive branch, exactly one trash copy exists on the archive disk that held
   the archive copy, its content hash equals the hash the fixture expects for the copied bytes, and a later run
   neither moves, restores nor re-copies the trash entry; (archive gone) where `branches_after_delete` names no
   branch, no trash copy exists and the item was reported `vanished-archive-gone`. The case records which
   outcome it reached together with `func.unlink`. After `inject:modify-archive-after-delete:M4` the content of
   the client's write is visible through the share at the path of item 1, nothing is trashed and nothing is
   deleted: where the archive copy survived the delete it stays where it is with the client's content and is
   reported `vanished-archive-changed`; where the delete had removed it, the written file is a pool file and
   item 1 is reported `changed`.
5. After `kill` or `poweroff` the machine is restarted and the check sequence below runs.

## Check sequence after every interruption (the oracle, FR-019, FR-020)

Run in this order, each as its own recorded check of case `S3.2:<point>:<mode>`:

1. **Invariants** (oracle, read only, no mover running):
   (a) every live file reads back through the share with the expected content hash;
   (b) every file whose pool copy is gone has an archive copy with the expected hash (for a vanished item whose trash move has completed, exactly one trash copy with that hash instead; for a vanished item without archive copy, none, provided the host recorded that the client's delete removed the archive copy);
   (c) no file that no client deleted is missing from the share;
   (d) no file is visible with partial content (S3.8: a reader sees complete old or complete new);
   (f) where the case injected a client write, the content of every client write that completed exists in
   the pool or in the archive (a case that ends with `data_lost: true` fails S3 scenario 7 (d));
   (g) where the case injected a client delete of the pool file of item 1: once the run that handled the delete
   has completed, the file is not visible through the share, the mover deleted no archive copy, and either
   exactly one trash copy exists and it is byte-identical (content hash) to the copy the mover made, or, where
   `branches_after_delete` names no archive branch, neither a trash copy nor an archive copy exists (a vanished
   item without archive copy, removed by the client's delete; the outcome is recorded). Until the trash
   move has completed the archive copy may show through the share (the measured window); immediately after a
   `kill` or `poweroff` at `mid:M5:trash-renamed` it may be under either its final name or its trash name (after
   a `poweroff` the rename may not be durable), and the case records which. This leniency ends with the retry:
   S3.3 requires the deleted file to be hidden and its trash copy to exist after the retry, see "Known limits of
   the trash handling".
2. **Retry** the mover (S3.3): the run must converge to the final state of an uninterrupted run, without
   duplicates and without manual cleanup. A second case kills the **retry** at a point chosen by a fixed
   rule, per case, and retries again: the host arms every point that follows the one just exercised in the
   execution order and kills the retry at the first of them that the retry reaches; if the retry reaches none
   of them, it is killed at `post:M5` (which every run reaches). This covers the cases in which the next point
   cannot fire in the retry: in the four cases of the two trash points the retry never selects the deleted
   item, so no point of the vanished path fires and the retry is killed at the first later point it reaches,
   at the latest `post:M5`; after the case `post:M5` itself the retry is killed at `post:M5`.
3. **Device failure drill** (S3.4, FR-019 (e) and the parity-coverage proof): on a copy of the
   interrupted machine state, fail one pool device in one drill and the archive disk holding a moved file in
   another; apply the S2 recovery sequence (with the snapshot workflow where S2 prescribes it); every live
   file must come back with the expected hash, and where the case holds a trash copy, the trash content must
   come back byte-identical as well (FR-019 (e): no live file and no trash content lost). The path under which
   a recovered trash entry lands (the trash path or the archive path it was moved from) is recorded per case;
   recovery is expected to restore the state as of the last completed sync, so a trash entry moved after that
   sync may come back under its old archive path, which makes the deleted file reappear through the share.
   **Outcome of S3.4 for such a case**: S3.4 judges FR-019 (e) only, by content. The case passes S3.4 when every
   live file and the trash content come back byte-identical, under either path; a trash entry that comes back
   under its archive path is recorded as a finding of the record (the landing path in the case evidence and a
   note on the case) for the maintainers. It does not fail S3.4, and it is not judged against FR-019 (c), which
   applies after an interruption and after a retry, not after a recovery. Content that does not come back fails
   S3.4. This is how "pool copy removed implies parity-covered" (FR-019 (b)) is proven without trusting the
   mover.

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

For a vanished item (the host deleted the pool file by injection, so its absence is not a mover effect):

| Phase | Pool copy | Archive copy (branch directory) | Trash copy |
| --- | --- | --- | --- |
| from the delete to `mid:M5:trash-renamed` | absent (deleted by the client) | complete under its final name, hash-equal to its copy-time hash; shows through the share until the trash move (measured) | absent |
| between `mid:M5:trash-renamed` and `mid:M5:trash-parent-synced` | absent | absent or complete under its final name (after a `poweroff` the rename may not be durable) | complete and hash-equal if the rename is durable, otherwise absent; never both partial |
| from `mid:M5:trash-parent-synced` and at `post:M5` | absent | absent | exactly one, complete, byte-identical to the copy-time hash; the file is not visible through the share |
| archive copy changed in place by `inject:modify-archive-after-delete:M4` (the delete left it in place), from the write to `post:M5` | absent | complete under its final name with the client's content (no longer equal to the copy-time hash); visible through the share; M5 leaves it and reports `vanished-archive-changed` | absent |
| vanished item without archive copy: from the delete on (the host recorded `branches_after_delete` without an archive branch) | absent (deleted by the client) | absent (removed by the client's delete through the share) | absent; the file is not visible through the share; the mover reports `vanished-archive-gone` |

At no phase may the content of a vanished item exist nowhere (neither in the archive branch nor in the trash),
except for a vanished item without archive copy, whose content the client deleted (as recorded by the host
right after the delete), and no archive copy may be unlinked by the mover. A pool copy absent before `post:M4` has completed is a violation of
FR-019 (b), unless the host's own delete injection removed it.

## Failure behaviour (S3 scenarios 6 and 9, FR-021)

A failure of M1 (including out-of-space), M2 (content or metadata mismatch of an unchanged item, or a hash mismatch of a vanished item's archive copy), M3 (sync
failure) or M4 (verification failure) stops the run, reports the failing step on standard error and in the
exit status, and leaves every pool copy in place. A changed item is not a failure (see "Changed item"). A
later run starts again from the on-disk state. A failure of the trash move (the rename or a directory sync
fails, for example because the rename is refused across filesystems) is reported as `trash-failed` in the exit
status, leaves the archive copy where it is (so the deleted file stays visible and the content is not lost)
and stops the run.

## Known limits of the trash handling

These follow from the decision to trash at M5 and from the design rule that the mover keeps no state across a
crash. They are expected to show up in S3.7 (e) and S3.2 and are recorded as findings; none of them is hidden
by the oracle.

1. **Visible window.** Where the client delete left the archive copy in place (see point 4), the archive copy
   of the file (made in M1) shows through the cache pool from the delete until the trash move, because the pool
   file that hid it is gone. The window is
   measured (`visible_before_trash`) and not bounded in Phase 0.
2. **Crash before the trash move.** After a `kill` or `poweroff` before the trash move is durable, the next
   run does not select the file (its pool file is gone), so it cannot tell the stale archive copy from an
   ordinary archived file and cannot trash it. The deleted file then stays visible. S3 records this for the two
   trash points. Immediately after a `poweroff` at `mid:M5:trash-renamed` the rename may not be durable (a
   `kill` does not lose it), and then the retry cannot restore the hidden state. The checks judge this
   without interpretation: a retry that leaves the deleted file visible fails S3.3 for that case, so a failing
   S3.3 case is a possible, expected-to-be-measured outcome, not a harness defect. Other combinations of a
   delete with a crash at an earlier point are not enumerated; they have the same gap. If the case fails, the
   correction is durable state (a journal or tombstone) and a reviewed amendment of this contract before S3 is
   re-run, as the design rule says.
3. **Delete while no mover runs.** A client delete of a pool file while no mover run is in progress has an
   archive copy to bring back only if an earlier, interrupted run had already copied the file; that case is the
   gap of point 2. A pool file that no run ever copied has no archive copy and nothing can reappear.
4. **Reachability of the trash path.** The trash path needs a vanished item whose archive copy is still
   present. Under the default unlink policy of mergerfs (`epall`, pinned 2.41.1 source
   `src/func.hpp:77-83,177`, `src/policy_epall.cpp:70-91`) a client delete through the share is expected to
   remove the archive copy together with the pool file; mergerfs unlinks the selected branches one after
   another (`src/fuse_unlink.cpp`), so the only state in which the pool file is gone and the archive copy is
   still present is the window between those unlinks, which no named point can hit. The trash outcome of the
   two delete injections and the four cases of the trash points (`mid:M5:trash-renamed`,
   `mid:M5:trash-parent-synced`, each in both kill modes) may therefore be reachable only through that window
   or under another unlink policy. Which one the harness can reach is measured (S1 scenario 4, the probe of the
   delete fault kind, the S3.7 (e) check), not decided: `func.unlink=ff` is a candidate setting and UNVERIFIED.
   A case that cannot be executed counts as failed (spec verdict model), so if the measurement shows the trash
   path unreachable, a reviewed amendment before the S3 criteria are frozen either adopts a measured unlink
   setting as a required setting (FR-013, with its effect on S1 scenarios 4 and 5 checked) or changes this
   point list, and with it the counts.

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
