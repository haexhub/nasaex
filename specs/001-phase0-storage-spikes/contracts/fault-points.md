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
| `concurrent-delete` (probed 2026-10-17, tasks T037c and T037f) | Client-side unlink of the pool file of item 1 through the share at a hook, with no mover-side lock; the harness waits for the unlink to complete, records which branches still hold item 1 (`branches_after_delete`, listed directly on the branches; for a delete during M1 a complete temporary copy counts, see the mover contract), the effective unlink policy of the cache pool (`func.unlink`, expected `epall`), and then reads the share path of item 1 once (for a delete during M1 again after the mover's rename) | The pool file disappears. The cache pool keeps the mergerfs default unlink policy `epall` (no `func.unlink` option; spec Clarifications 2026-10-17), which does not exclude `NC` branches: a delete after the rename of M1 removes the archive copy made in M1 as well (measured at `post:M1`), so nothing shows through; a delete during M1 before the rename removes only the pool file (measured at `mid:M1:tmp-data-synced` and `mid:M1:tmp-partial`), and the archive copy that the rename then creates shows through the cache pool until the mover has moved it into the trash (`visible_before_trash`, recorded, not judged) | Used by S3.7 (e) at the four `inject:delete-pool-*` points of the mover contract; `inject:delete-pool-at:M1:tmp-data-synced` is the precondition of the two trash crash points. The expected outcome of each point is fixed below and in the mover contract (trash, copy failed, archive gone); S1 scenario 4 records the unlink policy again (a measurement, not a pass condition). That the trash is hidden from the pool view is an expected behaviour that S1 and S3 must prove |

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

Four delete injection points, named like the existing injection points of the mover contract (`-after:M<k>`
on the boundary `post:M<k>`, `-at:M<k>:<name>` on a mid-step hook of M<k>, as `inject:modify-pool-at:M5:residual-window`
on `mid:M5:item-validated`):

| Point | Hook | Expected outcome under the unlink policy `epall` of the cache pool |
| --- | --- | --- |
| `inject:delete-pool-at:M1:tmp-data-synced` | `mid:M1:tmp-data-synced` | **trash**: the delete removes only the pool file; the mover's rename makes the synced temporary copy the archive copy; M2 finds a vanished item with archive copy, M3 and M4 cover it, M5 moves it into the trash (measured to the end of M1 by probe T037f; M2 to M5 UNVERIFIED until S3 runs) |
| `inject:delete-pool-at:M1:tmp-partial` | `mid:M1:tmp-partial` | **copy failed**: the copy fails on the deleted source, M1 fails and the run stops; no archive copy, no trash copy; the partial temporary file stays, listed through the share, until the next run removes it (measured by probe T037f; the removal is a proposed rule of the mover contract) |
| `inject:delete-pool-after:M1` | `post:M1` | **archive gone**: the delete removes the pool file and the archive copy; M2 reports `vanished-archive-gone` (measured by probe T037c) |
| `inject:delete-pool-after:M3` | `post:M3` | **archive gone**, found at M5 (the same policy; not measured at this point, UNVERIFIED) |

Each does the following, in this order, never after a timed delay:

1. The host waits for `reached/<hook>`; the mover is blocked at the hook.
2. The host unlinks the pool path of item 1 through the share and waits for the unlink to complete. No lock
   exists, so it completes.
3. The host lists the directory of item 1 directly on the pool branch and on every archive branch (temporary
   names included, kept in the evidence) and records `branches_after_delete` (a branch holding the path of item
   1, or for the two points during M1 a temporary file of item 1 whose content hash equals the expected hash),
   reads the effective `func.unlink` policy of the cache pool, then reads the share path of item 1 once and
   records `visible_before_trash`. For the two points during M1 it also arms `post:M1` as a release-only point
   and reads the share path once more there, after the mover's rename (for `tmp-partial`, which never reaches
   `post:M1`, after the run). These are measurements, not checks.
4. The host releases `go/<hook>`. The mover completes the run (or, for `tmp-partial`, stops in M1); for that
   point the case then runs the mover once more, uninterrupted.
5. The oracle checks FR-019 (c), (e) and (g): the deleted file is not visible through the share, the mover
   deleted no archive copy, and either exactly one trash copy exists and is byte-identical to the copy the mover
   made (where `branches_after_delete` named an archive branch) or no trash copy and no archive copy exist
   (where it named none); the device-failure drill loses no live file and no trash content. The case checks
   that the outcome it reached is the expected outcome of its point (a mismatch fails the case) and records it.

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
