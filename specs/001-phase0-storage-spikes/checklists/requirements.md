# Specification Quality Checklist: Phase 0 Storage Model Spikes

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-15
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [ ] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous (resolved 2026-10-16 in the clarification session: findings A1 to A5, I8, I10, I13, I14, I16 in analysis-2026-10-16.md; S3.7 resolved by T081, haexhub/nasaex#12, and the trash amendment, haexhub/nasaex#13)
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified
- [x] The optional experiment X1 is explicitly non-gating and cannot change any ADR status or the ADR gate (FR-034, SC-011, User Story 7)

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria (resolved 2026-10-16: FR-008, FR-009, FR-010 and the last clause of FR-034 carry a "verified by" reference, FR-014 and FR-032 no longer conflict, see G2, I10, I14; FR-019 to FR-022 resolved by T081 and the trash amendment)
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria (verified 2026-10-16 after the reference runner definition: SC-004 and the S5 threshold class were resolved (I8, A3), SC-009 now refers to the spec's "Reference runner" section, see I4 and I5)
- [x] No implementation details leak into specification

## Notes

- Validation iteration 1 of max 3. One item remains unchecked: "Written for non-technical stakeholders" is an accepted deviation (see below). The three items that failed the check of 2026-10-16 were fixed and ticked on 2026-10-16 (see the entries below); pass count 16/17.
- **Written for non-technical stakeholders (open, accepted deviation)**: the audience of a Phase 0 spec is the project maintainers deciding on ADR-0002 and ADR-0005. The spikes are about storage failure behaviour and cannot be described without naming it; the spec stays outcome-oriented but is not aimed at non-technical readers.
- **No implementation details (checked, with caveat)**: the technologies named (pool filesystem, union filesystem used as a tiered cache, parity tool with btrfs snapshots, NixOS, and for the optional experiment X1 an overlay filesystem) are the subject under test fixed by ADR-0002 and ADR-0005, not implementation choices. How the VM tests, fault injection and result records are built is left to the plan.
- **Success criteria technology-agnostic (checked, with caveat)**: success criteria SC-001 to SC-011 are phrased as counts, percentages and durations; SC-008 uses the term "generation" because it is the unit ADR-0005 reasons about.
- **[NEEDS CLARIFICATION] markers (resolved 2026-10-15)**:
  1. Acceptable rebuild duration for S5: median at most 5 minutes on a warm store for a typical change is a PASS; above 5 and up to 15 minutes is a PASS that requires asynchronous apply in the Phase 1 plan; above 15 minutes is a FAIL.
  2. Fallback if S5 fails: ADR-0005 stays proposed, the decision is amended and S5 re-run; direct file writes only if no amendment passes.
  3. S5 gates ADR-0005 only; ADR-0002 is gated by S1 to S4.
- **Re-validation after the storage baseline decision (2026-10-15)**: the maintainer decided that the documented mergerfs tiered-cache pattern is the baseline ("variant A") and that overlayfs on mergerfs is kept only as the optional, non-gating experiment X1 (research section 0 and section 7). Spec changes: S1 rewritten as the tiered-cache share (11 scenarios), S2 gains per-block predictions, the snapshot variant (scenario 9) and the clarified sync-after-failure hazard (9 scenarios), S3 loses all whiteout steps and gains the stock-mover negative control (9 scenarios), S4 and S5 are reworded (7 and 8 scenarios), X1 is added as User Story 7 (10 scenarios, not counted among the gating ones). Totals: 44 gating scenarios (11+9+9+7+8), 34 functional requirements (FR-001 to FR-034, with FR-032 to FR-034 new), 11 success criteria (SC-011 new). No new `[NEEDS CLARIFICATION]` markers; two baseline choices (snapshot tool, create policy) are plan open items with recommendations. The four items above that were unchecked before remain unchecked: this update did not verify them.
- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
- **Verification 2026-10-16** (cross-artifact analysis, `analysis-2026-10-16.md`): the three open items were checked against spec, plan, research, data-model, contracts and tasks. Result: all three FAIL and stay unchecked. "Requirements are testable and unambiguous": scenarios and checks exist for all 44 gating scenarios, but A1 to A5 (variant verdict rule, S2.7 review outcome, S5 "typical change", unquantified error and time terms, untestable state-table cells) and the conflicts I8, I10, I13, I14, I16 remain. "All functional requirements have clear acceptance criteria": 31 of 34 have an acceptance reference; FR-008, FR-009 and FR-010 have none. "Feature meets measurable outcomes": 11 of 11 success criteria are mapped to tasks, but SC-004, SC-008 and SC-009 need spec fixes. Analysis totals: 35 findings (0 critical, 3 high, 20 medium, 12 low), 45 of 45 requirements covered by tasks, no constitution violation. The accepted deviation "Written for non-technical stakeholders" is unchanged.
- **Clarification session 2026-10-16** (`spec.md`, section Clarifications): the maintainer decisions A1 to A5, I6, I8, I10, I12 to I14, I16 and G2 were applied to the spec and data model; no scenario, FR or SC was added or removed (still 44 gating scenarios: 11+9+9+7+8; 34 FR; 11 SC; 10 X1 scenarios). Re-evaluated: "Requirements are testable and unambiguous" and "All functional requirements have clear acceptance criteria" now PASS and are ticked (2 newly passing, no regression); "Feature meets measurable outcomes" stays open only for SC-009 (I4, not part of this session); "Written for non-technical stakeholders" is unchanged (accepted deviation). Pass count: 13/17 before, 15/17 after. Not decided here and still open: S3.7 and the mover contracts (T081), G1 edge-case coverage, I4/I5 (runner, CI).
- **Verification 2026-10-16, reference runner** (`spec.md`, section "Reference runner", Clarifications bullet of the same session; findings I4 and I5 of `analysis-2026-10-16.md`): the last open success-criteria item was re-checked against the spec. Each of SC-001 to SC-011 is stated as a count, percentage or duration with an observable check and maps to a task (coverage table of the analysis; SC-009 to T139, T143 and T144). SC-009 now names the reference runner by checkable properties (local record run with KVM, an 8 GiB VM memory budget, a state directory on disk with at least 100 GiB free, CI limited to the `static` job and KVM shakedown runs, no self-hosted runner), measures the 2 hours as the wall time of one record-mode run there, and treats them as an initial target revisited after the shakedown. "Feature meets measurable outcomes" is therefore ticked. SC-005 is measurable as written; its crash-point count still follows the T081 amendment (I3) and is not part of this item. "Written for non-technical stakeholders" is unchanged (accepted deviation). Pass count: 16/17.
- **Re-validation 2026-10-17** (after the review fixes for `analysis-rerun-1.md`, spec Clarifications session 2026-10-17): the ticked items were re-checked against spec, plan, data-model, contracts and tasks and stay ticked; pass count 16/17, the accepted deviation is unchanged. "Requirements are testable and unambiguous": the trash outcome of S3.7 (e) and FR-019 (g) now covers a vanished item without archive copy and the measured unlink policy (U7), S3.4 judges recovered trash content by content (A6), the retry kill point is fixed per case (U9) and the archive-change variant has a named point (U10). "All functional requirements have clear acceptance criteria": FR-009 names `environment.vm_resources` (I19). "Feature meets measurable outcomes": SC-001 states that any failed S2 scenario establishes FAIL without review (I18); how a failed reproducibility run reaches the verdict (SC-004, U8) is a proposed rule awaiting the maintainer, which does not change a success criterion. Findings still open and not affecting a checklist item: C3, C5, U5, G1, I9, D1.
