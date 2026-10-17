# Implementation Plan: Phase 0 Storage Model Spikes

**Branch**: `001-phase0-storage-spikes` | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-phase0-storage-spikes/spec.md`

## Summary

Phase 0 proves, in reproducible NixOS VM tests with virtual disks, whether the two-tier storage
model (ADR-0002) and the rebuild-based configuration model (ADR-0005) hold, and turns the result
into ADR decisions. The plan delivers **test, specification and decision artifacts only** (SC-010).

The storage baseline (variant A, decided 2026-10-15, [research.md](research.md) section 7) is the
tiered-cache pattern documented by mergerfs: a btrfs RAID1 pool, btrfs archive disks with SnapRAID
parity and a snapshot-supported sync and fix, and two mergerfs pools (base pool and cache pool) as
the share. The former overlayfs stack is only the optional, non-gating experiment **X1**.

Approach: one pinned flake of its own (`spikes/phase0/`) defines five gating NixOS VM tests plus the
optional X1 test
(`runNixOSTest`, Python scripts) that are built as cached derivations but **executed outside the
sandbox** by a small runner, so every run is fresh and every FAIL keeps its evidence. Faults are
injected from the host through the QEMU monitor and the test driver's `crash()`, always at named
points (mover step hooks, sync progress). An independent host-side oracle derives expected state
from seeded fixtures and proves parity coverage by actually failing a device and recovering,
never by asking the tools under test. Acceptance criteria are YAML files in `docs/phase0/criteria/`,
frozen by an append-only hash lock; result records and compressed evidence live in
`docs/phase0/results/`. A checker (`spikectl verify`) makes "criteria first", "amendment voids the
verdict" and "ADR status equals verdict" mechanical. Details and alternatives: [research.md](research.md).

## Technical Context

**Language/Version**: Nix flakes (Nix 2.34.8 at the pin) for the harness; Python 3 from the pin
(test scripts run inside the NixOS test driver; oracle; `spikectl`); POSIX shell for the in-VM
throwaway scripts (reference mover, lock/status, apply script). No Go, no TypeScript.

**Primary Dependencies**: nixpkgs `nixos-26.05` at full revision `a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf`
(candidate, fixed by the committed `flake.lock`): mergerfs 2.41.1, snapraid 14.4, btrfs-progs 6.19.1,
snapper 0.13.1, samba 4.23.10, Linux 6.18.56, qemu_kvm 10.2.4, e2fsprogs 1.47.4, cifs-utils 7.5
(research section 1). The snapshot tooling `snapraid-btrfs` is not in nixpkgs and is fetched by
pinned upstream revision (research F12, open item O15).

**Storage**: Files in git: criteria (YAML), frozen lock, result records (JSON) and evidence
(`.tar.xz`). VM state (qcow2 disks) is scratch and never committed.

**Testing**: NixOS test driver (`pkgs.testers.runNixOSTest`) with a project scenario recorder;
pure-Python unit tests for the oracle and `spikectl` (no VM); negative controls (known-bad mover
variants) that must be flagged as FAIL.

**Target Platform**: x86_64-linux host with KVM (`/dev/kvm` accessible); guests are NixOS VMs.
Hosts without KVM are unsupported (spec FR-011 assumption).

**Project Type**: test, specification and decision artifacts (no product component).

**Performance Goals**: full suite within 2 hours on the reference runner (SC-009, initial target; the
reference runner is defined in the spec, section "Reference runner"); S5 rebuild-duration thresholds
apply to each named change class separately and the worst class median decides: if every warm-store
median is <= 5 min the result is PASS, if any median is > 5 min and none is > 15 min the result is PASS
with an asynchronous-apply requirement, and if any median is > 15 min the result is FAIL.

**Constraints**: no network inside VMs; clean machine state on every run; no verdict from a cached
build; deterministic fault points only (FR-010); no committed host paths, secrets or personal data;
no product code.

**Scale/Scope**: 5 gating spikes and the optional X1; 44 gating acceptance scenarios (11+9+9+7+8)
plus 10 optional X1 scenarios; S3 enumerates 21 crash points (6 boundaries and 15 mid-step points, listed in
contracts/mover-steps.md), each in 2 kill modes (42 cases), plus 10 named fault and injection points that are not
crash points; topology profiles `large` (S1) and `small` (S2-S4), one single-machine
topology for S5, and the `x1` profile (the `large` disks with the overlay stack) for X1.

## Decisions at a glance

Rationale, alternatives and sources for each are in [research.md](research.md) (section in brackets).

| Topic | Decision |
| --- | --- |
| Pin | Dedicated flake `spikes/phase0`, nixpkgs by full commit SHA from `nixos-26.05`, `flake.lock` committed, one pin for all of Phase 0 [D1] |
| Harness | `pkgs.testers.runNixOSTest`, Python scripts, driver run outside the sandbox with `forceAccel`, fresh state per run, own scenario recorder (the driver's `subtest` aborts on error) [D2] |
| Baseline | Variant A: btrfs RAID1 pool, btrfs archive disks with SnapRAID, mergerfs base pool and cache pool (pool branch first, archive branches no-create, `moveonenospc` off), snapshot-supported sync and fix; overlayfs only as optional X1 [section 7] |
| Disks | `emptyDiskImages` with stable serials; `large` profile (S1: pool 2x8 GiB, archive 6/10/4 GiB btrfs, parity 12 GiB ext4) and `small` profile (S2-S4); sparse qcow2 [D3] |
| SMB client | Second VM with `mount.cifs` plus `smbclient`; minimal smb.conf [D4] |
| Fault injection | `machine.crash()` (QEMU process termination; host-power-loss semantics unverified), in-guest `kill -9` while blocked at a hook, QEMU `device_del`/`drive_del` for device failure, filler file for out-of-space, guest-side byte flips for corruption; all at named points [D5] |
| Mover hooks | Throwaway reference mover (five steps, 21 points; idle-file selection, no lock that clients must honour, compare-and-delete at M5, and a trash move at M5 for a selected pool file that a client deleted: the archive copy goes into a trash directory beside the branch directory on the same archive disk, expected to be hidden from the pool view, UNVERIFIED) calls blocking `spike_hook <point>`; host kills at the point or injects a client write or delete; every durable effect is a point; the stock `rsync --remove-source-files` mover is a mandatory negative control [D6, D7] |
| Oracle | Host-side seeded fixtures give expected manifests; observed state read from share and raw layers; parity coverage proven by device-failure drill on a copied machine state; S2 predictions at block granularity; negative controls validate the oracle [D7] |
| Criteria and records | YAML in `docs/phase0/criteria/`, append-only `frozen.lock` (SHA-256), versioned amendments, JSON records plus `evidence.tar.xz` in `docs/phase0/results/<spike>/<record-id>/` [D8] |
| ADR gate | Tool never edits ADRs; records land by PR; a separate reviewed docs PR changes ADR status; `spikectl verify` checks it in CI; X1 is never a gate input [D9] |
| S5 | Single VM with bootloader, tmpfs root, persistent `/nix` and state disk, offline rebuild; backend-equivalent apply script supplies the restoration that `nixos-rebuild` does not [D10] |
| Degraded locks | Kernel-enforced read-only remount of archive and parity filesystems plus a status/refusal layer [D11] |
| Layout and flake | Scaffolding only under `spikes/phase0/`; decisions under `docs/phase0/`; apps `suite`, `s1..s5`, `x1` (optional), `controls`, `spikectl`; cheap `checks` without KVM [D12] |
| Entry point | `nix run ./spikes/phase0#suite -- --mode record [--repeat 3] [--include-x1]`; `shakedown` mode never records [D13] |
| CI | `static` (no KVM), `suite` (KVM runner where one exists, dispatch/label, shakedown by default), `repro` (optional, three chained clean jobs); record runs are local on the reference runner (spec, "Reference runner"); actions pinned by SHA; budget table in research (adjusted 2026-10-15 for an S3 matrix of 38 cases instead of 68; the matrix is now 42 cases, which the shakedown re-measures), S3 sharded [D14] |
| Reproducibility | `--repeat 3` from clean state, per-scenario comparison, any difference is `flaky` = FAIL [D13] |

## Constitution Check

*GATE: passes before Phase 0 research; re-checked after Phase 1 design (below).*

Checked against `.spaex/constitution.md` (the Spec Kit file `.specify/memory/constitution.md` is
still an unfilled template, finding F8, so it contributes no gate). Principles that do not apply
to a Phase 0 consisting of docs, Nix, Python and shell are listed once.

| Constitution MUST / MUST NOT | Status | How the plan satisfies it |
| --- | --- | --- |
| Author changes in a dedicated repository-local worktree on a topic branch; keep the primary checkout for inspection | Pass | All work is in the worktree of branch `001-phase0-storage-spikes`. |
| All work lands on `main` through a PR; no squash merge (rebase or merge commit); a merge commit replaces GitHub's auto-generated "Merge pull request ..." subject with a Conventional-Commits header | Pass, with a recorded deviation | Records, criteria and ADR changes are PRs, merged by rebase (preferred) or by a merge commit whose subject is a Conventional-Commits header (tasks rule 5). Deviation: the merge commits of PRs #1, #4, #6, #7, #9 to #12 and #14 to #16 and the merge `9feb1d7` carry auto-generated subjects (finding C4 of `checklists/analysis-rerun-1.md`); history is not rewritten and the rule applies from now on. |
| Conventional Commits | Pass | All commits of this plan and of the implementation use `<type>(<scope>): ...`. Records use `docs(phase0)`. |
| No self-references of authoring tools (agent, model, runtime) in artifacts, commit messages or trailers | Pass | No such references in any artifact or commit; `spikectl verify` also scans committed docs and records for them. |
| No secrets in git; no committed paths that assume one OS layout (home-directory style paths of any OS) | Pass | Contracts forbid host paths, user names and host names in records; the sanitiser strips them from evidence; `spikectl verify` scans committed docs and records for them. Guest paths (`/mnt/...`) are VM-internal. |
| Cross-repo references use repository + full commit SHA + path | Pass | Upstream sources in research are pinned by SHA (research section 1); nixpkgs by full SHA and lock. Living documents (btrfs docs, SnapRAID manual URL) are cited as such and cross-checked with the pinned source where possible. |
| Phasing discipline: later phases not implemented before prerequisites | Pass | Only Phase 0; no backend, API, UI or module code. The mover, lock and apply scripts are scaffolding and say so. |
| Design decisions materially affecting a Core Principle need an ADR | Pass | The storage baseline and the client-delete trash decision are recorded in ADR-0002. The ADR-affecting outcomes (accept or fallback) are produced by the gate, not by the plan. |
| `/speckit-plan` checks the plan against the constitution; conflicts resolved by changing the plan | Pass | This section; no conflict found; no exception granted. |
| Follow the workflow `.specify/workflows/speckit/workflow.yml` | Pass | Specify, review-spec, plan done; stops at the `review-plan` gate; tasks and implement not run. The baseline change is a review-fix response on the open PR #4, for which freehand edits of the documents are permitted; no tasks were generated and no code was written. |
| Add or update tests with behavior changes; run formatter, static checks and relevant tests; report what was run | Pass at implementation | This contract-only amendment has no runtime implementation to test. The new oracle tests, concurrent-delete probe and S3 checks are scheduled before the harness tasks that rely on them (`T018a`, `T019a`, `T037c`, `T098a`); `spikectl verify` is the runnable check for this docs change. |
| Test code in files separate from production code | Pass | There is no production code. Inside `spikes/phase0/`, test scripts, oracle, fixtures and in-VM scaffolding are separate files. |
| Hand-maintained file clearly over 500 LoC must be split or justified | Pass at implementation | Layout splits per spike and per concern; implementation tasks must keep files under the bound. `spec.md` and `research.md` are documents. |
| Graphify-first authoring before new named functions, classes, modules, CLI commands | Applies at implementation | The plan authors no code. Every implementation task that introduces a named function, module or CLI command (`spikectl` subcommands, `spikelib` modules) must first consult `graphify` and record the candidates evaluated. The current graph is docs-only, so the expected outcome is "no candidate". |
| Ponytail: minimum code, leave one runnable check, no speculative abstraction | Pass | `spikectl` is limited to run/freeze/verify/verdict; negative controls limited to a handful of mutants; the harness has no plugin or config layer. Open item O5 lets the maintainers cut the negative controls if they judge them excessive. |
| Go, JavaScript/TypeScript guidelines | Not applicable | No Go or TS code. If `spikectl` is ever rewritten in Go, the Go MUSTs apply from then on. |
| Self-modifying instructions review-gated; external sources opt-in per project | Not applicable / Pass | No skill, instruction or permission file is changed. No external harness content is adopted. |
| Constitution amendment rules | Not applicable | No amendment proposed. |

**Result before research: PASS. Result after design (re-check below): PASS.** No violation, so
the Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-phase0-storage-spikes/
├── spec.md              # approved input, not edited by the plan
├── plan.md              # this file
├── research.md          # Phase 0 output: decisions, rationale, alternatives, sources, findings
├── data-model.md        # Phase 1 output: entities of the verdict model
├── quickstart.md        # Phase 1 output: how to run and validate end to end
├── contracts/           # Phase 1 output
│   ├── criteria.schema.json
│   ├── result-record.schema.json
│   ├── mover-steps.md
│   └── fault-points.md
└── tasks.md             # Phase 2 output (/speckit-tasks, NOT created here)
```

### Source Code and decision artifacts (repository root)

```text
docs/phase0/                          # decision artifacts: outlive the scaffolding
├── criteria/
│   ├── S1.yaml ... S5.yaml           # version 1; amendments are S<n>.v2.yaml, ...
│   ├── X1.yaml                       # optional, non-gating; exists only if X1 is run
│   └── frozen.lock                   # append-only: <file> <version> <sha256>
└── results/
    ├── S1/ ... S5/, X1/ (optional)
    │   └── <record-id>/              # e.g. S3-20261022T101500Z-v1
    │       ├── record.json           # contracts/result-record.schema.json
    │       └── evidence.tar.xz       # target <= 2 MiB, sanitised
    └── repro/<record-id>/            # three-run reproducibility records (SC-004)

spikes/phase0/                        # THROWAWAY scaffolding; never imported by product code
├── flake.nix                         # own flake, own flake.lock (nixpkgs pin)
├── flake.lock
├── nix/                              # topology profiles, node definitions, mkSpike helper
├── vm-tests/                         # s1..s5 and x1 test modules and their Python scenario scripts
├── testlib/                          # scenario recorder, fixtures, oracle, faults, hook client
├── guest/                            # in-VM scaffolding: reference mover, snapshot-supported sync/fix wrapper, lockd, spike_hook, apply-config
├── controls/                         # known-bad mover variants (negative controls), including the stock rsync mover
├── spikectl/                         # run, freeze, verify, verdict
└── tests/                            # pure-Python unit tests (oracle, manifests, spikectl)

.github/workflows/phase0.yml          # created in the implementation phase (static, suite, repro)
```

**Structure Decision**: Everything that exists only to run the spikes is under `spikes/phase0/` with
its own flake and lock, so that deleting or archiving it after Phase 0 leaves the repository intact and
no product directory can accidentally depend on it (`spikectl verify` fails if a file outside `spikes/`, `docs/`, `specs/`, `.specify/`, `.claude/`,
`.agents/`, `.spaex/` and `.github/workflows/phase0.yml` references `spikes/`; the last five are the
spec tooling and the CI workflow, and the feature's own documents live in `specs/`). Everything the ADRs will cite (criteria, records, evidence) is under
`docs/phase0/`. The spaex-delivered root `flake.nix` is not modified. Product code of later phases
never lives under `spikes/`.

### Flake outputs (`spikes/phase0`, system `x86_64-linux`)

| Output | Purpose |
| --- | --- |
| `apps.suite` | The one command (SC-009, FR-011): all spikes, schedule, budget, records or scratch output, verdict matrix |
| `apps.s1` .. `apps.s5`, `apps.controls` | One spike, or only the negative controls |
| `apps.x1` | The optional, non-gating overlayfs experiment (own profile `x1`); not part of `apps.suite` unless `--include-x1` |
| `apps.spikectl` | `freeze`, `verify`, `verdict` |
| `packages.driver-s1` .. `driver-s5`, `packages.driver-x1`, `packages.driver-interactive-s1` .. | Cached test drivers; interactive ones for debugging |
| `checks.criteria`, `checks.records`, `checks.oracle-unit`, `checks.formatting` | Cheap checks, no KVM, run by `nix flake check` and the static CI job |
| `devShells.default` | Nix, Python with pytest/pyyaml/jsonschema, QEMU tools |

## Phase 1 design artifacts

- [data-model.md](data-model.md): the entities of the verdict model (Spike, Acceptance scenario,
  Criteria version, Result record, Verdict, ADR gate, Environment, Recovery procedure,
  Degraded-state table) with fields, validation rules and state transitions.
- [contracts/criteria.schema.json](contracts/criteria.schema.json): schema of a criteria file.
- [contracts/result-record.schema.json](contracts/result-record.schema.json): schema of a result record.
- [contracts/mover-steps.md](contracts/mover-steps.md): the five mover steps (21 crash points,
  42 cases, enumerated in the contract), selection, copy-time identity, the trash handling of client-deleted files and metadata preservation, their durable
  effects, postconditions and the named hook, fault and injection points; the artifact the
  product mover will later implement (spec assumption).
- [contracts/fault-points.md](contracts/fault-points.md): the fault kinds, how each is
  triggered and what the harness guarantees about each.
- [quickstart.md](quickstart.md): run and validate guide.

## Open items (nothing blocks the review gate; each has a recommendation)

| ID | Open item | Recommendation |
| --- | --- | --- |
| O1 | The "reference runner" of SC-009 was not defined; KVM on GitHub standard runners is observed, not guaranteed, and their disk may be too small for the `large` profile (UNVERIFIED). | **Decided 2026-10-15 and defined in the spec (section "Reference runner", Clarifications of 2026-10-16); not repeated here.** Record runs are local on the maintainer's workstation within an 8 GiB VM budget; CI keeps `static` and KVM shakedown runs; no self-hosted runner is registered. The 2-hour target is revisited after the shakedown. |
| O2 | Spec says criteria are frozen "before the spike is run for the first time". The harness needs shakedown runs, and the S2 block-allocation model and the S1 view-consistency finding (scenario 10) need observation before criteria can be written precisely. | Interpret "run" as a run that can produce a record. `shakedown` mode never writes records and is allowed before the freeze; criteria edits after shakedown are normal pre-freeze edits recorded in the criteria file's changelog. Expected values come from documentation, and a later mismatch is a FAIL, never a silent re-prediction. Maintainers confirm this reading. **Confirmed 2026-10-15.** |
| O3 | Findings F1-F4 contradicted or sharpened the first spec. **Resolved by the maintainer decision of 2026-10-15**: the tiered-cache pattern is the baseline; F1, F4 and F6 apply to experiment X1 only; F2 (per-block predictions) and F3 (how the sync-after-failure hazard is reached) are built into S2; F5 into S5. Spec, design and ADR-0002 were edited accordingly. | Nothing further, except that the S2 block-allocation model stays UNVERIFIED until shakedown (O2). |
| O4 | Size of evidence in git. | Target <= 2 MiB per record, no Git LFS. Revisit if the first records exceed it. |
| O5 | Negative controls (about 6 known-bad variants) cost suite time and effort. | Keep: a harness that cannot fail proves nothing. The stock `rsync --remove-source-files` control is mandatory (spec FR-032, S3 scenario 5); the maintainers may cut the other variants to the S3 mutants only. |
| O6 | The degraded lock mechanism is a design choice that S4 itself tests. | Start with read-only remount of archive and parity filesystems plus a status layer (D11); a different mechanism after a FAIL is an S4 re-run. |
| O7 | nixpkgs channel. | `nixos-26.05` (stable) at the committed lock; unstable is the alternative. The stable branch moving on does not affect the pin. |
| O8 | S5 harness feasibility (tmpfs root, bootloader, local writable store, offline rebuild). | First S5 task is a feasibility probe; the documented fallback records which part of ADR-0004 was reproduced. |
| O9 | Which mergerfs `passthrough.io` and cache settings the S1 criteria test; the spec names only the create policy, branch modes and out-of-space behaviour. | Baseline `passthrough.io=off` and `cache.files=off` (defaults); informational variants with `passthrough.io=rw` and `cache.files=auto-full` for the large-file and rename scenarios, so the product module can choose with evidence. Only the baseline gates S1; the non-baseline variants are informational (recorded with `gating: false`, never changing a scenario outcome). X1 additionally tests the FUSE stacking depth at its default, `0` and `2`. |
| O10 | PR #3 is already merged (rebase merge, head `f256c54`); the branch contains the rebased clarification commit and the remaining plan commit. | Keep PR #4 as the delivery vehicle for these commits; no rebase/cherry-pick or second PR is needed. |
| O11 | `.specify/memory/constitution.md` is an unfilled template (finding F8). | Either fill it (`/speckit-constitution`) or state that the spaex constitution is the only gate. |
| O12 | SMB client details (which commands expose DOS attributes and xattrs, smb.conf keys) are UNVERIFIED. | Settled in the S1 criteria task through a shakedown, recorded as required settings (FR-013). |
| O13 | Third-party GitHub Actions need full-SHA pins. | Resolve at implementation. |
| O14 | Fidelity of power-off: device-level volatile write caches are not emulated. | Accepted as residual risk, stated in every record. Optional hardening later: replay-based crash testing (for example `dm-log-writes`) as a separate, later spike. |
| O15 | Snapshot tooling for the archive: `snapraid-btrfs` (with `snapper`, external, works with SnapRAID 14.x, not in nixpkgs at the pin), `btrfssnapraid` (external, last push 2023-12-13), or the native `snapshot` option of SnapRAID 15.0 (release candidate `v15.0rc2`, not at the pin). All UNVERIFIED in practice (research 7.3, F12). | Default: `snapraid-btrfs` with `snapper` at the pinned SnapRAID 14.x, fetched by pinned upstream revision (flake input) and wrapped by the harness's sync/fix helper. Re-judge the native v15 option after v15 is released; S2 scenario 9 measures whichever is selected. The choice is a plan decision, not a spec change; a switch before the S2 criteria are frozen is a normal pre-freeze edit. **Decided 2026-10-15: the default is adopted.** |
| O16 | Create policy and cache pool settings: `ff` or `lus` (mergerfs names `ff`, `lus` and `lfs` for a cache pool), whether archive branches are `NC`, `moveonenospc`, `minfreespace` (research F10, 7.2). | Default: `ff`, archive branches `NC`, `moveonenospc=false`, explicit low `minfreespace`. S1 scenario 2 gates on `ff`, and `lus` is an informational variant (`gating: false`); S1 scenarios 2 and 9 record what is required (FR-013). With only one create-eligible branch the two policies behave the same, so `lus` only matters if `NC` is dropped. **Decided 2026-10-15: the default is adopted.** |
| O17 | Where the mover puts the archive copy of a pool file that a client deleted after selection (maintainer decision 2026-10-16/17): the stale archive copy would make the file reappear through the share, and deleting it would lose a file that vanished through a device error. | Trash directory on the same archive disk, beside the branch directory inside the data subvolume, so that it is outside every pool branch (expected hidden from both pool views, UNVERIFIED) and a rename into it stays in one subvolume (a rename across btrfs subvolumes is not expected to work, UNVERIFIED). S1 scenario 1 proves the exclusion, S3 scenario 7 (e) the move; the retention period is an S3 criteria parameter and purging is out of Phase 0. Known limits (a visible window until the trash move, a crash before the move is durable) are recorded as S3 findings, see the mover contract. If the exclusion or the rename does not hold, the layout is corrected before the S1 and S3 criteria freeze. |
| O18 | Whether durable state across a crash is needed for the trash handling of the mover (see O17; maintainer rule: measure first, add nothing to the contract beforehand). | Decided by measurement in S3, not beforehand. If S3 shows the vanished-path case losing the hidden state, the order of remedies is (1) fix the fsync order, (2) store the copy-time identity as an extended attribute on the archive copy, (3) a journal or tombstone through a reviewed contract amendment. Nothing is added to the mover contract before the measurement. |

## Complexity Tracking

No constitution violation; nothing to justify.

## Post-design Constitution re-check

After `data-model.md`, `contracts/` and `quickstart.md`, and again after the baseline change of
2026-10-15 and the client-delete trash amendment: the new data-safety decision is recorded in
ADR-0002. The record schema
excludes host paths, user names and host names by construction (fields hold identifiers and hashes);
the sanitiser and verifier enforce it for free text. The plan adds no abstraction beyond `spikectl`'s
four verbs, and the mover and lock scripts are explicitly scaffolding. **PASS.**
