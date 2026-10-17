# Contract: fault kinds and injection points

**Feature**: `001-phase0-storage-spikes` | **Used by**: S1 (out of space), S2, S3, S4, and the optional experiment X1 (out of space) | **Spec**: FR-010

Every fault is injected from the host side by the Python test script, at a named point, never after a
timed delay. A scenario that needs a fault names the **kind** and the **point**; the record stores both
(`fault_kind`, `fault_point`). Items marked UNVERIFIED are feasibility probes of the first
implementation tasks (research section 6).

## Fault kinds

| Kind | Trigger (host side) | What the guest sees | Guarantees and limits |
| --- | --- | --- | --- |
| `kill` | `kill -9` of a named guest process through the driver shell, while the process is blocked at a hook | The process vanishes; the kernel and all other processes continue | Deterministic: the process cannot advance. Lock files and temporary files stay as they were |
| `poweroff` (**UNVERIFIED**) | `machine.crash()` of the test driver (monitor `quit`) | QEMU process termination; guest RAM and guest page cache are lost; the machine can be started again on the same disks | This is not a host-power-loss guarantee. Writes may remain visible through the host page cache; loss or reordering of un-flushed host- or device-cache writes is not emulated. Keep this fault kind unverified until the pinned QEMU and `emptyDiskImages` configuration passes an unflushed-write probe, and record the limitation in every result |
| `device-unplug` | Monitor `device_del <disk id>` of one disk, selected by its stable serial | The disk disappears like a pulled drive | UNVERIFIED for virtio-blk disks on this machine type |
| `device-ioerror` | Monitor `drive_del <drive>` of one disk | I/O to the disk fails, the device node remains | UNVERIFIED on the pinned QEMU |
| `device-failed-at-boot` | Before the next start, the backing image of the failed disk is replaced by an empty image (also the "replacement disk" step) | The disk is blank on the next boot | Needed because a machine's start command is fixed and would re-attach the old image |
| `write-error` | Parity or archive disk failed at a hook (`device-ioerror`), or a `blkdebug` backing definition for a single-block fault | Failed writes to that disk | `blkdebug` UNVERIFIED |
| `corrupt` | Guest-side overwrite of bytes of the archive copy at a hook | Archive copy differs from the pool copy | Content only; no device fault |
| `enospc` | Filler file leaves a defined number of free bytes on the target filesystem | `ENOSPC` on the next large write | Deterministic; no special device. For S1 scenario 9 the share's `minfreespace` is set below the free bytes left, so that the write reaches the pool branch and fails there (research F10) |
| `kill-sync-at-position` | Wait until the `snapraid -l` log shows `run:pos:<block>` with block >= K, then `kill` | A SnapRAID sync dies after at least K blocks were processed | Flush frequency of the log lines UNVERIFIED; the S2.4 prediction is evaluated against what the kill left (research 3.4), separately for the plain and the snapshot variant; with the snapshot tooling the kill also leaves an incomplete snapshot set whose handling is part of the result (research 7.3) |
| `sync-limited` | `snapraid sync -S <start> -B <count>` | A sync that processes only a block range and ends normally | Deterministic stand-in for "stopped after K blocks" |
| `concurrent-modify` | Client-side append or rewrite of the pool file of item 1 through the share at a hook, with no mover-side lock (a lock of the mover cannot bind SMB clients, so none exists); the harness measures the time the write takes to complete | The write is never held back by the mover and creates a newer pool version (new ctime and size, or a new inode) | Used by S3.7 at the four `inject:modify-pool-*` points of the mover contract, and by S3.7 (e) at `inject:modify-archive-after-delete:M4`, where the write goes through the share to the path of item 1 after its pool file was deleted, so it lands in place on the archive copy if the delete left one, otherwise as a new pool file (the branch it lands on is recorded). At `inject:modify-pool-during:M3` the host pauses the sync process with a stop signal at the `sync-pos` position (named, not timed) so that the write provably overlaps M3, then resumes it; the pause method is UNVERIFIED until probed. A write that has not completed within the bound (initial target 5 s) while the sync is paused counts as having waited through M3 |
| `concurrent-delete` (**UNVERIFIED**) | Client-side unlink of the pool file of item 1 through the share at a hook, with no mover-side lock; the harness waits for the unlink to complete, records which branches still hold the path (`branches_after_delete`, listed directly on the branches) and the effective unlink policy of the cache pool (`func.unlink`), and then reads the share path of item 1 once | The pool file disappears. Under the default unlink policy `epall` of mergerfs the archive copy made in M1 is expected to disappear with it (pinned 2.41.1 source; `NC` branches are not excluded), so nothing shows through; only where the delete leaves the archive copy in place does it show through the cache pool until the mover has moved it into the trash (`visible_before_trash`, recorded, not judged) | Used by S3.7 (e) at the two `inject:delete-pool-after:*` points of the mover contract, and as the precondition of the two trash crash points. Which branches the delete removes is measured first (S1 scenario 4 and the probe of this kind); the trash path is reachable only where the archive copy survives the delete (mover contract, known limit 4). That the trash is hidden from the pool view is an expected behaviour that S1 and S3 must prove |

## Point namespaces

| Namespace | Defined in | Examples |
| --- | --- | --- |
| Mover boundaries and mid-step points | [mover-steps.md](mover-steps.md) | `post:M5`, `mid:M1:tmp-partial` |
| Mover faults, injections and the negative-control variant | [mover-steps.md](mover-steps.md) | `fault:M3:parity-write-fail`, `inject:modify-pool-during:M3`, `inject:modify-pool-at:M5:residual-window`, `inject:delete-pool-after:M3`, `control:stock-remove-source` |
| SnapRAID sync (S2.4) | criteria file S2 | `sync:pos:<K>` for K in the frozen list |
| Disk lifecycle (S2, S4) | criteria files S2, S4 | `disk:<serial>:fail`, `disk:<serial>:replace`, `boot:after-failure` |
| Fixture states (S1) | criteria file S1 | `pool:free:<bytes>` for the out-of-space new write |

## M5 residual-window fault point

`inject:modify-pool-at:M5:residual-window` is armed on the hook `mid:M5:item-validated`, which the mover
reaches for item 1 after its final identity check (ctime, size, inode against the copy-time values) passed
and before it unlinks the pool copy. The same hook is also a crash point of the mover contract; as an
injection point it does the following, in this order, never after a timed delay:

1. The host waits for `reached/mid:M5:item-validated`. The mover is blocked between the final check and the
   unlink.
2. The host performs a client write through the share to the pool path of item 1 (an append of a known
   byte pattern, so that the final content is predictable) and waits for it to complete. No lock exists, so
   it completes; its completion and the acknowledged bytes are recorded.
3. The host releases `go/mid:M5:item-validated`. The mover unlinks the pool copy it validated, completes
   the run and exits.
4. The oracle then runs one further mover run and searches the pool and the archive for the final content
   of item 1 (the original bytes plus the appended pattern, by hash). The case records `data_lost: true` if
   that content exists nowhere, otherwise `data_lost: false`, together with the observed placement.

The outcome is recorded either way. `data_lost: true` fails scenario S3.7 (d), S3 FAILS, and the record names
the rename-to-quarantine-and-recheck protocol as the fallback (spec FR-031). A `poweroff` or `kill` at the
same hook is a separate crash case (`S3.2:mid:M5:item-validated:<mode>`) and is not an injection.

## Delete injection points

`inject:delete-pool-after:M1` is armed on the hook `post:M1` and `inject:delete-pool-after:M3` on `post:M3`.
Both do the following, in this order, never after a timed delay:

1. The host waits for `reached/<hook>`; the mover is blocked at the boundary.
2. The host unlinks the pool path of item 1 through the share and waits for the unlink to complete. No lock
   exists, so it completes.
3. The host lists the path of item 1 directly on the pool branch and on every archive branch and records
   `branches_after_delete`, reads the effective `func.unlink` policy of the cache pool, then reads the share
   path of item 1 once and records `visible_before_trash` (true only where the delete left the archive copy in
   place; expected false under the default policy `epall`, UNVERIFIED). These are measurements, not checks.
4. The host releases `go/<hook>`. The mover completes the run: for a delete after M1 it finds the vanished item
   at M2, for a delete after M3 at M5; it moves the archive copy into the trash at M5 if the archive copy is
   still present and still matches its copy-time record, and reports `vanished-archive-gone` if the delete
   removed the archive copy too.
5. The oracle checks FR-019 (c), (e) and (g): the deleted file is not visible through the share, the mover
   deleted no archive copy, and either exactly one trash copy exists and is byte-identical to the copy the mover
   made (where `branches_after_delete` named an archive branch) or no trash copy and no archive copy exist
   (where it named none); the device-failure drill loses no live file and no trash content. The case records
   which outcome it reached.

The trash handling does not change the residual window above: that window concerns a pool file that is
present at the final check, and the trash applies only when the pool file is missing.

## Rules

1. **Named, not timed.** A fault point is reached because a guest process says so (hook marker), because
   a log line says so, or because a scenario step finished. `sleep` is not a trigger.
2. **One fault, one record entry.** Each case in a record names its fault kind and point. SC-005 coverage
   counts are computed from the enumerated crash points and kill modes; named fault and injection points are
   recorded separately and are not included in those counts.
3. **Hooks do not sync.** The hook client never calls `sync`, `fsync` or drops caches, so a `poweroff` at a
   point sees exactly the effects of the steps before it.
4. **Guest-local state is not trusted.** The oracle never reads a hook marker, a mover log or SnapRAID's
   own status as evidence of correctness; markers are used only to know where the process is blocked.
5. **Device identity by serial.** Disks are addressed by `/dev/disk/by-id/virtio-<serial>`, so removing
   one never renumbers the others.
6. **Probe first.** Before a spike relies on a kind marked UNVERIFIED, a probe test shows that the kind
   behaves as listed (including btrfs and the guest recovering as expected); if not, the kind is replaced
   by the fallback `dm-error` in the guest and the substitution is recorded in the result record's
   `residual_risk`.
