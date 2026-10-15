# Feature Specification: Phase 0 Storage Model Spikes

**Feature Branch**: `001-phase0-storage-spikes`

**Created**: 2026-10-15

**Status**: Draft

**Input**: User description: "Phase 0 of nasaex: prove the two-tier storage model and the declarative rebuild model in reproducible NixOS VM tests with virtual disks, before any product code is written. Each spike (S1-S5) has acceptance criteria that are written down BEFORE the spike is run, a clear pass/fail outcome, and a recorded result. The phase gates ADR-0002 and ADR-0005: they move from proposed to accepted only when their spikes pass, otherwise the documented fallback applies."

**Sources of truth**: [Design](../../docs/plans/2026-10-15-nasaex-design.md) (section "Phase 0 spikes"), [ADR-0002](../../docs/adr/0002-two-tier-storage.md), [ADR-0005](../../docs/adr/0005-declarative-config-applied-by-rebuild.md). Where this spec and the design disagree, the design wins and this spec is corrected.

## Overview

nasaex stakes its storage safety on two untested ideas: a two-tier layout (mirrored pool above, parity-protected archive below, presented as one share) and a rebuild-based configuration model. Phase 0 exists to find out, cheaply and before any product code exists, whether those ideas hold. This feature specifies **what must be proven and how pass or fail is judged**. It does not specify how the tests are built.

The "users" of this feature are the project maintainers, who must decide whether to accept ADR-0002 and ADR-0005. The product's end users (NAS owners and their SMB clients) appear only as the people whose data the spikes protect.

Spike identifiers used throughout: **S1** stacked share filesystem, **S2** parity recovery, **S3** mover crash safety, **S4** degraded modes, **S5** declarative rebuild.

## Verdict model *(applies to every spike)*

- **Criteria first.** The acceptance criteria of a spike are written down and committed before the spike is run for the first time. The criteria of a spike are frozen at that point. Changing them later is allowed only as a recorded amendment that states the reason, and any verdict obtained under the old wording is void.
- **Binary verdict.** A spike ends as **PASS** or **FAIL**. There is no partial pass: a spike passes only if every one of its scenarios passes. A scenario that could not be executed, was skipped or is flaky counts as failed.
- **Recorded result.** Every run produces a result record kept in version control next to the criteria: spike, criteria version, environment revision, date, per-scenario outcome, evidence reference, verdict, and, on FAIL, the fallback decision.
- **Re-runs.** After a documented design correction a spike may be re-run. A failed run is never deleted; the new run is a new record that references the earlier one.
- **ADR gate.** ADR-0002 moves from proposed to accepted only when S1, S2, S3 and S4 all PASS. ADR-0005 moves from proposed to accepted only when S5 PASSES. If a gating spike FAILS, the ADR stays proposed until the documented fallback has been adopted and the ADR text updated to say so.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - S1: The stacked share behaves like a normal filesystem (Priority: P1)

A maintainer needs evidence that the share (a read-only archive pool underneath, a writable pool layer on top) can be used by real SMB clients exactly like an ordinary folder, and that none of the operations a NAS user performs breaks because of the stacking. Everything else in the storage model depends on this.

**Why this priority**: If the stacked view fails, the two-tier model has no way to present one share, and ADR-0002 falls back to direct archive access. It is the cheapest spike to run and the one that can end the design earliest.

**Independent Test**: In a VM test with several archive disks of different sizes and a two-device pool, mount the share, drive it from a separate SMB client VM, and compare the client-visible results and the on-disk layer contents with the expectations below.

**Acceptance Scenarios**:

1. **Given** the share is mounted with `passthrough.max-stack-depth=1`, **When** a client lists, reads and checksums files that exist only in the archive, **Then** names, sizes, timestamps, permissions and content are identical to a reference copy of the archive data.
2. **Given** `passthrough.max-stack-depth=1` is deliberately removed, **When** the share is mounted, **Then** the outcome (mount refused or broken behaviour) is recorded and confirms that the setting is required. If the stack works without it, the finding is recorded and the setting is re-judged.
3. **Given** a file that exists only in the archive, **When** a client modifies it, **Then** the modified version exists in the pool layer, the client sees the new content, and the archive copy remains byte-identical to before (verified by checksum of the archive side).
4. **Given** a file that exists only in the archive, **When** a client deletes it, **Then** it disappears from the share, a whiteout exists in the pool layer, and the archive copy is untouched.
5. **Given** a deleted archive file, **When** a client creates a new file with the same name, **Then** the new content is shown and the old archived content never reappears. The same holds for a deleted-then-recreated directory: no entries from the old archived directory appear in the new one.
6. **Given** files and directories of each origin (archive only, pool only, both), **When** a client renames or moves them within the share, **Then** the rename succeeds, the complete tree (including archive-origin subdirectories) is present under the new name, and nothing is left under the old name. A rename that fails or leaves partial results is a FAIL unless a documented setting makes it succeed, in which case that setting is recorded as required.
7. **Given** extended attributes and SMB-relevant file attributes set by a client on files of each origin, **When** the file is copied up, renamed, remounted or read from another client, **Then** the attributes are preserved. Attributes already present on archive files are visible through the share.
8. **Given** a file larger than 4 GiB in the archive and another written new through the share, **When** clients read, copy and checksum them, and modify one byte of the archived one, **Then** all checksums match the expectation end to end and the copy-up of the large file completes.
9. **Given** the pool layer has too little free space for a copy-up, **When** a client modifies an archive file, **Then** the client receives a clear out-of-space error and both the archive copy and the partial pool state remain consistent (no truncated file visible).
10. **Given** the share is mounted, **When** the archive side is changed directly (file added, file removed, file replaced) the way the mover will do it, **Then** the outcome is recorded: either the share shows the change consistently without intervention, or an exact refresh procedure is recorded and verified to make the share consistent. Undefined or stale views without a working procedure are a FAIL.
11. **Given** a share populated with whiteouts and copied-up files, **When** the VM is rebooted, **Then** the same view is presented after remount.

---

### User Story 2 - S2: Parity recovery works as assumed, and the dangerous sequence is blocked (Priority: P1)

A maintainer needs to know exactly what a disk failure does to recoverability in each situation (before a parity sync, during a sync, and after a sync that should not have run), and needs a documented, tested recovery sequence plus proof that read-only gating prevents the destructive case.

**Why this priority**: ADR-0002 rests on the claim that a sync after a disk failure destroys recoverability and that the archive can therefore be protected by locking it. If that claim or the recovery sequence is wrong, the data-loss promise of the product is wrong.

**Independent Test**: In a VM test with data disks and parity disks, populate, sync, change data, fail a data disk at different moments, replace it, run the recovery sequence, and compare every file against reference checksums.

**Acceptance Scenarios**:

1. **Given** a fully synced archive and no later changes, **When** one data disk fails and is replaced, **Then** following the documented recovery sequence restores 100% of that disk's files bit-exact, and the system is fully protected again after the resynchronisation step.
2. **Given** a synced archive in which files were added, modified and deleted on the surviving disks after the last sync, **When** one data disk fails, **Then** the files of the failed disk that were affected by those changes are identified, the unaffected files are recovered bit-exact, and the set of unrecoverable files matches the prediction written in the criteria.
3. **Given** files written to the failed disk after the last sync, **When** that disk fails, **Then** those files are reported as unrecoverable (documents the unprotected window and confirms why new data goes to the pool, not the archive).
4. **Given** a sync is running, **When** a data disk fails during the run (and separately when the sync process is killed during the run), **Then** the resulting recoverability is measured and recorded, and the recovery sequence is shown to recover everything the criteria predict. If recovery is impossible for some files, those files are listed and the sequence's guidance for this case is recorded.
5. **Given** a data disk has failed and been replaced with an empty disk, **When** a sync is run before the recovery step, **Then** the loss of recoverability for the failed disk's files is demonstrated and recorded. This confirms the hazard the design relies on.
6. **Given** read-only gating is active after a disk failure, **When** a sync, scrub or any mover action is attempted by any route (scheduled, manual, automatic), **Then** the attempt is refused and the archive content and parity are unchanged (verified by checksum), and a subsequent recovery restores 100% of the synced data bit-exact.
7. **Given** the documented safe sequence (detect failure, lock, replace disk, recover, verify, resynchronise, unlock), **When** it is executed verbatim by the test, **Then** every step has an explicit check, the final state passes a full verification, and the sequence is saved as a written procedure in the result record.
8. **Given** two data disks fail with a single parity level configured, **When** recovery is attempted, **Then** the limit is recorded: which files are lost and that the system reports the condition instead of claiming success.

**Recoverability predictions** (frozen before the first S2 run):

| Scenario | Expected recoverability |
| --- | --- |
| S2.1: failure after a complete sync with no later changes | Every file present on the failed disk at the last completed sync is recoverable bit-for-bit. |
| S2.2: changes on surviving disks after the last sync | Files on the failed disk unchanged since the last completed sync are recoverable; files first written or last changed on the failed disk after that sync are not. Changes on surviving disks are reported separately and are not counted as recovered failed-disk data. |
| S2.3: files written to the failed disk after the last sync | Those files are not recoverable; all files covered by the last completed sync remain subject to the S2.1 prediction. |
| S2.4: device failure or process kill during sync | Files whose parity blocks were completed and verified before the interruption are recoverable; files in blocks not completed and verified are expected to be listed as unrecoverable. The result record maps this prediction to each file. |
| S2.5: sync before recovery of a replaced disk | Files that were present only on the failed disk before the premature sync are expected to be unrecoverable. |
| S2.6: read-only gating after failure | All files covered by the last completed sync are recoverable; the refused actions leave the prediction and the parity content unchanged. |
| S2.7: documented safe sequence | All files covered by the last completed sync are recoverable after the sequence and final verification. |
| S2.8: two data disks with single parity | Recovery is not claimed for files whose content cannot be reconstructed; the result lists those files and reports the parity limit. |

---

### User Story 3 - S3: The mover never loses a pool copy, whatever interrupts it (Priority: P1)

A maintainer needs proof that the mover (the component that migrates cold data from the pool into the archive and applies deletions) can be killed at any point and that retrying it never loses data, never resurrects deleted files, and never removes anything before its replacement is protected.

**Why this priority**: The mover is the only component that moves data out of the redundant pool into the parity-protected archive. ADR-0002 names it the critical component. A single flaw here silently loses user data.

**Independent Test**: In a VM test, run the documented mover sequence for a set of files and whiteouts with a deterministic kill injected at each named step boundary (process kill and hard power-off), then check the invariants below after each interruption and after each retry.

**Acceptance Scenarios**:

1. **Given** a set of pool-only files and deletions (whiteouts) to apply, **When** the mover runs uninterrupted, **Then** all files end up in the archive with matching checksums, the pool copies are gone only after a completed and verified sync, the archive deletions are synced and verified before the whiteouts are removed, and the share view is identical before and after.
2. **Given** the documented step boundaries (after copy, after checksum, after sync, after verification, after pool copy removal, after archive deletion, after the second sync, after the second verification, after whiteout cleanup, plus a kill in the middle of each step), **When** the mover is killed at each of them by process kill and again by hard power-off, **Then** after every kill and restart each live file reads through the share with correct content (never missing, never partial), no deleted file reappears, and no pool copy has been removed that is not already checksum-verified and parity-covered in the archive.
3. **Given** each interruption point, **When** the mover is retried (and when the retry is itself killed and retried again), **Then** the run converges to the same final state as an uninterrupted run, without duplicates, without data loss and without manual cleanup.
4. **Given** each interruption point, **When** a single device is then failed (a pool device, or the archive disk holding the file) and the recovery procedure of S2 is applied, **Then** every live file is recovered intact. This proves "every live file is protected by the mirror or by parity at all times", not just "readable".
5. **Given** a whiteout whose archive file has been deleted but not yet synced, **When** the mover is killed, **Then** the whiteout is still present and the deleted file does not reappear through the share.
6. **Given** a fault is injected in each of copy, checksum comparison (corrupted archive copy), sync (failed parity write) and verification, **When** the mover runs, **Then** it stops, reports which step failed, and the pool copy and the whiteout are both left in place.
7. **Given** a file is modified in the pool after the mover has copied or checksummed it, **When** the mover continues, **Then** the newer pool version is not deleted and not replaced by the stale archive copy; it is picked up by a later run.
8. **Given** the archive already holds an older version of a file and the pool holds a modified one, **When** the mover replaces the archived version and is killed during the replacement, **Then** a reader of the share sees either the complete old or the complete new version, never a partial or mixed one.
9. **Given** archive disks run out of space during the copy step, **When** the mover runs, **Then** it fails cleanly at that step with all pool copies intact.

---

### User Story 4 - S4: Degraded modes behave as designed and the lockouts hold (Priority: P2)

A maintainer needs evidence that each degraded state (pool degraded, archive degraded, both degraded) is detected, reported and enforced, including the actions that must be locked, and that normal operation returns after repair.

**Why this priority**: The degraded rules are what keep a single failure from becoming data loss, but they only matter once S1 to S3 show the base model works.

**Independent Test**: In a VM test, fail pool devices and archive disks in every order (and simultaneously), then exercise reads, writes and maintenance actions through the share and the management routes, and compare with the state table in the criteria.

**Degraded-state criteria**:

| State | Status and warning | Read | Write through share | Sync | Scrub | Mover | Direct archive write |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Healthy | Healthy; no warning | Allowed | Allowed | Allowed | Allowed | Allowed | Refused; archive changes use the controlled mover/sync path |
| Pool degraded | Pool degraded; repair required | Allowed | Allowed | Allowed | Allowed | Allowed | Refused; archive changes use the controlled mover/sync path |
| Archive degraded | Archive degraded; archive maintenance locked | Available data only; failed-disk reads report a defined error | Allowed to the pool | Refused | Refused | Refused | Refused |
| Both degraded | Pool and archive degraded; share read-only and maintenance locked | Available data only; failed-disk reads report a defined error | Refused with a clear error | Refused | Refused | Refused | Refused |

**Acceptance Scenarios**:

1. **Given** a healthy system, **When** one pool device fails, **Then** a degraded-pool state with a warning is observable without any UI, shares keep serving reads and writes, written data stays readable, and archive operations remain permitted as defined in the criteria.
2. **Given** a healthy system, **When** one archive disk fails, **Then** a degraded-archive state is observable, the archive is read-only, sync, scrub and mover are locked (scheduled and manual), writes through the share continue and land in the pool, and files on surviving archive disks stay readable.
3. **Given** a healthy system, **When** a pool device and an archive disk fail at the same time (and in each order, one after the other), **Then** the both-degraded state is reached in every case and every share is read-only: write attempts fail with a clear error, nothing hangs, and nothing is silently discarded.
4. **Given** each degraded state, **When** a sync, a scrub, a mover run, or a direct write to the archive is attempted where the criteria say it must be locked, **Then** the attempt is refused and the archive and parity are unchanged (verified by checksum).
5. **Given** a file located on the failed archive disk, **When** a client reads it, **Then** the read fails with a defined error. When a client modifies it, the outcome is defined and data-safe (no truncated copy appears in the pool).
6. **Given** each degraded state, **When** the failed device is replaced and repaired (pool device resilvered or rebalanced; archive disk recovered as in S2), **Then** the state returns to healthy (or to the single remaining degraded state), locks are released only when the criteria allow it, and no data is lost.
7. **Given** a degraded state, **When** the VM is rebooted, **Then** the same state is detected and the same locks are enforced after boot.

---

### User Story 5 - S5: A configuration change is applied by rebuild, and failures leave the system safe (Priority: P2)

A maintainer needs to know how long applying a configuration change takes, what happens when the rebuild or the activation fails, and that rolling back a generation is explicit and does not get confused with Git history.

**Why this priority**: It decides ADR-0005 independently of the storage spikes. It does not depend on the storage spikes and can run in parallel with them, but it must finish before Phase 1 begins.

**Independent Test**: In a VM test that follows the system-disk layout of ADR-0004, drive the configuration through the same write, commit, rebuild sequence the backend will use, with valid, build-breaking and activation-breaking configurations, and compare the running system and the repository state with the expectations below.

**Acceptance Scenarios**:

1. **Given** a healthy system, **When** a representative set of changes is applied (no-op re-apply, adding a share, changing a storage parameter), **Then** the duration of each is measured over at least 5 runs on a warm store (and once on a cold store), and the median and worst case are recorded together with the environment. The values are judged against the duration threshold fixed in the criteria [NEEDS CLARIFICATION: acceptable rebuild duration is not defined by the design; recommended: median at most 5 minutes for a typical change on a warm store, FAIL above 15 minutes].
2. **Given** a valid configuration change, **When** it is applied, **Then** the system runs the new configuration, a new generation exists, and the configuration repository contains exactly one new commit for the change.
3. **Given** a configuration that cannot be built (invalid content, evaluation error, or failing build), **When** apply is attempted, **Then** the running system and the active generation are unchanged, the failure is reported with the build log, and the failed commit stays in history for diagnosis (it is not reverted).
4. **Given** a configuration that builds but fails during activation (for example a mount or service that cannot start), **When** apply is attempted, **Then** the failure is reported, the previous known-good generation is restored as the running system, services are back in their previous state, and the failed commit is kept.
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

1. **Given** the spikes have not been run, **When** the history is reviewed, **Then** frozen criteria for all five spikes exist and the first result record is committed after them.
2. **Given** all five spikes have run, **When** the results are reviewed, **Then** each spike has a PASS or FAIL record that names its scenarios, evidence and environment revision.
3. **Given** S1 to S4 are PASS, **When** ADR-0002 is updated, **Then** its status is accepted and references the result records. Given S5 is PASS, ADR-0005 is accepted the same way.
4. **Given** a spike FAILED, **When** the gate is applied, **Then** the ADR is not accepted, the fallback decision is recorded, and the ADR text and the design document are updated to reflect the fallback.
5. **Given** a clean machine with KVM and the pinned inputs, **When** the full suite is started with one command, **Then** it runs unattended to completion and produces the same verdicts as the recorded ones.

---

### Edge Cases

- **Layer mutation while mounted.** Changing the lower (archive) side of a stacked mount while it is mounted is not guaranteed by the kernel to be seen consistently. S1 scenario 10 must settle this before S3 relies on it.
- **Kill that lands between two on-disk effects** that are individually crash-safe (for example a file created but its rename not yet done): covered by killing both at named step boundaries and mid-step.
- **Hard power-off versus process kill**: acknowledged writes may or may not be on disk. The mover must survive both; each is exercised separately.
- **Concurrent client write during the mover** (S3 scenario 7) and **concurrent client I/O during a degraded transition** (S4): no corruption, defined errors.
- **Device failure during copy-up, during a rename, or during the mover's sync**: the behaviour must be defined, never silent corruption.
- **Replacement disk smaller than the failed one, or already formatted**: recovery must refuse rather than guess (recorded in S2 criteria).
- **Empty archive, empty pool, only whiteouts, zero-byte files, very long names, non-ASCII names, sparse files, hard links**: at least one case each in the S1 data set; failures are recorded.
- **Clock skew**: age-based selection is not under test, but file timestamps must survive copy-up and move unchanged.
- **Flaky test**: a scenario that passes only sometimes is a FAIL and the cause is investigated, not retried until green.
- **Uncommitted configuration change at apply time** (S5): the behaviour is defined and reported, never applied silently as if committed.

## Requirements *(mandatory)*

### Functional Requirements

**Process and records**

- **FR-001**: The project MUST have written acceptance criteria for each of S1 to S5, committed before the first run of that spike, with each scenario carrying a stable identifier, a precondition, an action and an observable, checkable expected outcome.
- **FR-002**: Every acceptance scenario MUST be judged by an automated check that yields pass or fail without human interpretation. The only deliverable judged by review is the written recovery procedure of S2 scenario 7.
- **FR-003**: Every spike MUST end in a PASS or FAIL verdict under the verdict model above, recorded in a result record kept in version control.
- **FR-004**: A criteria amendment after a first run MUST be recorded with its reason and MUST invalidate the verdict obtained under the earlier wording.
- **FR-005**: Each result record MUST identify the exact environment inputs (the pinned revisions of the operating system and tools) so the run can be reproduced.
- **FR-006**: ADR-0002 MUST NOT be marked accepted unless S1, S2, S3 and S4 all have a PASS record; ADR-0005 MUST NOT be marked accepted unless S5 has a PASS record. The accepted ADR MUST reference those records.
- **FR-007**: For each spike that FAILS, the result record MUST state which fallback applies, and the affected ADR and the design document MUST be updated accordingly.

**Environment**

- **FR-008**: All spikes MUST run as reproducible tests in virtual machines using virtual disks only, with hardware acceleration, from a clean state on every run, with no dependence on physical disks, network services outside the test, or state left by an earlier run.
- **FR-009**: The test environment MUST provide archive disks of different sizes, at least one parity disk, and at least two pool devices, and a separate SMB client for S1.
- **FR-010**: Fault injection (device failure, process kill, power-off, injected write or checksum faults, out-of-space) MUST be deterministic and triggered at named points of the procedure under test, not by timing.
- **FR-011**: The suite MUST run unattended in CI and by a single documented command locally on a machine with KVM, and every scenario MUST give the same verdict on three consecutive runs from clean state.

**S1 stacked share**

- **FR-012**: The criteria for S1 MUST cover: documented stacking setting present and absent; reading, copy-up, deletion, whiteouts, recreation after deletion; renames of files and directories of every layer origin; extended attributes and SMB file attributes; SMB client access from a separate client; files larger than 4 GiB; pool out of space; direct changes to the archive side while mounted; persistence across reboot.
- **FR-013**: S1 MUST record every mount or share setting found to be required for a PASS, so the product module can reproduce them.
- **FR-014**: S1 MUST verify that the archive side stays byte-identical across every client operation (copy-up and whiteout writes only the pool layer).

**S2 parity recovery**

- **FR-015**: The criteria for S2 MUST cover failure before a sync, failure during a sync (device failure and process kill), failure with changes on surviving disks since the last sync, sync after failure and replacement, double failure beyond parity strength, and read-only gating.
- **FR-016**: Every scenario of S2 MUST state in advance which files are expected to be recoverable and which are expected to be lost, and the verdict compares the outcome against that prediction.
- **FR-017**: S2 MUST produce a written safe recovery and resynchronisation sequence, executed in full by the test, and MUST demonstrate that read-only gating blocks every route to a sync, scrub or mover action while a disk is failed.

**S3 mover**

- **FR-018**: The criteria for S3 MUST enumerate the mover's step boundaries before the spike runs, covering at least copy, checksum, sync, verification, pool copy removal, archive deletion, second sync, second verification and whiteout cleanup, and MUST require a kill at every boundary and mid-step, each by process kill and by hard power-off.
- **FR-019**: S3 MUST check, after every interruption and after every retry, these invariants with an independent oracle: (a) every live file reads back complete and correct through the share; (b) no pool copy was removed before its archive copy was checksum-verified and parity-covered; (c) no whiteout was removed before the archive deletion it represents was synced and verified; (d) no deleted file reappears; (e) a single device failure afterwards loses no live file.
- **FR-020**: S3 MUST require that a retry converges to the uninterrupted final state, including a retry that is itself interrupted.
- **FR-021**: S3 MUST require that failure of copy, checksum, sync or verification leaves the pool copy and the whiteout in place, and the failing step reported.
- **FR-022**: S3 MUST cover concurrent modification of a file by a client during the mover run, replacement of an existing archive version, and archive out-of-space.

**S4 degraded modes**

- **FR-023**: The criteria for S4 MUST include a state table that defines, for each of healthy, pool degraded, archive degraded and both degraded, which operations are allowed (read, write, sync, scrub, mover, direct archive write) and what status and warning the system shows.
- **FR-024**: S4 MUST test the transition into each degraded state, both-degraded reached in both orders and simultaneously, and the return to healthy after repair.
- **FR-025**: S4 MUST verify that locked actions are refused by every route and that refused actions leave archive and parity unchanged, and that states and locks are re-established after reboot.
- **FR-026**: Each degraded state MUST be observable through a status indication that does not depend on a UI.

**S5 declarative rebuild**

- **FR-027**: The criteria for S5 MUST cover: duration measurement for named change classes (cold and warm), failed build, failed activation, explicit generation rollback (including after reboot), the separation of repository head and active generation, and a pure-evaluation check of the configuration location.
- **FR-028**: S5 MUST show that after a failed build or failed activation the running system equals the last known-good generation and that the failure is reported to the caller with its log.
- **FR-029**: S5 MUST show that a rollback changes the active generation without altering the repository history, and that the two are reported separately.
- **FR-030**: S5 MUST record the measured rebuild durations in its result record, in the form the criteria prescribe, and judge them against the threshold set in the criteria.

**Fallbacks**

- **FR-031**: The spike criteria MUST state the fallback for each spike, as follows, and the result records MUST apply it on FAIL:
  - **S1 FAIL**: stacking is abandoned; ADR-0002 falls back to direct archive access, read-only when degraded (the fallback already written in ADR-0002). The overlay, whiteout and mover design are dropped; how the pool is exposed in that case is decided in the ADR update.
  - **S2 FAIL** (recovery or gating does not behave as ADR-0002 assumes): the same ADR-0002 fallback applies, and the safe-sequence assumptions in the ADR are corrected before any further decision.
  - **S3 FAIL**: the mover sequence is corrected and S3 is re-run. If no sequence is found that satisfies all invariants, the stacked model is dropped and the ADR-0002 fallback applies.
  - **S4 FAIL**: the lock design is corrected and S4 is re-run. Both the stacked model and the fallback need the lockouts, so S4 has no fallback of its own: ADR-0002 stays proposed until S4 passes.
  - **S5 FAIL**: ADR-0005 has no documented fallback [NEEDS CLARIFICATION: what happens on S5 FAIL? Recommended: ADR-0005 stays proposed, the decision is amended and S5 re-run; direct file writes only if no amendment passes].

### Key Entities

- **Spike**: one of S1 to S5; has a name, frozen criteria, scenarios, and zero or more result records.
- **Acceptance scenario**: a precondition, an action and an observable expected outcome with a stable identifier; judged pass or fail automatically.
- **Criteria version**: the frozen wording of a spike's scenarios; amendments create a new version and void earlier verdicts.
- **Result record**: the outcome of one run of one spike: criteria version, environment revision, date, per-scenario outcome, evidence reference, verdict, fallback decision.
- **Verdict**: PASS or FAIL for a spike, derived only from its scenario outcomes.
- **ADR gate**: the rule linking spike verdicts to an ADR status change (proposed to accepted, or fallback adopted).
- **Environment**: the VM topology (disks, client), pinned inputs, and fault-injection points used by a run.
- **Recovery procedure**: the written, tested sequence produced by S2.
- **Degraded-state table**: the allowed-operations table per state defined by S4.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All five spikes have a recorded PASS or FAIL verdict (5 of 5), each traceable to a result record naming its criteria version and environment revision.
- **SC-002**: For 5 of 5 spikes, the criteria are committed before the first result record; for 0 spikes does a verdict stand that was obtained under criteria later amended.
- **SC-003**: 0 ADR status changes happen without a matching verdict record: ADR-0002 is accepted only with 4 of 4 PASS (S1 to S4), ADR-0005 only with S5 PASS, and each failed spike has its fallback decision recorded and applied to the ADR text.
- **SC-004**: Every acceptance scenario is automated, and 100% of scenarios return the same verdict on 3 consecutive clean runs.
- **SC-005**: S3 kills the mover at 100% of the enumerated step boundaries and mid-step points, each in both kill modes, and records the number of crash points covered; the result is zero lost live files, zero resurrected deletions and zero premature pool-copy or whiteout removals.
- **SC-006**: S2 recovers 100% of the files predicted as recoverable bit-exact in every scenario, and the set of files reported lost equals the predicted set exactly (no more, no fewer).
- **SC-007**: S4 covers all transitions in the state table (healthy to each degraded state, both-degraded in both orders and simultaneously, and repair back) and 100% of lockout attempts are refused with archive and parity unchanged.
- **SC-008**: S5 reports rebuild duration (median and worst case over at least 5 warm runs per change class, plus one cold run) and 100% of failed builds and failed activations leave the running system equal to the last known-good generation.
- **SC-009**: The full suite starts with one command, runs without manual steps on the reference environment, and completes within 2 hours on the project's reference runner.
- **SC-010**: At the end of Phase 0 the repository contains test, specification and decision artifacts only; no product code was added for the sake of the spikes.

## Out of Scope

- The backend, API, UI, and any product component of Phase 1 or later. Spikes may use throwaway test scaffolding only, which is not carried into the product.
- Docker and container management, VM management, the plugin system.
- Notifications, SMART monitoring and real-world disk health detection (failures are injected, not predicted).
- Physical hardware validation, real disk failure modes, power-loss on real devices and performance benchmarking beyond the S5 rebuild duration and the S1 large-file correctness.
- Production SMB configuration (users, permissions, tuning); only what S1 needs to prove client access is configured.
- Age or fill-level policies for selecting what the mover moves; the spikes pick the files explicitly.
- Real-time parity, alternative storage layouts beyond the documented fallback, and revisiting ADR-0001, ADR-0003 and ADR-0004.
- Choosing a project licence.

## Assumptions

- The audience is the project maintainers. Because the spikes exist to test named technologies chosen in the ADRs (a pool filesystem, a stacked union filesystem, an archive pooling layer, a parity tool, NixOS), those names appear where they are the subject under test; this is not a choice of implementation made by this spec.
- Tests run as NixOS VM tests on a host with hardware virtualisation. A KVM-capable CI runner is available; without it, the CI requirement (FR-011) cannot be met and that is escalated rather than waived.
- Virtual disks stand in for real disks. Failures, crashes and power-off are injected from the host side. Findings that depend on real hardware behaviour are out of scope and noted as residual risk in the result records.
- Operating system and tool revisions are pinned for the whole phase, so a verdict is tied to a known environment.
- The S1 large-file size (above 4 GiB) is chosen to cross common 32-bit limits; the pool and archive virtual disks are sized to allow it.
- In the pool-degraded state, sync, scrub and mover remain allowed, because the design locks them only for archive degradation; the S4 criteria confirm or refine this in the state table before the spike runs.
- Single parity (one parity disk) is the baseline configuration for S2 and S3; double failure behaviour is recorded for information in S2.
- S5 follows the system-disk layout of ADR-0004 where the VM test can reproduce it, since rollback across a reboot is meaningless without it.
- The recovery procedure produced by S2 and the mover step list used by S3 are the same artifacts the product will later implement; a change to either after Phase 0 reopens the corresponding spike.
- Phase 0 deliverables land on `main` through pull requests under the project's workflow, like all other changes.
- Phase 1 does not start until all five verdicts are recorded and each ADR is either accepted or its fallback adopted (design rule: phase n+1 starts only when phase n passes its tests).
- The 2-hour suite budget (SC-009) is an initial target; the plan may revise it with a recorded reason if large-file and crash-point coverage need more.

## Open Questions

The two decisions that could not be settled from the design are marked inline with their recommended answers: (1) the acceptable rebuild duration (S5 scenario 1), and (2) the fallback for a failed S5 (FR-031).
