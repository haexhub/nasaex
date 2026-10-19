# Phase 0 CI: static checks, VM smoke and a coverage ratchet

Date: 2026-10-19. Status: agreed with the maintainer in a design session; implementation follows
through a spec PR (Clarifications and tasks) and a code PR. Scope: the Phase 0 spikes
(`spikes/phase0/`); no product code exists yet.

## Why

`nix flake check ./spikes/phase0` already runs every unit test, formatting, lint, shellcheck, the
criteria and record schema checks and two evaluation checks, but only when someone runs it. There
is no workflow under `.github/workflows/`. Defects that show only inside the VMs (the EROFS refusal
window of S1.9, the boot race of the two-device pool) were found by local shakedown runs on the
maintainer's workstation alone. The CI was planned for Phase 8 (task T142); it is pulled forward so
that every PR proves it breaks nothing.

## Workflow `.github/workflows/phase0.yml`

Triggers: pull requests on `opened`, `synchronize`, `reopened` and `labeled` (so adding
`ci:full-s1` starts `vm-full`), every push to `main`, and manual dispatch with an explicit `base`
input.

| Job | Runs | Content |
|---|---|---|
| `changes` | always | `git diff` against the event-specific base decides whether `spikes/`, `specs/001-phase0-storage-spikes/`, `docs/phase0/` or `docs/adr/` changed: the pull request base SHA for pull requests, the push event's `before` SHA for pushes, and the explicit `base` input for manual dispatch |
| `static` | always; required | install Nix; `nix flake check ./spikes/phase0` (unit tests with coverage, lint, formatting, schema and lock checks, evaluation checks); `spikectl verify --base origin/main` with full git history (append-only lock, criteria before record) |
| `vm-smoke` | when `spikes/` changed; required | enable and prove KVM; S1 shakedown of a fast scenario subset |
| `vm-full` | PR label `ci:full-s1` or manual dispatch | the full S1 shakedown; uploads `junit.xml` and the scratch output as an artifact; never writes a record |

- Path decisions are job conditions (`if`), never the workflow `paths` filter: a required check
  that a `paths` filter never starts stays pending and blocks the merge; a job skipped by its
  condition reports as skipped.
- A runner without a usable `/dev/kvm` fails `vm-smoke` loudly instead of skipping it, so no
  green check ever stands for a VM run that did not happen. If GitHub withdraws KVM, the
  maintainer decides whether the check becomes optional for a while.
- Every third-party action is pinned by full commit SHA (plan open item O13).
- Record mode never runs in CI (spec, Reference runner).

## Smoke subset

S1 scenarios that need little disk and time: candidates S1.1, S1.3 to S1.7 and S1.10. Left out:
S1.2 and S1.9 (fill the pool), S1.8 (files above 4 GiB), S1.11 (20 reboots, about 11 minutes).
The final subset follows two measurements before it is fixed: the disk the VM images use at their
peak in a full run (the runner has 14 GB SSD for public repositories), and the job time on the
runner. `spikectl run --only` accepts a single scenario id today and is extended to a list.

## Coverage ratchet

- `pytest-cov` joins the Python environment of the flake; the `oracle-unit` check of
  `nix flake check` measures line and branch coverage of `spikectl/`, `testlib/` and
  `vm-tests/s1/`. `vm-tests/probes/` is excluded in the configuration (finished measurement
  tools whose results are committed); no `pragma: no cover` in code.
- The floor is `fail_under` in `pyproject.toml` (coverage.py's own setting). Coverage below it
  fails `nix flake check`, locally and in `static`. A PR that raises coverage raises the floor by
  hand; lowering it is a visible diff.
- After the first measurement, the uncovered lines of decision logic get tests first: verdict,
  verify, record and freeze in `spikectl`; recorder, manifest, oracle and faults in `testlib`;
  the judging functions of the S1 checks. Code that only drives the VMs is covered by `vm-smoke`,
  not by unit tests against mocked machines. No chase for 100 %.

## Required checks

After the workflow is on `main` and has run green once, `static` and `vm-smoke` become required
status checks of `main` through the GitHub API, after the maintainer confirms; whether they also
bind administrators is asked then (recommended: yes).

## Proving the CI

Green alone proves nothing. In the code PR, throwaway commits (removed before merge) show that
each required check turns red: a failing unit test (`static`), a removed test below the coverage
floor (`static`), a wrong expectation in a smoke scenario (`vm-smoke`). The links of the red runs
go into the PR description. `vm-smoke` logs `/dev/kvm` and the run time of every job.

## Spec and tasks

- Spec Clarifications: CI is pulled forward; `vm-smoke` on hosted runners is required and proves
  KVM in every run instead of assuming it (amends "KVM and disk of hosted runners are not
  assumed"); record runs stay local.
- tasks.md: new Foundation tasks for `static` and `vm-smoke`, `--only` with several ids, the
  coverage ratchet with its gap tests, and the required checks; T142 keeps the `repro` job.

## Open until measured

- KVM on the standard Linux runner: the GitHub documentation names hardware acceleration for
  Android SDK tools on Linux runners, which needs KVM (Likely); only a job proves it.
- Whether `vm-full` fits the runner. Measured on 2026-10-19: in the S1 record run the VM images
  used at least 20252 MiB at their peak (sampled every 15 s; 4141 MiB after the setup), above the
  14 GB SSD that GitHub documents for the runner; the free disk of a real runner is not known.
- The job times on the runner, including the Nix build of the VM closure.
