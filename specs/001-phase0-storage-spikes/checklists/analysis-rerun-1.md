# Specification Analysis Report, re-run 1: 001-phase0-storage-spikes

**Date**: 2026-10-16. **Baseline**: `origin/main` at `3efa128` (PR #15 merged). This is the re-run that T081 requires after its merge. It extends [analysis-2026-10-16.md](analysis-2026-10-16.md) (35 findings, PR #8) and does not repeat it.

**Method**: A read-only subagent compared spec, plan, research, data-model, quickstart, tasks, contracts, schemas, ADRs and `spikectl` code. The maintainer's agent session then re-checked the load-bearing claims against the repository and the pinned mergerfs source. Results of that check are marked **verified**.

**Checks run**: `python -m pytest` in `nix develop ./spikes/phase0`: 270 passed. `spikectl verify`: ok. `spikectl verdict`: all `none`. Task graph parsed: 171 unique ids, 40 done, 131 open, no unknown or forward dependency, no done task depending on an open one.

**Changes since the first report**: PR #9 (clarify), #10 (align), #11 (T037a), #12 (T081), #13 (trash), #14 (T037b), #15 (FAIL rule, O18), #16 (review-target validation), all in `haexhub/nasaex`.

## Part 1: Status of the 35 earlier findings

27 resolved, 5 still open (C3, U5, G1, I9, D1), 3 changed (U2 becomes G4, U4 becomes U8, D3 becomes G5).

| ID | Status | Note |
| --- | --- | --- |
| A1, A2, A3, A4, A5 | Resolved | A2 leaves follow-ups G3 and U8 |
| C1, C2 | Resolved | tasks rule 3 and 5, T081 route |
| C3 | Open (LOW) | `research.md` is 1040 lines; no split plan |
| U1, U3, U6 | Resolved | |
| U2 | Changed | Contract now requires metadata preservation; the done primitive contradicts it (G4) |
| U4 | Changed | Verdict source is defined (newest `spike-run`); how `flaky` reaches the verdict is not (U8) |
| U5 | Open (LOW) | Corrupted-parity control not in spec, plan or data-model |
| G1 | Open (MEDIUM) | Edge cases without a check: concurrent I/O during a degraded transition, device failure during in-place edit, rename or mover sync, uncommitted change (T124 implements, T127 does not assert) |
| G2 | Resolved | FR-009 cites a field that does not exist (I19) |
| I1, I2, I3, I4, I5, I6, I7, I8, I10, I11, I12, I13, I14, I15, I16 | Resolved | I5 and I3 leave small leftovers (I24, I25) |
| I9 | Open (LOW) | Terminology drift ("hard power-off", "recovery sequence/procedure") |
| D1 | Open (LOW) | X1 non-gating repeated in ten places |
| D2 | Resolved | |
| D3 | Changed | `fault:M1:enospc` is now S3.9 only; S3.6 has no copy-fault point (G5) |

## Part 2: New findings

| ID | Severity | Summary | Recommendation |
| --- | --- | --- | --- |
| C4 | **CRITICAL** (process) | **Verified.** The constitution (merge-strategy-no-squash) requires replacing GitHub's auto-generated "Merge pull request ..." subject with a Conventional-Commits header. Merges #9, #10, #11, #12, #14, #15, #16 and `9feb1d7` ("Merge remote-tracking branch ...") carry the auto subject. tasks.md rule 5 and plan.md:104 do not carry the subject rule. History cannot be rewritten. | Add the rule to tasks rule 5 and plan.md:104. Merge with rebase-merge or a rewritten subject from now on. Record the deviation. |
| U7 | **HIGH** | **Verified in the pinned mergerfs 2.41.1 source** (`src/func.hpp:77-83,177`, `src/policy_epall.cpp:70-91`): `unlink` uses `ActionDefault`, which is policy `epall`; the action policy skips only `RO` branches, so `NC` archive branches are included. `tiered-cache.nix` sets only `category.create`. A client delete through the share after M1 also unlinks the archive copy M1 just made. Consequences: `visible_before_trash` is false, not "expected true"; M2 sees the archive copy missing and the contract stops the run as a hash mismatch (mover-steps.md:116); at M5 there is nothing to trash and FR-019 (g) "exactly one trash copy" cannot hold; the four trash crash cases (of 42) cannot fire. | Decide before T028a and T037c: set an unlink action policy such as `func.unlink=ff` in the cache pool (a required setting under FR-013, with its effect on S1.4 and S1.5 checked), or redesign. Define the vanished item whose archive copy is also gone (reported, not trashed). Measure first; do not assume `ff` works. |
| G3 | **HIGH** | **Verified.** `spikectl` has only `freeze`, `verify`, `verdict` (cli.py:15-33). `build_review_record` is reachable from Python only. No task performs the S2.7 review or commits the review sub-record. S2 can only end `pending` or `FAIL`, so ADR-0002 can never be accepted. | Add a GATE task between T080 and T143 and a documented way to build the record (verb or script). Add the step to quickstart Scenario 7. |
| U8 | **HIGH** | Undefined whether the three `--repeat 3` runs write `spike-run` records. If they do, the newest S2 run has no review and S2 falls back to `pending` after T143, blocking T145 and T146. If not, `repro_runs` points at records that are not in the repository. How a `flaky` repro reaches the verdict is undefined; `verdict` ignores repro records. | Define the record set a repeat run writes, how `flaky` feeds the verdict, and whether a repro needs a new review. |
| G4 | **HIGH** | **Verified.** The done M1 primitive copies with `head -c`/`tail -c` redirects (mover-effects.sh:38-46): a sparse source becomes fully allocated and hard links are not kept. The amended contract requires an equal hole map and shared inodes, and M2 fails on any metadata mismatch. No open task changes the primitive (T086a says `mover-effects.sh` stays unchanged). S3.1 and M2 would fail on the harness itself. | Add a task (tests first) that brings the primitive and T086 in line with "Metadata preservation", or narrow the contract and S3.1. |
| I17 | MEDIUM | quickstart.md:73-74 shows `S2 pending` with `ADR-0002 eligible: yes`; the code prints `no`. | Fix the example. |
| I18 | MEDIUM | spec.md:216 (US6.2) says S2 needs the review "before its verdict is PASS or FAIL"; contradicts FAIL-over-pending. SC-001 names only a failed automated S2.7 check, not a failed other scenario. | Reword: a review is needed only for PASS. |
| I19 | MEDIUM | FR-009 cites `environment.vms`; the schema has `vm_resources` with optional `disks`. data-model.md:156-163 drifted from the schema. | Point FR-009 at `vm_resources`, make `disks` required for S1-S4, align the data-model. |
| G5 | MEDIUM | S3.6 "fault in copy" has no named injection point (FR-010). | Map `fault:M1:enospc` to S3.6 too, or add `fault:M1:write-error`. |
| A6 | MEDIUM | The trash move happens at M5, after the last sync (M3). Recovery as of the last sync puts a trashed entry back at its archive path. The contract calls this a finding but does not say whether S3.4 passes or fails (FR-002). | State the outcome in spec and contract before T091. |
| U9 | MEDIUM | A retry never selects the deleted item, so the "next point after the one just exercised" cannot fire for the four trash cases. The "replacing item first" rule is unclear in retries after M5. | Define the retry-kill point per case; limit the rule to the first run of a case. |
| U10 | MEDIUM | T098a needs a client change to the archive copy after the delete (`vanished-archive-changed`), but no fault kind or injection point exists and the permitted-state table has no row. | Add an injection point and state row, or drop the variant. |
| I20 | MEDIUM | The design document, named source of truth, still describes the mover without idle selection, compare-and-delete, no-client-lock or trash. | Update its mover paragraph in a reviewed docs PR. |
| G6 | MEDIUM | The reference-runner free-space check (at least 100 GiB, retained in evidence) has no task or test; `record.py` checks RAM, KVM, dirty tree and lock only. | Add to T049a and its tests. |
| I21 | MEDIUM | O18 puts "fix the fsync order" first, which cannot address known limit 2 (pool file gone, next run does not select the item). Spec, contract and ADR name only durable state. T162 closes O1, O2, O9, O12-O14 but not O15-O18. | Align O18 with the contract; add O17, O18 to T162. |
| I22 | LOW | tasks.md:193 and OI-12 say the trash amendment is part of the T081 PR; it landed in PR #13. Decision dates read "2026-10-16/17" although all commits are 2026-10-16. | Fix the history wording. |
| I23 | LOW | quickstart.md:135 says O1 to O16; the plan has O18. | Say O1 to O18. |
| I24 | LOW | spec.md:377 vs :281: the Assumption says the CI clause is met by `static` plus the local record run; FR-011 says the local record run is not a CI run. | Align the Assumption with FR-011. |
| I25 | LOW | T090 budget "76 to 152 min serial" was derived from 38 cases; the matrix has 42. | Mark as an estimate or rescale. |
| C5 | LOW | **Verified.** `test_verdict.py` has 503 lines, above the 500-line rule. | Split it. |
| U11 | LOW | Nothing forces the S2 criteria to carry S2.7 with `review: true`; without the flag S2 could PASS with no human judgement. | Add a `verify` or schema rule. |
| I26 | LOW | plan.md:104, 112, 221 carry stale status text. | Refresh. |
| I27 | LOW | checklists/requirements.md still says "S3.7 stays with T081" and "Four items remain incomplete". | Re-validate after the fixes. |

## Checks that pass (verified by the subagent)

- FAIL-over-pending is consistent across spec, data-model, both schemas, T009/T037b/T072/T080/T137/T140/T141 and `verdict.py`, except I17, I18 and SC-001.
- Crash points and cases: 6+6+2+1+1+3+2 = 21 points, 42 cases, consistent across mover-steps, plan, data-model and tasks. The 10 named fault and injection points match the contract table.
- PR #14 and #16 validation in `verify` (same criteria version and hash, evidence pinned, committed after the reviewed record) and its 15 tests.
- All 34 FR and 11 SC map to at least one task; no unmapped tasks. Nine keys are only partly covered (G3, U8, I19, G5, U10, U7, U9, A6, G4, G6).

## Constitution alignment

`.spaex/constitution.md`: C4 (CRITICAL, process), C5 (LOW), C3 (LOW). Graphify-first and workflow adherence are met. No host paths or secrets. `.specify/memory/constitution.md` is still the unfilled template (OI-9 / plan O11).

## Metrics

Requirements 45 (34 FR, 11 SC); tasks 171 (40 done, 131 open); earlier findings 27 resolved, 5 open, 3 changed; new findings 23 (1 critical, 4 high, 10 medium, 8 low); open in total 28.

## Next actions

1. C4: add the merge-subject rule now; use rebase-merge or a rewritten subject.
2. Before T028a, T037c, T086a, T091: U7 (measure the unlink policy; define the vanished item without an archive copy), G4, A6, U9, U10. Reviewed docs PR via `/speckit-clarify` plus a harness task for G4.
3. Before T080 and T143: G3 and U8.
4. Docs alignment: I17, I18, I19, G5, I20, I21, G6, then the LOW items.
