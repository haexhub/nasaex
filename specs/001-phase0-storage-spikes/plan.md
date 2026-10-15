# Implementation Plan: Phase 0 Storage Model Spikes

**Branch**: `001-phase0-storage-spikes` | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-phase0-storage-spikes/spec.md`

## Summary

Phase 0 proves, in reproducible NixOS VM tests with virtual disks, whether the two-tier storage
model (ADR-0002) and the rebuild-based configuration model (ADR-0005) hold, and turns the result
into ADR decisions. The plan delivers **test, specification and decision artifacts only** (SC-010).

Approach: one pinned flake of its own (`spikes/phase0/`) defines five NixOS VM tests
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
samba 4.23.10, Linux 6.18.56, qemu_kvm 10.2.4, xfsprogs 6.19.0, e2fsprogs 1.47.4, cifs-utils 7.5
(research section 1).

**Storage**: Files in git: criteria (YAML), frozen lock, result records (JSON) and evidence
(`.tar.xz`). VM state (qcow2 disks) is scratch and never committed.

**Testing**: NixOS test driver (`pkgs.testers.runNixOSTest`) with a project scenario recorder;
pure-Python unit tests for the oracle and `spikectl` (no VM); negative controls (known-bad mover
variants) that must be flagged as FAIL.

**Target Platform**: x86_64-linux host with KVM (`/dev/kvm` accessible); guests are NixOS VMs.
Hosts without KVM are unsupported (spec FR-011 assumption).

**Project Type**: test, specification and decision artifacts (no product component).

**Performance Goals**: full suite within 2 hours on the reference runner (SC-009, initial target);
S5 rebuild-duration thresholds as fixed in the spec (median <= 5 min PASS, <= 15 min PASS with
asynchronous-apply requirement, above FAIL).

**Constraints**: no network inside VMs; clean machine state on every run; no verdict from a cached
build; deterministic fault points only (FR-010); no committed host paths, secrets or personal data;
no product code.

**Scale/Scope**: 5 spikes; 43 acceptance scenarios (11+8+9+7+8); S3 enumerates 34 crash points
(contracts/mover-steps.md), each in 2 kill modes (68 cases); topology profiles `large` (S1) and `small` (S2-S4); one single-machine
topology for S5.

## Decisions at a glance

Rationale, alternatives and sources for each are in [research.md](research.md) (section in brackets).

| Topic | Decision |
| --- | --- |
| Pin | Dedicated flake `spikes/phase0`, nixpkgs by full commit SHA from `nixos-26.05`, `flake.lock` committed, one pin for all of Phase 0 [D1] |
| Harness | `pkgs.testers.runNixOSTest`, Python scripts, driver run outside the sandbox with `forceAccel`, fresh state per run, own scenario recorder (the driver's `subtest` aborts on error) [D2] |
| Disks | `emptyDiskImages` with stable serials; `large` profile (S1: pool 2x8 GiB, archive 6/10/4 GiB XFS/XFS/ext4, parity 12 GiB) and `small` profile (S2-S4); sparse qcow2 [D3] |
| SMB client | Second VM with `mount.cifs` plus `smbclient`; minimal smb.conf [D4] |
| Fault injection | `machine.crash()` (QEMU process termination; host-power-loss semantics unverified), in-guest `kill -9` while blocked at a hook, QEMU `device_del`/`drive_del` for device failure, filler file for out-of-space, guest-side byte flips for corruption; all at named points [D5] |
| Mover hooks | Throwaway reference mover calls blocking `spike_hook <point>`; host kills at the point; every durable effect is a point [D6] |
| Oracle | Host-side seeded fixtures give expected manifests; observed state read from share and raw layers; parity coverage proven by device-failure drill on a copied machine state; S2 predictions at block granularity; negative controls validate the oracle [D7] |
| Criteria and records | YAML in `docs/phase0/criteria/`, append-only `frozen.lock` (SHA-256), versioned amendments, JSON records plus `evidence.tar.xz` in `docs/phase0/results/<spike>/<record-id>/` [D8] |
| ADR gate | Tool never edits ADRs; records land by PR; a separate reviewed docs PR changes ADR status; `spikectl verify` checks it in CI [D9] |
| S5 | Single VM with bootloader, tmpfs root, persistent `/nix` and state disk, offline rebuild; backend-equivalent apply script supplies the restoration that `nixos-rebuild` does not [D10] |
| Degraded locks | Kernel-enforced read-only remount of archive and parity filesystems plus a status/refusal layer [D11] |
| Layout and flake | Scaffolding only under `spikes/phase0/`; decisions under `docs/phase0/`; apps `suite`, `s1..s5`, `controls`, `spikectl`; cheap `checks` without KVM [D12] |
| Entry point | `nix run ./spikes/phase0#suite -- --mode record [--repeat 3]`; `shakedown` mode never records [D13] |
| CI | `static` (no KVM), `suite` (KVM, dispatch/label), `repro` (three chained clean jobs); actions pinned by SHA; budget table in research, S3 sharded [D14] |
| Reproducibility | `--repeat 3` from clean state, per-scenario comparison, any difference is `flaky` = FAIL [D13] |

## Constitution Check

*GATE: passes before Phase 0 research; re-checked after Phase 1 design (below).*

Checked against `.spaex/constitution.md` (the Spec Kit file `.specify/memory/constitution.md` is
still an unfilled template, finding F8, so it contributes no gate). Principles that do not apply
to a Phase 0 consisting of docs, Nix, Python and shell are listed once.

| Constitution MUST / MUST NOT | Status | How the plan satisfies it |
| --- | --- | --- |
| Author changes in a dedicated repository-local worktree on a topic branch; keep the primary checkout for inspection | Pass | All work is in the worktree of branch `001-phase0-storage-spikes`. |
| All work lands on `main` through a PR; no squash merge (rebase or merge commit) | Pass, action needed | Records, criteria and ADR changes are PRs. PR #3 is already merged and the branch has unmerged commits (finding F7, open item O10); merge strategy must stay rebase or merge commit. |
| Conventional Commits | Pass | All commits of this plan and of the implementation use `<type>(<scope>): ...`. Records use `docs(phase0)`. |
| No self-references of authoring tools (agent, model, runtime) in artifacts, commit messages or trailers | Pass | No such references in any artifact or commit; `spikectl verify` also scans committed docs and records for them. |
| No secrets in git; no committed paths that assume one OS layout (home-directory style paths of any OS) | Pass | Contracts forbid host paths, user names and host names in records; the sanitiser strips them from evidence; `spikectl verify` scans committed docs and records for them. Guest paths (`/mnt/...`) are VM-internal. |
| Cross-repo references use repository + full commit SHA + path | Pass | Upstream sources in research are pinned by SHA (research section 1); nixpkgs by full SHA and lock. Living documents (btrfs docs, SnapRAID manual URL) are cited as such and cross-checked with the pinned source where possible. |
| Phasing discipline: later phases not implemented before prerequisites | Pass | Only Phase 0; no backend, API, UI or module code. The mover, lock and apply scripts are scaffolding and say so. |
| Design decisions materially affecting a Core Principle need an ADR | Pass | No Core Principle of this constitution is affected by plan decisions. The ADR-affecting outcomes (accept or fallback) are produced by the gate, not by the plan. |
| `/speckit-plan` checks the plan against the constitution; conflicts resolved by changing the plan | Pass | This section; no conflict found; no exception granted. |
| Follow the workflow `.specify/workflows/speckit/workflow.yml` | Pass | Specify, review-spec, plan done; stops at the `review-plan` gate; tasks and implement not run. |
| Add or update tests with behavior changes; run formatter, static checks and relevant tests; report what was run | Pass at implementation | `checks.oracle-unit`, `checks.formatting`, `checks.criteria`, `checks.records` and the negative controls exist for exactly this. Nothing is runnable at plan stage; this plan changes only Markdown and JSON schemas. |
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
│   └── frozen.lock                   # append-only: <file> <version> <sha256>
└── results/
    ├── S1/ ... S5/
    │   └── <record-id>/              # e.g. S3-20261022T101500Z-v1
    │       ├── record.json           # contracts/result-record.schema.json
    │       └── evidence.tar.xz       # target <= 2 MiB, sanitised
    └── repro/<record-id>/            # three-run reproducibility records (SC-004)

spikes/phase0/                        # THROWAWAY scaffolding; never imported by product code
├── flake.nix                         # own flake, own flake.lock (nixpkgs pin)
├── flake.lock
├── nix/                              # topology profiles, node definitions, mkSpike helper
├── vm-tests/                         # s1..s5 test modules and their Python scenario scripts
├── testlib/                          # scenario recorder, fixtures, oracle, faults, hook client
├── guest/                            # in-VM scaffolding: reference mover, lockd, spike_hook, apply-config
├── controls/                         # known-bad mover variants (negative controls)
├── spikectl/                         # run, freeze, verify, verdict
└── tests/                            # pure-Python unit tests (oracle, manifests, spikectl)

.github/workflows/phase0.yml          # created in the implementation phase (static, suite, repro)
```

**Structure Decision**: Everything that exists only to run the spikes is under `spikes/phase0/` with
its own flake and lock, so that deleting or archiving it after Phase 0 leaves the repository intact and
no product directory can accidentally depend on it (`spikectl verify` fails if a file outside `spikes/`
and `docs/` references `spikes/`). Everything the ADRs will cite (criteria, records, evidence) is under
`docs/phase0/`. The spaex-delivered root `flake.nix` is not modified. Product code of later phases
never lives under `spikes/`.

### Flake outputs (`spikes/phase0`, system `x86_64-linux`)

| Output | Purpose |
| --- | --- |
| `apps.suite` | The one command (SC-009, FR-011): all spikes, schedule, budget, records or scratch output, verdict matrix |
| `apps.s1` .. `apps.s5`, `apps.controls` | One spike, or only the negative controls |
| `apps.spikectl` | `freeze`, `verify`, `verdict` |
| `packages.driver-s1` .. `driver-s5`, `packages.driver-interactive-s1` .. | Cached test drivers; interactive ones for debugging |
| `checks.criteria`, `checks.records`, `checks.oracle-unit`, `checks.formatting` | Cheap checks, no KVM, run by `nix flake check` and the static CI job |
| `devShells.default` | Nix, Python with pytest/pyyaml/jsonschema, QEMU tools |

## Phase 1 design artifacts

- [data-model.md](data-model.md): the entities of the verdict model (Spike, Acceptance scenario,
  Criteria version, Result record, Verdict, ADR gate, Environment, Recovery procedure,
  Degraded-state table) with fields, validation rules and state transitions.
- [contracts/criteria.schema.json](contracts/criteria.schema.json): schema of a criteria file.
- [contracts/result-record.schema.json](contracts/result-record.schema.json): schema of a result record.
- [contracts/mover-steps.md](contracts/mover-steps.md): the nine mover steps, their durable
  effects, postconditions and the named hook points; the artifact the product mover will later
  implement (spec assumption).
- [contracts/fault-points.md](contracts/fault-points.md): the fault kinds, how each is
  triggered and what the harness guarantees about each.
- [quickstart.md](quickstart.md): run and validate guide.

## Open items (nothing blocks the review gate; each has a recommendation)

| ID | Open item | Recommendation |
| --- | --- | --- |
| O1 | The "reference runner" of SC-009 is not defined; KVM on GitHub standard runners is observed, not guaranteed, and their disk may be too small for the `large` profile (UNVERIFIED). | Define it as a runner with >= 8 vCPU, >= 32 GiB RAM, >= 100 GiB free disk, KVM, state directory on disk. Use a self-hosted or larger runner for `suite`/`repro`; keep `static` on standard runners. Decide before the first record run. |
| O2 | Spec says criteria are frozen "before the spike is run for the first time". The harness needs shakedown runs, and the S2 block-allocation model and the S1 refresh finding need observation before criteria can be written precisely. | Interpret "run" as a run that can produce a record. `shakedown` mode never writes records and is allowed before the freeze; criteria edits after shakedown are normal pre-freeze edits recorded in the criteria file's changelog. Expected values come from documentation, and a later mismatch is a FAIL, never a silent re-prediction. Maintainers confirm this reading. |
| O3 | Findings F1-F4 contradict or sharpen the spec (S1.2 removal control, S2.2/S2.4 prediction granularity, S2.5 `-E`, S1.10 upper-layer changes). The spec and ADRs were not edited. | Amend the spec before criteria are written: S1.2 controls `passthrough.max-stack-depth` at the default, `0` and `2` (expect: default and `0` stack, `2` refused) and re-judges the "required" claim; S2.2/S2.4 predictions at block granularity; S2.5 names `-E` or a partially populated replacement; S1.10 includes upper-layer removals and whiteout removal. Maintainers decide; the plan works either way. |
| O4 | Size of evidence in git. | Target <= 2 MiB per record, no Git LFS. Revisit if the first records exceed it. |
| O5 | Negative controls (about 6 known-bad variants) cost suite time and effort. | Keep: a harness that cannot fail proves nothing. The maintainers may cut them to the S3 mutants only. |
| O6 | The degraded lock mechanism is a design choice that S4 itself tests. | Start with read-only remount of archive and parity filesystems plus a status layer (D11); a different mechanism after a FAIL is an S4 re-run. |
| O7 | nixpkgs channel. | `nixos-26.05` (stable) at the committed lock; unstable is the alternative. The stable branch moving on does not affect the pin. |
| O8 | S5 harness feasibility (tmpfs root, bootloader, local writable store, offline rebuild). | First S5 task is a feasibility probe; the documented fallback records which part of ADR-0004 was reproduced. |
| O9 | Which mergerfs `passthrough.io` mode the criteria test; the spec names only `max-stack-depth`. | Baseline `passthrough.io=off` (default); one additional S1 run with `rw` and `cache.files=auto-full` for the large-file and rename scenarios, so the product module can choose with evidence. |
| O10 | PR #3 is already merged (rebase merge, head `f256c54`); the branch contains the rebased clarification commit and the remaining plan commit. | Keep PR #4 as the delivery vehicle for these commits; no rebase/cherry-pick or second PR is needed. |
| O11 | `.specify/memory/constitution.md` is an unfilled template (finding F8). | Either fill it (`/speckit-constitution`) or state that the spaex constitution is the only gate. |
| O12 | SMB client details (which commands expose DOS attributes and xattrs, smb.conf keys) are UNVERIFIED. | Settled in the S1 criteria task through a shakedown, recorded as required settings (FR-013). |
| O13 | Third-party GitHub Actions need full-SHA pins. | Resolve at implementation. |
| O14 | Fidelity of power-off: device-level volatile write caches are not emulated. | Accepted as residual risk, stated in every record. Optional hardening later: replay-based crash testing (for example `dm-log-writes`) as a separate, later spike. |

## Complexity Tracking

No constitution violation; nothing to justify.

## Post-design Constitution re-check

After `data-model.md`, `contracts/` and `quickstart.md`: no new principle is engaged. The record schema
excludes host paths, user names and host names by construction (fields hold identifiers and hashes);
the sanitiser and verifier enforce it for free text. The plan adds no abstraction beyond `spikectl`'s
four verbs, and the mover and lock scripts are explicitly scaffolding. **PASS.**
