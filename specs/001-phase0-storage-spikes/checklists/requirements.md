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
- [ ] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified
- [x] The optional experiment X1 is explicitly non-gating and cannot change any ADR status or the ADR gate (FR-034, SC-011, User Story 7)

## Feature Readiness

- [ ] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [ ] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Validation iteration 1 of max 3. Four items remain unchecked: "Written for non-technical stakeholders" is an accepted deviation (see below); "Requirements are testable and unambiguous", "All functional requirements have clear acceptance criteria" and "Feature meets measurable outcomes" have not been verified yet and are to be checked before implementation.
- **Written for non-technical stakeholders (open, accepted deviation)**: the audience of a Phase 0 spec is the project maintainers deciding on ADR-0002 and ADR-0005. The spikes are about storage failure behaviour and cannot be described without naming it; the spec stays outcome-oriented but is not aimed at non-technical readers.
- **No implementation details (checked, with caveat)**: the technologies named (pool filesystem, union filesystem used as a tiered cache, parity tool with btrfs snapshots, NixOS, and for the optional experiment X1 an overlay filesystem) are the subject under test fixed by ADR-0002 and ADR-0005, not implementation choices. How the VM tests, fault injection and result records are built is left to the plan.
- **Success criteria technology-agnostic (checked, with caveat)**: success criteria SC-001 to SC-011 are phrased as counts, percentages and durations; SC-008 uses the term "generation" because it is the unit ADR-0005 reasons about.
- **[NEEDS CLARIFICATION] markers (resolved 2026-10-15)**:
  1. Acceptable rebuild duration for S5: median at most 5 minutes on a warm store for a typical change is a PASS; above 5 and up to 15 minutes is a PASS that requires asynchronous apply in the Phase 1 plan; above 15 minutes is a FAIL.
  2. Fallback if S5 fails: ADR-0005 stays proposed, the decision is amended and S5 re-run; direct file writes only if no amendment passes.
  3. S5 gates ADR-0005 only; ADR-0002 is gated by S1 to S4.
- **Re-validation after the storage baseline decision (2026-10-15)**: the maintainer decided that the documented mergerfs tiered-cache pattern is the baseline ("variant A") and that overlayfs on mergerfs is kept only as the optional, non-gating experiment X1 (research section 0 and section 7). Spec changes: S1 rewritten as the tiered-cache share (11 scenarios), S2 gains per-block predictions, the snapshot variant (scenario 9) and the clarified sync-after-failure hazard (9 scenarios), S3 loses all whiteout steps and gains the stock-mover negative control (9 scenarios), S4 and S5 are reworded (7 and 8 scenarios), X1 is added as User Story 7 (10 scenarios, not counted among the gating ones). Totals: 44 gating scenarios (11+9+9+7+8), 34 functional requirements (FR-001 to FR-034, with FR-032 to FR-034 new), 11 success criteria (SC-011 new). No new `[NEEDS CLARIFICATION]` markers; two baseline choices (snapshot tool, create policy) are plan open items with recommendations. The four items above that were unchecked before remain unchecked: this update did not verify them.
- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
