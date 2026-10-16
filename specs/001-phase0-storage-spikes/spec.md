# Feature Specification: Phase 0 Storage Model Spikes

**Feature Branch**: `001-phase0-storage-spikes`

**Created**: 2026-10-15

**Status**: Approved (amended 2026-10-16)

**Input**: User description: "Phase 0 of nasaex: prove the two-tier storage model and the declarative rebuild model in reproducible NixOS VM tests with virtual disks, before any product code is written. Each spike (S1-S5) has acceptance criteria that are written down BEFORE the spike is run, a clear pass/fail outcome, and a recorded result. The phase gates ADR-0002 and ADR-0005: they move from proposed to accepted only when their spikes pass, otherwise the documented fallback applies. An optional experiment (X1) tests the former overlayfs variant; it never gates anything."

**Sources of truth**: [Design](../../docs/plans/2026-10-15-nasaex-design.md) (section "Phase 0 spikes"), [ADR-0002](../../docs/adr/0002-two-tier-storage.md), [ADR-0005](../../docs/adr/0005-declarative-config-applied-by-rebuild.md). Where this spec and the design disagree, the design wins and this spec is corrected.

## Overview

nasaex stakes its storage safety on two untested ideas: a two-tier layout (mirrored pool above, parity-protected archive below, presented as one share through the tiered-cache pattern that mergerfs documents) and a rebuild-based configuration model. Phase 0 exists to find out, cheaply and before any product code exists, whether those ideas hold. This feature specifies **what must be proven and how pass or fail is judged**. It does not specify how the tests are built.

The "users" of this feature are the project maintainers, who must decide whether to accept ADR-0002 and ADR-0005. The product's end users (NAS owners and their SMB clients) appear only as the people whose data the spikes protect.

Spike identifiers used throughout: **S1** tiered-cache share, **S2** parity recovery, **S3** mover crash safety, **S4** degraded modes, **S5** declarative rebuild, and the optional, non-gating experiment **X1** overlayfs variant (User Story 7).

## Clarifications

### Session 2026-10-16

Decisions taken by the maintainer on 2026-10-16 (no open questions were asked; each answer is applied in the sections named). Finding ids refer to `checklists/analysis-2026-10-16.md`.

- Q: Do the non-baseline variants of S1 scenarios 2, 6 and 8 (create policy `lus`, `passthrough.io=rw`, `cache.files=auto-full`) gate the scenario? → A: No. Only the baseline variant gates; each variant's outcome is recorded in the record's evidence and case list with `gating: false` (A1; Verdict model, S1.2, S1.6, S1.8, data-model).
- Q: How does the reviewer's judgement of S2 scenario 7 enter the verdict? → A: The automated step checks of S2.7 stay; the judgement is a separate reviewed sub-record (kind review, committed through a PR) that sets S2.7 to `pass` or `fail`. Until it exists the S2 verdict is `pending` and no ADR gate may be applied (A2; Verdict model, FR-002, S2.7, data-model).
- Q: Which client operations on already archived files may change an archive disk? → A: All client-driven in-place operations (modify, delete, rename, attribute or xattr change); new data never lands on an archive disk (I14; FR-014).
- Q: How do files get onto the surviving archive disks in the S2 history? → A: Only through the mover path, not through the share; modification and deletion stay in place through the share (I16; S2.2, S2.9, S1.2).
- Q: What is a "run" for the rule "criteria first", and what may a shakedown change? → A: A run is a run that can produce a record. Shakedown runs never count as the first run and never write records; before the freeze they may change the structure of a model (for example the block-allocation model or state-table cells) but never the predicted outcomes once derived (I12, I13; Verdict model).
- Q: Which change class decides the S5 duration threshold? → A: The thresholds apply per change class (no-op re-apply, adding a share, changing a storage parameter) and the worst class median decides (A3; S5.1, FR-030, SC-008).
- Q: What replaces the unquantified terms "clear/defined error", "bounded time", "nothing hangs" and "same view"? → A: Observable definitions in the Verdict model: named errno plus the client-visible SMB status, a numeric bound fixed by the criteria before the freeze (initial targets 30 s and 5 s), and an identical manifest before and after (A4; S1.9, S1.10, S1.11, S4, S5).
- Q: What does the state table say about "direct archive write" in the healthy and pool-degraded states? → A: "Not tested: no client route exists"; the refusal tests of the archive-degraded and both-degraded states stay (A5; S4 table, FR-023).
- Q: Which scenarios must be automated? → A: Every acceptance scenario except the review judgement of S2.7, whose step checks are automated (I8; FR-002, SC-004).
- Q: What happens if the stock-mover negative control is not flagged as FAIL? → A: The S3 run is invalid (a harness defect), no record is written and the spike has no verdict; a flagged control is a precondition for a record, not a scenario outcome (I10; Verdict model, S3.5, FR-032).
- Q: How are FR-008, FR-009, FR-010 and the last clause of FR-034 evidenced? → A: Each names an explicit "verified by" reference (task ids or record fields) (G2; the four requirements).

## Verdict model *(applies to every spike)*

- **Criteria first.** The acceptance criteria of a spike are written down and committed before the spike is run for the first time (a run as defined in the next bullet). The criteria of a spike are frozen at that point. Changing them later is allowed only as a recorded amendment that states the reason, and any verdict obtained under the old wording is void.
- **Run.** A **run** is a run that can produce a record (a record-mode run). A shakedown run (used to build and debug the harness and the models behind the criteria) never counts as the first run and never writes a record. Before the freeze a shakedown may change the structure of a model the criteria use (for example the block-allocation model of S2 or the cells of the S4 state table), but never the predicted outcomes themselves once they have been derived: a later mismatch is a FAIL, not a re-prediction.
- **Binary verdict.** A spike ends as **PASS** or **FAIL**. There is no partial pass: a spike passes only if every one of its scenarios passes. A scenario that could not be executed, was skipped or is flaky counts as failed. The only deviations are the three rules below on variants, the reviewed judgement of S2.7 and a harness defect.
- **Variants are informational.** Where a scenario is run in variants (S1 scenarios 2, 6 and 8: create policy `lus`, `passthrough.io=rw`, `cache.files=auto-full`), only the baseline variant gates the scenario. The outcome of every other variant is recorded in the record's evidence and case list with `gating: false` and never changes the scenario outcome. A setting that turns out to be required for the baseline to pass is recorded as a required setting and becomes part of the baseline (FR-013).
- **Reviewed judgement (S2.7).** The automated step checks of S2 scenario 7 run like any other check. Whether the written recovery procedure is acceptable is judged by a reviewer and carried by a separate reviewed sub-record (a record of kind review, committed through a PR) that sets scenario S2.7 to `pass` or `fail`. A failed step check makes S2.7 `fail` without review. Until the sub-record exists the S2 verdict is `pending`, and no ADR gate may be applied.
- **Valid run.** An S3 run is valid only if the stock-mover negative control is flagged as FAIL by the harness (S3 scenario 5, FR-032). If it is not flagged, the run is invalid because of a harness defect, no record is written and S3 has no verdict. A flagged control is a precondition for a record, not a scenario outcome.
- **Observable terms.** A scenario outcome is decided without interpretation, so these terms are defined once and used as defined:
  - A **clear** or **defined error** is a named errno together with the corresponding SMB status as seen by the client: `ENOSPC` for out of space, `EROFS` for a refused write to a read-only layer, `EIO` where a device error is injected. The criteria name the SMB status of each case.
  - A **bounded time** (and "nothing hangs") is a numeric bound that the criteria MUST fix before the freeze. The initial defaults are 30 s for a client operation and 5 s for a client write during the mover's sync step; both are initial targets, not derived from measurements.
  - The **same view** means an identical manifest of path, type, size, mtime, mode and content hash before and after a mover run.
- **Recorded result.** Every run produces a result record kept in version control next to the criteria: spike, criteria version, environment revision, date, per-scenario outcome, evidence reference and verdict. A failed S1 to S5 record also carries the applicable fallback decision; X1 has no fallback.
- **Re-runs.** After a documented design correction a spike may be re-run. A failed run is never deleted; the new run is a new record that references the earlier one.
- **ADR gate.** ADR-0002 moves from proposed to accepted only when S1, S2, S3 and S4 all PASS. ADR-0005 moves from proposed to accepted only when S5 PASSES. If a gating spike FAILS, the ADR stays proposed until the documented fallback has been adopted and the ADR text updated to say so. While a gating spike's verdict is `pending` or `none`, no ADR gate may be applied. X1 never gates an ADR: it may be run or skipped, and neither its verdict nor its absence changes any ADR status.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - S1: The tiered-cache share behaves like a normal filesystem (Priority: P1)

A maintainer needs evidence that the share (a cache pool in which the fast pool comes first and the archive disks follow, next to a base pool of the archive disks alone) can be used by real SMB clients exactly like an ordinary folder, that new data lands on the pool and never on an archive disk, that archived files stay readable and changeable through the same share, and that a file stays visible when the mover moves it. Everything else in the storage model depends on this.

**Why this priority**: If the tiered-cache share fails, the two-tier model has no way to present one share, and ADR-0002 falls back to direct archive access. It is the cheapest spike to run and the one that can end the design earliest.

**Independent Test**: In a VM test with several archive disks of different sizes (each its own btrfs filesystem) and a two-device pool, set up the base pool and the cache pool, share the cache pool, drive it from a separate SMB client VM, and compare the client-visible results and the on-disk branch contents with the expectations below.

**Acceptance Scenarios**:

1. **Given** the share is the cache pool (pool branch first, archive branches after it), **When** a client lists, reads and checksums files that exist only in the archive, **Then** names, sizes, timestamps, permissions and content are identical to a reference copy of the archive data.
2. **Given** the documented create policy (baseline `ff`; `lus` is also tried as an informational variant) with the archive branches set to no-create, **When** a client creates new files and directories, including inside directories that exist only on archive disks, **Then** all new data exists on the pool branch and on no archive branch (verified by a tree hash of every archive branch before and after), and the share shows archive and pool content as one tree. The policy and branch modes found to be required are recorded, together with the observed placement of a new file when the no-create mode is absent, as evidence for why it is required. Files reach the archive disks only through the mover path (S2, S3), never through the share.
3. **Given** a file that exists only in the archive, **When** a client modifies it, **Then** the client sees the new content, the change is made in place on the same archive disk (no copy appears on the pool branch), and every other file on the archive disks and the pool is byte-identical to before. This is the in-place change whose protection S2 measures.
4. **Given** a file that exists only in the archive, **When** a client deletes it, **Then** it disappears from the share, it is removed in place from its archive disk, and no marker or copy of it exists on the pool branch.
5. **Given** a deleted archive file, **When** a client creates a new file with the same name, **Then** the new content is shown and the old archived content never reappears. The same holds for a deleted-then-recreated directory: no entries from the old archived directory appear in the new one.
6. **Given** files and directories of each origin (archive only, pool only, directories present on both), **When** a client renames or moves them within the share, **Then** the rename succeeds, the complete tree is present under the new name, and nothing is left under the old name. A rename that fails (for example with a cross-device error) or leaves partial results is a FAIL of the baseline variant unless a documented setting makes it succeed for the SMB client, in which case that setting is recorded as required and becomes part of the baseline. The variant with `passthrough.io=rw` and `cache.files=auto-full` is informational.
7. **Given** extended attributes and SMB-relevant file attributes set by a client on files of each origin, **When** the file is renamed, remounted, read from another client or moved by the mover, **Then** the attributes are preserved. Attributes already present on archive files are visible through the share.
8. **Given** a file larger than 4 GiB in the archive and another written new through the share, **When** clients read, copy and checksum them and modify one byte of the archived one, **Then** all checksums match the expectation end to end, the new file landed on the pool, and the modification happened in place on its archive disk. The variant with `passthrough.io=rw` and `cache.files=auto-full` is informational.
9. **Given** the pool has too little free space for a new write, **When** a client creates a file, or appends to a file that lives on the pool, **Then** the client receives `ENOSPC` (with the corresponding SMB status as seen by the client), nothing is written to any archive disk, and no truncated file is visible. The settings needed to guarantee this (such as no-create archive branches and the cache pool's behaviour on out-of-space) are recorded as required.
10. **Given** a file visible through the share, **When** it is moved the way the mover does it (copy from the pool branch into the archive through the base pool, then remove the pool copy), **Then** the share shows the same name, content and attributes after the move (the same view: identical manifest before and after), the file is never missing or partial through the share at any step, and a reader holding the file open keeps reading complete data. The outcome is recorded: either the view is consistent without intervention within the bounded time fixed in the criteria (initial target 30 s), or an exact refresh procedure is recorded and verified. Undefined or stale views without a working procedure (a view whose manifest differs from the expected one after the bound) are a FAIL.
11. **Given** a share populated with archive files and pool files, **When** the VM is rebooted, **Then** both pools and the share come up without manual steps and the same view is presented (identical manifest before and after the reboot).

---

### User Story 2 - S2: Parity recovery works as assumed, and the dangerous sequence is blocked (Priority: P1)

A maintainer needs to know exactly what a disk failure does to recoverability in each situation (before a parity sync, during a sync, and after a sync that should not have run), with and without the btrfs snapshot workflow, and needs a documented, tested recovery sequence plus proof that read-only gating prevents the destructive case.

**Why this priority**: ADR-0002 rests on the claims that a sync after a disk failure destroys recoverability, that the archive can therefore be protected by locking it, and that the snapshot workflow keeps files recoverable although archived files are changed in place. If a claim or the recovery sequence is wrong, the data-loss promise of the product is wrong.

**Independent Test**: In a VM test with btrfs data disks and parity disks, populate, sync, change data (files are added to the surviving archive disks only through the mover path, not through the share; they are modified and deleted in place through the share), fail a data disk at different moments, replace it, run the recovery sequence, and compare every file against reference checksums. The scenarios that concern the snapshot workflow run the same history a second time with it.

**Acceptance Scenarios**:

1. **Given** a fully synced archive and no later changes, **When** one data disk fails and is replaced, **Then** following the documented recovery sequence restores 100% of that disk's files bit-exact, and the system is fully protected again after the resynchronisation step.
2. **Given** a synced archive (no snapshot workflow) in which files were added (only through the mover path, never through the share), modified and deleted (in place, through the share) on the surviving disks after the last sync, **When** one data disk fails, **Then** the files of the failed disk that share a parity block position with a changed or deleted block of a surviving disk are identified as affected, the unaffected files are recovered bit-exact, and the set of unrecoverable files matches the per-block prediction written in the criteria.
3. **Given** files written to the failed disk after the last sync, **When** that disk fails, **Then** those files are reported as unrecoverable (documents the unprotected window and confirms why new data goes to the pool, not the archive).
4. **Given** a sync is running, **When** a data disk fails during the run (and separately when the sync process is killed during the run), in both the plain and the snapshot variant, **Then** the resulting recoverability is measured and recorded, and the recovery sequence is shown to recover everything the criteria predict. If recovery is impossible for some files, those files are listed and the sequence's guidance for this case is recorded.
5. **Given** a data disk has failed and been replaced, **When** a sync is run before the recovery step, **Then** the test states how the hazard is reached (a replacement that is empty needs the force-empty override, because the tool refuses by default; a replacement that still holds some of the old files is not refused), demonstrates the loss of recoverability for the failed disk's files on that path, and records that the default invocation on an empty replacement is refused with the disk and parity unchanged. This confirms the hazard the design relies on.
6. **Given** read-only gating is active after a disk failure, **When** a sync, scrub or any mover action is attempted by any route (scheduled, manual, automatic), **Then** the attempt is refused and the archive content and parity are unchanged (verified by checksum), and a subsequent recovery restores 100% of the synced data bit-exact.
7. **Given** the documented safe sequence (detect failure, lock, replace disk, recover, verify, resynchronise, unlock), **When** it is executed verbatim by the test, **Then** every step has an automated check, the final state passes a full verification, and the sequence is saved as a written procedure in the result record. Whether the written procedure is acceptable is judged by a reviewer and recorded in a separate reviewed sub-record that sets this scenario to `pass` or `fail`; until it exists the S2 verdict is `pending` and no ADR gate may be applied.
8. **Given** two data disks fail with a single parity level configured, **When** recovery is attempted, **Then** the limit is recorded: which files are lost and that the system reports the condition instead of claiming success.
9. **Given** the snapshot-supported sync and fix workflow and the history of scenario 2 (files added through the mover path, and modified and deleted in place through the share, on the surviving disks after the last sync), **When** one data disk fails and recovery runs against the snapshots taken for the last completed sync, **Then** every file of the failed disk as of that sync is recovered bit-exact, files written or changed on the failed disk after that sync are listed as unrecoverable, and the difference to the result of scenario 2 is recorded as the measured value of the workflow.

**Recoverability predictions** (frozen before the first S2 run). All predictions are computed per parity block position, because SnapRAID's parity blocks are shared across disks at the same position and its manual states that changed or deleted files reduce recoverability even if they are not on the failed disk:

| Scenario | Expected recoverability |
| --- | --- |
| S2.1: failure after a complete sync with no later changes | Every file present on the failed disk at the last completed sync is recoverable bit-for-bit. |
| S2.2: changes on surviving disks after the last sync (no snapshots) | A file of the failed disk is recoverable if and only if, at every block position it occupies, no block of a surviving disk was changed or deleted since the last completed sync and the file itself is unchanged. Files first written or last changed on the failed disk after that sync are not recoverable. Changes on surviving disks are reported separately and are not counted as recovered failed-disk data. |
| S2.3: files written to the failed disk after the last sync | Those files are not recoverable; all files covered by the last completed sync remain subject to the S2.1 prediction (adding files elsewhere does not reduce it). |
| S2.4: device failure or process kill during sync | Files whose parity blocks (at every position they occupy) were completed and verified before the interruption, and whose positions were not otherwise changed, are recoverable; files in blocks not completed and verified are expected to be listed as unrecoverable. The result record maps this prediction to each file, separately for the plain and the snapshot variant. |
| S2.5: sync before recovery of a replaced disk | Files that were present only on the failed disk before the premature sync are expected to be unrecoverable on the path that reaches the sync (force-empty override, or a partially populated replacement). With the default invocation on an empty replacement the sync is refused and nothing changes. |
| S2.6: read-only gating after failure | All files covered by the last completed sync are recoverable; the refused actions leave the prediction and the parity content unchanged. |
| S2.7: documented safe sequence | All files covered by the last completed sync are recoverable after the sequence and final verification (automated step checks). The scenario outcome is set by the reviewed sub-record; until then it is `pending`. |
| S2.8: two data disks with single parity | Recovery is not claimed for files whose content cannot be reconstructed; the result lists those files and reports the parity limit. |
| S2.9: snapshot workflow, changes on surviving disks | Every file of the failed disk as of the last completed sync is recoverable bit-for-bit, whatever was added, modified or deleted on the surviving disks afterwards; files written or changed on the failed disk after that sync are not. This expectation rests on the documentation of the snapshot tools only and has not been observed (research section 7). |

---

### User Story 3 - S3: The mover never loses a pool copy, whatever interrupts it (Priority: P1)

A maintainer needs proof that the mover (the component that migrates cold data from the pool into the archive) can be killed at any point and that retrying it never loses data and never removes anything before its replacement is protected, and that the stock mover pattern, which deletes its source right after copying, is detected as unsafe.

**Why this priority**: The mover is the only component that moves data out of the redundant pool into the parity-protected archive. ADR-0002 names it the critical component. A single flaw here silently loses user data.

**Independent Test**: In a VM test, run the documented mover sequence for a set of pool files with a deterministic kill injected at each named step boundary (process kill and hard power-off), then check the invariants below after each interruption and after each retry.

**Acceptance Scenarios**:

1. **Given** a set of pool-only files to move, **When** the mover runs uninterrupted, **Then** all files end up in the archive with matching checksums, the pool copies are gone only after a completed and verified sync, and the share view is identical before and after.
2. **Given** the documented step boundaries (after copy, after checksum, after sync, after verification, after pool copy removal, plus a kill in the middle of each step), **When** the mover is killed at each of them by process kill and again by hard power-off, **Then** after every kill and restart each live file reads through the share with correct content (never missing, never partial), and no pool copy has been removed that is not already checksum-verified and parity-covered in the archive.
3. **Given** each interruption point, **When** the mover is retried (and when the retry is itself killed and retried again), **Then** the run converges to the same final state as an uninterrupted run, without duplicates, without data loss and without manual cleanup.
4. **Given** each interruption point, **When** a single device is then failed (a pool device, or the archive disk holding the file) and the recovery procedure of S2 is applied, **Then** every live file is recovered intact. This proves "every moved file is protected by the mirror or by parity at all times", not just "readable".
5. **Given** a mover variant that follows the stock pattern (copy, then delete the source immediately, as `rsync --remove-source-files` does, before any sync), **When** the S3 checks run against it as a negative control, **Then** the checks flag it as a violation of the rule "no pool copy is removed before its archive copy is checksum-verified and parity-covered". The flagged control is a precondition for a record, not a scenario outcome: if the harness does not flag the stock variant as FAIL, the S3 run is invalid (a harness defect), no record is written and S3 has no verdict.
6. **Given** a fault is injected in each of copy, checksum comparison (corrupted archive copy), sync (failed parity write) and verification, **When** the mover runs, **Then** it stops, reports which step failed, and the pool copy is left in place.
7. **Given** a client modifies a file in the pool after the mover has copied or checksummed it, using the same source-version lock as the mover, **When** the mover continues, **Then** the client write waits through M5, the newer pool version remains available, and it is not deleted or hidden by the stale archive copy; it is picked up by a later run.
8. **Given** the archive already holds an older version of a file at the same path and the pool holds a modified one, **When** the mover replaces the archived version and is killed during the replacement, **Then** a reader of the share sees either the complete old or the complete new version, never a partial or mixed one.
9. **Given** archive disks run out of space during the copy step, **When** the mover runs, **Then** it fails cleanly at that step with all pool copies intact.

---

### User Story 4 - S4: Degraded modes behave as designed and the lockouts hold (Priority: P2)

A maintainer needs evidence that each degraded state (pool degraded, archive degraded, both degraded) is detected, reported and enforced, including the actions that must be locked, and that normal operation returns after repair.

**Why this priority**: The degraded rules are what keep a single failure from becoming data loss, but they only matter once S1 to S3 show the base model works.

**Independent Test**: In a VM test, fail pool devices and archive disks in every order (and simultaneously), then exercise reads, writes and maintenance actions through the share and the management routes, and compare with the state table in the criteria.

**Degraded-state criteria**:

| State | Status and warning | Read | Write through share | Sync | Scrub | Mover | Direct archive write |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Healthy | Healthy; no warning | Allowed | Allowed (new files on the pool, archived files changed in place) | Allowed | Allowed | Allowed | Not tested: no client route exists |
| Pool degraded | Pool degraded; repair required | Allowed | Allowed (new files on the pool, archived files changed in place) | Allowed | Allowed | Allowed | Not tested: no client route exists |
| Archive degraded | Archive degraded; archive maintenance locked | Available data only; reads of the failed disk fail with `EIO` | New files to the pool allowed; changes to archived files refused with `EROFS` | Refused | Refused | Refused | Refused with `EROFS` |
| Both degraded | Pool and archive degraded; share read-only and maintenance locked | Available data only; reads of the failed disk fail with `EIO` | Refused with `EROFS` | Refused | Refused | Refused | Refused with `EROFS` |

**Acceptance Scenarios**:

1. **Given** a healthy system, **When** one pool device fails, **Then** a degraded-pool state with a warning is observable without any UI, shares keep serving reads and writes, written data stays readable, and archive operations remain permitted as defined in the criteria.
2. **Given** a healthy system, **When** one archive disk fails, **Then** a degraded-archive state is observable, the archive is read-only (changes to archived files are refused with `EROFS`), sync, scrub and mover are locked (scheduled and manual), new files written through the share continue and land in the pool, and files on surviving archive disks stay readable.
3. **Given** a healthy system, **When** a pool device and an archive disk fail at the same time (and in each order, one after the other), **Then** the both-degraded state is reached in every case and every share is read-only: write attempts fail with `EROFS` (with the corresponding SMB status as seen by the client), every client operation returns within the bounded time fixed in the criteria (initial target 30 s), and nothing is silently discarded.
4. **Given** each degraded state, **When** a sync, a scrub, a mover run, or a direct write to the archive is attempted where the criteria say it must be locked, **Then** the attempt is refused and the archive and parity are unchanged (verified by checksum).
5. **Given** a file located on the failed archive disk, **When** a client reads it, **Then** the read fails with `EIO`. When a client modifies it, the outcome is defined and data-safe (refused with `EROFS`, or `EIO` where the failed device itself is hit; the criteria name which one per case; no truncated or partial copy appears in the pool).
6. **Given** each degraded state, **When** the failed device is replaced and repaired (pool device resilvered or rebalanced; archive disk recovered as in S2), **Then** the state returns to healthy (or to the single remaining degraded state), locks are released only when the criteria allow it, and no data is lost.
7. **Given** a degraded state, **When** the VM is rebooted, **Then** the same state is detected and the same locks are enforced after boot.

---

### User Story 5 - S5: A configuration change is applied by rebuild, and failures leave the system safe (Priority: P2)

A maintainer needs to know how long applying a configuration change takes, what happens when the rebuild or the activation fails, and that rolling back a generation is explicit and does not get confused with Git history.

**Why this priority**: It decides ADR-0005 independently of the storage spikes. It does not depend on the storage spikes and can run in parallel with them, but it must finish before Phase 1 begins.

**Independent Test**: In a VM test that follows the system-disk layout of ADR-0004, drive the configuration through the same write, commit, rebuild sequence the backend will use, with valid, build-breaking and activation-breaking configurations, and compare the running system and the repository state with the expectations below.

**Acceptance Scenarios**:

1. **Given** a healthy system, **When** a representative set of changes is applied (no-op re-apply, adding a share, changing a storage parameter), **Then** the duration of each is measured over at least 5 runs on a warm store (and once on a cold store), and the median and worst case are recorded together with the environment. The values are judged against the duration thresholds fixed in the criteria (decided 2026-10-15, per change class decided 2026-10-16). The thresholds apply to each change class separately, on the warm-store median, and the worst class median decides: if every median is at most 5 minutes the result is a PASS; if any median is above 5 minutes and none is above 15 minutes it is still a PASS, but the Phase 1 plan MUST then include asynchronous apply with streamed progress; if any median is above 15 minutes it is a FAIL. The thresholds are an initial target and are not derived from measurements.
2. **Given** a valid configuration change, **When** it is applied, **Then** the system runs the new configuration, a new generation exists, and the configuration repository contains exactly one new commit for the change.
3. **Given** a configuration that cannot be built (invalid content, evaluation error, or failing build), **When** apply is attempted, **Then** the running system and the active generation are unchanged (the same active generation and the same running system store path as before the attempt), the failure is reported with the build log, and the failed commit stays in history for diagnosis (it is not reverted).
4. **Given** a configuration that builds but fails during activation (for example a mount or service that cannot start), **When** apply is attempted, **Then** the failure is reported, the previous known-good generation is restored as the running system, services are back in their previous state (the same set and state of active units as before the attempt), and the failed commit is kept. Because the rebuild tool switches the system profile before activation and does not roll back by itself, the restoration is behaviour that the test harness must supply and that this scenario tests (research finding F5).
5. **Given** a system with at least two generations, **When** an explicit rollback to the previous generation is requested, **Then** that generation becomes the running one and the one booted after a reboot, while the repository history is not rewritten and the repository head is unchanged.
6. **Given** a rollback, a failed build and a failed activation, **When** the state is inspected, **Then** the system reports separately the configuration revision at the repository head and the revision of the active generation, they are shown to differ where applicable, and a failed rebuild is visible as such and is not represented by Git history alone.
7. **Given** the configuration file lives in the flake directory, **When** the system is evaluated in pure mode, **Then** the configuration is read successfully, and a file placed outside the flake directory is demonstrated not to be readable.
8. **Given** the system has been rebooted, **When** the layout of ADR-0004 (clean root every boot, persistent store and state area) is in effect, **Then** the last applied configuration and its generations are still present and selectable.

---

### User Story 6 - Phase 0 produces a decision, not just test output (Priority: P1)

A maintainer reading the result needs, for each ADR, a recorded and reproducible reason why it is accepted or why the fallback applies, and needs the whole record to be re-runnable by someone else.

**Why this priority**: The purpose of Phase 0 is the decision gate ("Phase n+1 starts only when phase n passes its tests"). Test output nobody can map to an ADR decision has no value.

**Independent Test**: Review the repository after the spikes ran: criteria precede results in history, each spike has a verdict, and each ADR status is consistent with the verdicts.

**Acceptance Scenarios**:

1. **Given** the spikes have not been run, **When** the history is reviewed, **Then** frozen criteria for all five spikes exist and the first result record is committed after them (and, if X1 is run, its criteria precede its record in the same way).
2. **Given** all five spikes have run, **When** the results are reviewed, **Then** each spike has a PASS or FAIL record that names its scenarios, evidence and environment revision (S2 additionally needs the reviewed sub-record of S2.7 before its verdict is PASS or FAIL). An X1 record, if present, is marked non-gating and no ADR cites it as a condition.
3. **Given** S1 to S4 are PASS, **When** ADR-0002 is updated, **Then** its status is accepted and references the result records. Given S5 is PASS, ADR-0005 is accepted the same way.
4. **Given** a gating spike S1 to S5 FAILED, **When** the gate is applied, **Then** the ADR is not accepted, the fallback decision is recorded, and the ADR text and the design document are updated to reflect the fallback.
5. **Given** a clean machine with KVM and the pinned inputs, **When** the full suite (S1 to S5) is started with one command, **Then** it runs unattended to completion and produces the same verdicts as the recorded ones. X1 runs only when asked for explicitly.

---

### User Story 7 - X1: The overlayfs variant, as an optional experiment (Priority: P3, non-gating)

A maintainer who wants to know whether the former design (overlayfs with the archive as read-only lower layer and a pool subvolume as upper layer, so that writes to archived files copy up into the pool and deletes become whiteouts) would be worth adding later as an enhancement needs evidence about it. X1 produces that evidence and nothing else.

**Why this priority**: The baseline is the documented tiered-cache pattern. Overlayfs on top of mergerfs is not an established setup (mergerfs lists a read/write overlay on top of a read-only filesystem among its non-features, upstream documents no such stack, and the kernel documents changes to underlying layers while mounted as undefined). X1 can be skipped without affecting any ADR, S1 to S5, the ADR gate or the suite budget.

**Independent Test**: In a VM test with the S1 disk layout, mount the archive as read-only lower layer and a pool subvolume as upper layer, share the overlay, drive it from the SMB client VM, and compare the client-visible results and the on-disk layer contents with the expectations below.

**Statement of independence**: X1 has its own frozen criteria and, if run, its own result record with a PASS or FAIL verdict. A FAIL, a PASS or the absence of any X1 record changes no ADR status and triggers no fallback. A PASS only means that a copy-up enhancement can be considered later, as its own decision.

**Acceptance Scenarios**:

1. **Given** the overlay is mounted with the stacking depth of the FUSE layer at its default, at `0` and at `2`, **When** the mount is attempted, **Then** the outcome of each is recorded (expected from the kernel sources: default and `0` stack, `2` is refused), and the claim "the setting is required" is judged against it.
2. **Given** the overlay is mounted, **When** a client lists, reads and checksums files that exist only in the archive, **Then** names, sizes, timestamps, permissions and content are identical to a reference copy of the archive data.
3. **Given** a file that exists only in the archive, **When** a client modifies it, **Then** the modified version exists in the pool layer and the archive copy remains byte-identical (verified by checksum of the archive side).
4. **Given** a file that exists only in the archive, **When** a client deletes it, **Then** it disappears from the share, a whiteout exists in the pool layer, and the archive copy is untouched.
5. **Given** a deleted archive file, **When** a client creates a new file with the same name, **Then** the new content is shown and the old archived content never reappears; the same holds for a deleted-then-recreated directory.
6. **Given** files and directories of each origin, **When** a client renames or moves them (with and without the overlay's directory-redirect setting), **Then** the outcome is recorded; a rename that fails or leaves partial results is a FAIL of this scenario unless a documented setting makes it succeed.
7. **Given** extended attributes and SMB attributes set by a client on files of each origin, **When** the file is copied up, renamed, remounted or read from another client, **Then** the attributes are preserved.
8. **Given** a file larger than 4 GiB in the archive and another written new, **When** clients read, copy and checksum them and modify one byte of the archived one, **Then** all checksums match and the copy-up completes; with too little pool space the client receives a clear out-of-space error and no truncated file is visible.
9. **Given** the overlay is mounted and the mover changes the archive side and the pool layer directly (archive writes and deletions, pool copy removal, whiteout removal), **When** the share is read afterwards, **Then** either the share shows the change consistently without intervention, or an exact refresh procedure is recorded and verified; undefined or stale views without a working procedure are a FAIL. This is the situation the kernel documents as undefined.
10. **Given** a share populated with whiteouts and copied-up files, **When** the VM is rebooted, **Then** the same view is presented after remount.

---

### Edge Cases

- **Branch mutation while mounted.** The mover changes the archive branches and the pool branch underneath a mounted cache pool, and mergerfs caches name lookups and attributes for a configurable time (default 1 second). Whether the share is consistent without intervention is not known; S1 scenario 10 must settle this before S3 relies on it.
- **Kill that lands between two on-disk effects** that are individually crash-safe (for example a file created but its rename not yet done): covered by killing both at named step boundaries and mid-step.
- **Hard power-off versus process kill**: acknowledged writes may or may not be on disk. The mover must survive both; each is exercised separately.
- **Concurrent client write during the mover** (S3 scenario 7) and **concurrent client I/O during a degraded transition** (S4): no corruption, named errors and bounded time as defined in the verdict model.
- **Device failure during an in-place modification of an archived file, during a rename, or during the mover's sync**: the behaviour must be defined, never silent corruption.
- **Replacement disk smaller than the failed one, or already formatted**: recovery must refuse rather than guess (recorded in S2 criteria).
- **Empty archive, empty pool, directories that exist only on archive disks, zero-byte files, very long names, non-ASCII names, sparse files, hard links**: at least one case each in the S1 data set; failures are recorded.
- **Clock skew**: age-based selection is not under test, but file timestamps must survive the move unchanged.
- **Flaky test**: a scenario that passes only sometimes is a FAIL and the cause is investigated, not retried until green.
- **Uncommitted configuration change at apply time** (S5): the behaviour is defined and reported, never applied silently as if committed.

## Requirements *(mandatory)*

### Functional Requirements

**Process and records**

- **FR-001**: The project MUST have written acceptance criteria for each of S1 to S5 (and for X1 if it is run), committed before the first run of that spike, with each scenario carrying a stable identifier, a precondition, an action and an observable, checkable expected outcome.
- **FR-002**: Every acceptance scenario MUST be judged by an automated check that yields pass or fail without human interpretation. The only exception is the review judgement of the written recovery procedure of S2 scenario 7: its step checks are automated, and the judgement is carried by a separate reviewed sub-record (kind review, committed through a PR) that sets S2.7 to `pass` or `fail`. Until that sub-record exists the S2 verdict is `pending` and no ADR gate may be applied.
- **FR-003**: Every spike MUST end in a PASS or FAIL verdict under the verdict model above, recorded in a result record kept in version control.
- **FR-004**: A criteria amendment after a first run MUST be recorded with its reason and MUST invalidate the verdict obtained under the earlier wording.
- **FR-005**: Each result record MUST identify the exact environment inputs (the pinned revisions of the operating system and tools) so the run can be reproduced.
- **FR-006**: ADR-0002 MUST NOT be marked accepted unless S1, S2, S3 and S4 all have a PASS record; ADR-0005 MUST NOT be marked accepted unless S5 has a PASS record. The accepted ADR MUST reference those records.
- **FR-007**: For each gating spike S1 to S5 that FAILS, the result record MUST state which fallback applies, and the affected ADR and the design document MUST be updated accordingly. X1 has no fallback and does not update an ADR.

**Environment**

- **FR-008**: All spikes MUST run as reproducible tests in virtual machines using virtual disks only, with hardware acceleration, from a clean state on every run, with no dependence on physical disks, network services outside the test, or state left by an earlier run. *Verified by*: T032 (a missing `/dev/kvm` is a hard error, no emulated fallback), T138 (fresh state directory per run) and the record fields `environment.kvm` and `environment.profile`.
- **FR-009**: The test environment MUST provide archive disks of different sizes (each its own btrfs filesystem), at least one parity disk, and at least two pool devices, and a separate SMB client for S1. *Verified by*: T026 and T027 (topology profiles and nodes), T041 (client VM) and the record fields `environment.disks` and `environment.vms`.
- **FR-010**: Fault injection (device failure, process kill, power-off, injected write or checksum faults, out-of-space) MUST be deterministic and triggered at named points of the procedure under test, not by timing. *Verified by*: T035, T063 and T083 (host-side fault kinds, no sleep triggers) and the fault kind and fault point recorded for every case of a record.
- **FR-011**: The suite MUST run unattended in CI and by a single documented command locally on a machine with KVM, and every scenario MUST give the same verdict on three consecutive runs from clean state.

**S1 tiered-cache share**

- **FR-012**: The criteria for S1 MUST cover: the two-pool setup (base pool of the archive disks, cache pool with the pool branch first) and its create policy; reading archive files; placement of new files and directories on the pool, including inside archive-only directories; in-place modification and deletion of archived files; recreation after deletion; renames of files and directories of every origin; extended attributes and SMB file attributes; SMB client access from a separate client; files larger than 4 GiB; pool out of space; the share's view before, during and after a file is moved by the mover's method; persistence across reboot.
- **FR-013**: S1 MUST record every mount or share setting found to be required for a PASS (including create policy, branch modes, out-of-space behaviour and any rename setting), so the product module can reproduce them.
- **FR-014**: S1 MUST verify that no client operation writes new data to an archive disk: new files and directories land only on the pool, and the only client-driven changes to an archive disk are in-place operations on already archived files (modify, delete, rename, attribute or xattr change).

**S2 parity recovery**

- **FR-015**: The criteria for S2 MUST cover failure before a sync, failure during a sync (device failure and process kill), failure with changes on surviving disks since the last sync, the same history with the snapshot-supported workflow (with the result compared against the run without snapshots), sync after failure and replacement (including how that hazard is reached), double failure beyond parity strength, and read-only gating.
- **FR-016**: Every scenario of S2 MUST state in advance which files are expected to be recoverable and which are expected to be lost, computed per parity block position, and the verdict compares the outcome against that prediction.
- **FR-017**: S2 MUST produce a written safe recovery and resynchronisation sequence, executed in full by the test, and MUST demonstrate that read-only gating blocks every route to a sync, scrub or mover action while a disk is failed.

**S3 mover**

- **FR-018**: The criteria for S3 MUST enumerate the mover's step boundaries before the spike runs, covering at least copy, checksum, sync, verification and pool copy removal, and MUST require a kill at every boundary and mid-step, each by process kill and by hard power-off.
- **FR-019**: S3 MUST check, after every interruption and after every retry, these invariants with an independent oracle: (a) every live file reads back complete and correct through the share; (b) no pool copy was removed before its archive copy was checksum-verified and parity-covered; (c) no file that was not deleted by a client disappears and no client-deleted file reappears; (d) no file is visible with partial content; (e) a single device failure afterwards loses no live file.
- **FR-020**: S3 MUST require that a retry converges to the uninterrupted final state, including a retry that is itself interrupted.
- **FR-021**: S3 MUST require that failure of copy, checksum, sync or verification leaves the pool copy in place, and the failing step reported.
- **FR-022**: S3 MUST cover concurrent modification of a file by a client during the mover run, replacement of an existing archive version, and archive out-of-space.

**S4 degraded modes**

- **FR-023**: The criteria for S4 MUST include a state table that defines, for each of healthy, pool degraded, archive degraded and both degraded, which operations are allowed (read, write, sync, scrub, mover, direct archive write) and what status and warning the system shows. The direct archive write cells of the healthy and pool-degraded states read "not tested: no client route exists"; the refusal is tested in the archive-degraded and both-degraded states.
- **FR-024**: S4 MUST test the transition into each degraded state, both-degraded reached in both orders and simultaneously, and the return to healthy after repair.
- **FR-025**: S4 MUST verify that locked actions are refused by every route and that refused actions leave archive and parity unchanged, and that states and locks are re-established after reboot.
- **FR-026**: Each degraded state MUST be observable through a status indication that does not depend on a UI.

**S5 declarative rebuild**

- **FR-027**: The criteria for S5 MUST cover: duration measurement for named change classes (cold and warm), failed build, failed activation, explicit generation rollback (including after reboot), the separation of repository head and active generation, and a pure-evaluation check of the configuration location.
- **FR-028**: S5 MUST show that after a failed build or failed activation the running system equals the last known-good generation and that the failure is reported to the caller with its log.
- **FR-029**: S5 MUST show that a rollback changes the active generation without altering the repository history, and that the two are reported separately.
- **FR-030**: S5 MUST record the measured rebuild durations in its result record, in the form the criteria prescribe, and judge them against the thresholds set in the criteria, per change class, with the worst class median deciding.

**Fallbacks**

- **FR-031**: The spike criteria MUST state the fallback for each gating spike S1 to S5, as follows, and the result records MUST apply it on FAIL. X1 has no fallback:
  - **S1 FAIL**: the tiered-cache share is abandoned; ADR-0002 falls back to direct archive access, read-only when degraded (the fallback already written in ADR-0002). The merged share is dropped; how the pool is exposed in that case, and whether the mover design survives, is decided in the ADR update.
  - **S2 FAIL** (recovery or gating does not behave as ADR-0002 assumes): the same ADR-0002 fallback applies, and the safe-sequence assumptions in the ADR are corrected before any further decision. If only the snapshot scenario (S2 scenario 9) fails, ADR-0002 stays proposed and the model is amended so that archived files are no longer changed in place (for example a read-only archive with changes taken into the pool, which is what X1 would provide) before S2 is re-run (confirmed by the maintainer 2026-10-15).
  - **S3 FAIL**: the mover sequence is corrected and S3 is re-run. If no sequence is found that satisfies all invariants, the tiered model is dropped and the ADR-0002 fallback applies.
  - **S4 FAIL**: the lock design is corrected and S4 is re-run. Both the tiered model and the fallback need the lockouts, so S4 has no fallback of its own: ADR-0002 stays proposed until S4 passes.
  - **S5 FAIL** (decided 2026-10-15): ADR-0005 stays proposed, the decision is amended (for example build and test before switching) and S5 is re-run. Writing the configuration files directly, without rebuilds, is adopted only if no amendment passes.
  - **X1**: no fallback and no consequence for any ADR, whatever its verdict.

**Negative control and optional experiment**

- **FR-032**: S3 MUST include the stock mover pattern (source deleted immediately after the copy, before parity covers the copy) as a negative control that the S3 checks flag as a failure; a control that is not flagged invalidates the S3 run: it is a harness defect, no record is written and S3 has no verdict.
- **FR-033**: X1 MUST have its own written criteria (frozen before its first run, with the same verdict model) covering: the stacking depth at its default, `0` and `2`; reading, copy-up, deletion, whiteouts and recreation; renames with and without the directory-redirect setting; extended attributes; files larger than 4 GiB and pool out of space; the mover changing the lower and upper layer while the overlay is mounted; persistence across reboot.
- **FR-034**: X1 MUST be runnable and skippable on its own. Its result record MUST state that it is non-gating; the ADR gate, the suite verdict matrix and `SC-001` to `SC-010` MUST NOT depend on it, and its absence MUST NOT block Phase 1. *Verified by* (last clause): T140 (an X1 record or its absence never changes the ADR gate result), T156 (`spikectl verify` passes with and without an X1 record) and T147 (the Phase 1 gate names the five gating verdicts only).

### Key Entities

- **Spike**: one of S1 to S5, or the optional experiment X1; has a name, frozen criteria, scenarios, and zero or more result records. Only S1 to S5 can gate an ADR.
- **Acceptance scenario**: a precondition, an action and an observable expected outcome with a stable identifier; judged pass or fail automatically.
- **Criteria version**: the frozen wording of a spike's scenarios; amendments create a new version and void earlier verdicts.
- **Result record**: the outcome of one record-mode run of one spike: criteria version, environment revision, date, per-scenario outcome (variant outcomes marked `gating: false` in the evidence and case list), evidence reference and verdict; failed S1 to S5 records also carry a fallback decision. The reviewed sub-record (kind review) of S2.7 is a separate record that references the S2 record.
- **Verdict**: PASS or FAIL for a spike, derived only from its scenario outcomes; `pending` for S2 until the reviewed sub-record of S2.7 exists, `none` while no valid record exists.
- **ADR gate**: the rule linking spike verdicts to an ADR status change (proposed to accepted, or fallback adopted).
- **Environment**: the VM topology (disks, client), pinned inputs, and fault-injection points used by a run.
- **Recovery procedure**: the written, tested sequence produced by S2; its acceptability is judged by the reviewed sub-record of S2.7.
- **Degraded-state table**: the allowed-operations table per state defined by S4.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All five spikes have a recorded PASS or FAIL verdict (5 of 5), each traceable to a result record naming its criteria version and environment revision (for S2 the verdict stands once the reviewed sub-record of S2.7 exists).
- **SC-002**: For 5 of 5 spikes, the criteria are committed before the first result record; for 0 spikes does a verdict stand that was obtained under criteria later amended.
- **SC-003**: 0 ADR status changes happen without a matching verdict record: ADR-0002 is accepted only with 4 of 4 PASS (S1 to S4), ADR-0005 only with S5 PASS, and each failed gating spike has its fallback decision recorded and applied to the ADR text. X1 never produces an ADR fallback.
- **SC-004**: Every acceptance scenario is automated except the review judgement of S2.7 (its step checks are automated), and 100% of scenarios return the same verdict on 3 consecutive clean runs.
- **SC-005**: S3 kills the mover at 100% of the enumerated step boundaries and mid-step points, each in both kill modes, and records the number of crash points covered; the result is zero lost live files and zero premature pool-copy removals, and the stock-pattern negative control is flagged as a failure.
- **SC-006**: S2 recovers 100% of the files predicted as recoverable bit-exact in every scenario, and the set of files reported lost equals the predicted set exactly (no more, no fewer).
- **SC-007**: S4 covers all transitions in the state table (healthy to each degraded state, both-degraded in both orders and simultaneously, and repair back) and 100% of lockout attempts are refused with archive and parity unchanged.
- **SC-008**: S5 reports rebuild duration (median and worst case over at least 5 warm runs per change class, plus one cold run, judged by the worst class median) and 100% of failed builds and failed activations leave the running system equal to the last known-good generation.
- **SC-009**: The full suite (S1 to S5, without X1) starts with one command, runs without manual steps on the reference environment, and completes within 2 hours on the project's reference runner.
- **SC-010**: At the end of Phase 0 the repository contains test, specification and decision artifacts only; no product code was added for the sake of the spikes.
- **SC-011**: If X1 is run, it has a recorded PASS or FAIL verdict traceable to its own frozen criteria; 0 ADR status changes, fallbacks or Phase 1 decisions depend on whether it was run or on its verdict.

## Out of Scope

- The backend, API, UI, and any product component of Phase 1 or later. Spikes may use throwaway test scaffolding only, which is not carried into the product.
- Docker and container management, VM management, the plugin system.
- Notifications, SMART monitoring and real-world disk health detection (failures are injected, not predicted).
- Physical hardware validation, real disk failure modes, power-loss on real devices and performance benchmarking beyond the S5 rebuild duration and the S1 large-file correctness.
- Production SMB configuration (users, permissions, tuning); only what S1 needs to prove client access is configured.
- Age or fill-level policies for selecting what the mover moves; the spikes pick the files explicitly.
- Real-time parity, alternative storage layouts beyond the documented fallback and the optional experiment X1, and revisiting ADR-0001, ADR-0003 and ADR-0004.
- Choosing between the candidate snapshot tools for the archive (see the plan's open items); S2 measures the workflow the plan selects.
- Choosing a project licence.

## Assumptions

- The audience is the project maintainers. Because the spikes exist to test named technologies chosen in the ADRs (a pool filesystem, a union filesystem with a tiered-cache pattern, a parity tool with btrfs snapshots, NixOS, and for X1 an overlay filesystem), those names appear where they are the subject under test; this is not a choice of implementation made by this spec.
- Tests run as NixOS VM tests on a host with hardware virtualisation. A KVM-capable CI runner is available; without it, the CI requirement (FR-011) cannot be met and that is escalated rather than waived.
- Virtual disks stand in for real disks. Failures, crashes and power-off are injected from the host side. Findings that depend on real hardware behaviour are out of scope and noted as residual risk in the result records.
- Operating system and tool revisions are pinned for the whole phase, so a verdict is tied to a known environment.
- The S1 large-file size (above 4 GiB) is chosen to cross common 32-bit limits; the pool and archive virtual disks are sized to allow it.
- The baseline storage model follows the tiered-cache pattern documented by mergerfs, which states that it does not natively support tiered caching; the stock mover scripts delete their source immediately after the copy, which the project's safety rule forbids, so the mover is a project component (S3). The overlayfs variant is not an established setup and is therefore only the optional experiment X1.
- The expectation that a snapshot-supported sync and fix keeps files recoverable after in-place changes rests on the documentation of the snapshot tools only (a native snapshot option exists in a SnapRAID release candidate); S2 scenario 9 is where it is observed.
- In the pool-degraded state, sync, scrub and mover remain allowed, because the design locks them only for archive degradation; the S4 criteria confirm or refine this in the state table before the spike runs.
- Single parity (one parity disk) is the baseline configuration for S2 and S3; double failure behaviour is recorded for information in S2.
- The S1 baseline is `ff` with `passthrough.io=off` and `cache.files=off` (the defaults); the other settings named in S1 scenarios 2, 6 and 8 are informational variants only.
- No client route to an archive disk exists in the healthy and pool-degraded states, so a direct archive write is not tested there; files are added to archive disks only by the mover path (S2 history, S3), never through the share.
- The numeric bounds (30 s per client operation, 5 s per client write during the mover's sync step) and the S5 thresholds are initial targets that the criteria fix before the freeze. The JSON schemas are not changed by this clarification; carrying the review sub-record (kind review), the `pending` verdict and the per-case `gating` flag in the schemas is a follow-up if the schemas turn out not to cover it.
- S5 follows the system-disk layout of ADR-0004 where the VM test can reproduce it, since rollback across a reboot is meaningless without it.
- The recovery procedure produced by S2 and the mover step list used by S3 are the same artifacts the product will later implement; a change to either after Phase 0 reopens the corresponding spike.
- Phase 0 deliverables land on `main` through pull requests under the project's workflow, like all other changes.
- Phase 1 does not start until all five verdicts are recorded and each ADR is either accepted or its fallback adopted (design rule: phase n+1 starts only when phase n passes its tests).
- The 2-hour suite budget (SC-009) is an initial target; the plan may revise it with a recorded reason if large-file and crash-point coverage need more. X1 is outside this budget.

## Open Questions

None that block the spec. The two decisions that could not be settled from the design were resolved on 2026-10-15 and are recorded inline: (1) the acceptable rebuild duration (S5 scenario 1), and (2) the fallback for a failed S5 (FR-031). S5 gates ADR-0005 only; ADR-0002 is gated by S1 to S4. The maintainer's decision of 2026-10-15 to make the tiered-cache pattern the baseline and overlayfs the optional experiment X1 is reflected throughout. Two choices of the baseline are left to the plan as open items with recommendations: the snapshot tool of the archive and the create policy of the cache pool.
