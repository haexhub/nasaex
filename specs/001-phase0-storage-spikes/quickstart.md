# Quickstart: run and validate Phase 0

**Feature**: `001-phase0-storage-spikes` | **Plan**: [plan.md](plan.md)

This is a validation guide for the finished Phase 0 harness. Nothing here exists yet; the commands become
real in the implementation phase. Contracts: [criteria schema](contracts/criteria.schema.json),
[result record schema](contracts/result-record.schema.json), [mover steps](contracts/mover-steps.md),
[fault points](contracts/fault-points.md). Entities: [data-model.md](data-model.md).

## Prerequisites

- A Linux x86_64 machine with hardware virtualisation: `/dev/kvm` exists and is readable and writable for
  your user. Without KVM the suite refuses to run (no emulated fallback).
- Nix with the `nix-command` and `flakes` features enabled.
- Free disk space for the state directory (>= 100 GiB, on a disk, not a tmpfs) and enough available RAM
  for the configured aggregate VM budget (at most 8 GiB for all test VMs together by default; 32 GiB of
  RAM are not required). The budget is configurable and the run refuses to start rather than overcommit
  it. These are the properties of the reference runner defined in the spec (section "Reference runner").
- No network is needed inside the VMs; the host needs access to the pinned inputs once.

## Scenario 1: static checks (no KVM, seconds to minutes)

```text
nix flake check ./spikes/phase0
nix run ./spikes/phase0#spikectl -- verify
```

Expected: criteria files validate against the schema; every frozen file matches its line in
`frozen.lock` and the lock is append-only; every record names a locked (version, hash); criteria precede
records in git history; ADR status lines are consistent with the verdicts; no committed file contains
absolute host paths, user names or host names; no file outside `spikes/`, `docs/`, `specs/`, `.specify/`, `.claude/`, `.agents/`, `.spaex/` and `.github/workflows/phase0.yml` references `spikes/`.
The oracle unit tests and the negative-control definitions pass.

## Scenario 2: shakedown of one spike (KVM; never records)

```text
nix run ./spikes/phase0#s1 -- --mode shakedown
```

Expected: the S1 VMs boot, every scenario runs and is reported as `pass`, `fail`, `error` or `skipped`;
output goes to a scratch directory; **no record is written**. Use this while writing the harness and the
criteria (plan open item O2). `error` and `skipped` count as failed.

## Scenario 3: freeze the criteria

```text
nix run ./spikes/phase0#spikectl -- freeze S1
```

Expected: `docs/phase0/criteria/frozen.lock` gains one line for S1 (file, version, SHA-256). Commit the
criteria and the lock, merge through a PR, **then** run record mode. Editing a frozen file afterwards makes
`spikectl verify` fail; the correct change is a new version file with `amends`, `reason` and `date`.

## Scenario 4: the full suite, one command

```text
nix run ./spikes/phase0#suite -- --mode record
```

Expected, unattended, on the reference runner (the maintainer's workstation within the 8 GiB VM budget) within the 2-hour initial target (SC-009):

1. `/dev/kvm` is checked; the five drivers are built.
2. Spikes run on the schedule of D13 (S5 parallel to S1; then S2 and S4; S3 sharded), each from a fresh
   machine state, each under its budget. X1 is not part of this run.
3. The negative controls run and every known-bad mover variant, including the stock
   `--remove-source-files` mover, is flagged `fail`. A flagged stock control is a precondition of the S3
   record, not a scenario outcome: if it is not flagged, the harness is reported as invalid and no
   record is written.
4. For each spike, `docs/phase0/results/<spike>/<record-id>/` gains `record.json` and `evidence.tar.xz`.
5. The verdict matrix is printed:

```text
S1 PASS  S2 pending  S3 PASS  S4 PASS  S5 PASS
ADR-0002 eligible: yes (S1..S4 PASS)   ADR-0005 eligible: yes (S5 PASS)
X1 not run (optional, non-gating)
```

A run without KVM, with a dirty git tree, or against criteria that are not frozen refuses to start in
record mode. A run that exceeds a spike budget marks the unfinished scenarios `error` (counts as failed).

## Scenario 5: reproducibility (SC-004)

```text
nix run ./spikes/phase0#suite -- --mode record --repeat 3
```

Expected: three full suite runs in sequence, each from clean state; one `repro` record per spike lists the
three run ids and the per-scenario matrix. Any scenario whose outcome differs between the runs is `flaky`,
which counts as FAIL (spec edge case). The committed reproducibility record comes from this local run on the reference runner; CI offers the same only as an optional chain of three jobs where a runner offers KVM.

## Scenario 5a: the optional overlayfs experiment X1

```text
nix run ./spikes/phase0#x1 -- --mode record
nix run ./spikes/phase0#suite -- --mode record --include-x1
```

Expected: the X1 criteria (frozen before, like any spike) are run on the `x1` profile and one record with
`gating: false` is written; the verdict matrix prints it on a separate line marked non-gating. Skipping X1
is the default and changes nothing: no ADR, no S1 to S5 verdict and no suite budget depends on it, and
`spikectl verify` does not ask for an X1 record. A FAIL of X1 triggers no fallback.

## Scenario 6: a FAIL is a result

Run a spike against a deliberately broken input (a known-bad mover variant from `controls/`, other than the
mandatory stock negative control). Expected: the affected scenario is `fail`, the verdict is `FAIL`, and for
S1 to S5 the record carries the fallback decision named in the criteria. The evidence archive contains the
oracle's expected and observed manifests, and `spikectl verify` refuses an ADR change to `accepted` while
that record is the current one. This scenario never turns the mandatory stock control into a FAIL record: a
stock control that the harness does not flag as `fail` is a broken precondition, so the S3 run is invalid,
no record is written and S3 has no verdict.

## Scenario 7: from verdict to ADR

1. Commit the record directories in a PR (`docs(phase0): record S1-S4 results`).
2. Run `nix run ./spikes/phase0#spikectl -- verdict` and read the gate matrix.
3. If S2 is `pending` (the automated S2.7 checks and every other S2 scenario passed), the reviewer judges the
   written recovery procedure in the evidence of the newest S2 spike-run and creates the review sub-record:

   ```text
   nix run ./spikes/phase0#spikectl -- review <S2 record id> --outcome pass --reviewer <public handle> \
     --evidence <entry of the reviewed evidence archive>
   ```

   Commit it through its own PR (`docs(phase0): review S2.7 recovery procedure`) after the reviewed record
   is merged; `verdict` then shows S2 as `PASS` or `FAIL` (a failing review carries the S2 fallback). An S2
   run that is already `FAIL` needs no review.
4. In a separate reviewed docs-only PR change the `Status:` line of ADR-0002 (S1 to S4 all PASS) or
   ADR-0005 (S5 PASS) and reference the record paths. On a gating S1 to S5 FAIL, adopt the fallback from the
   criteria and update the ADR and the design document in that PR (FR-007).
5. CI runs `spikectl verify` on the PR; it fails if the ADR claims more than the records support.

## Debugging

```text
nix run ./spikes/phase0#s3 -- --mode shakedown --only S3.2:post:M5:poweroff
nix build ./spikes/phase0#driver-interactive-s3
```

`--only` runs a single scenario or case id. The interactive driver gives a Python prompt with the machines
(`server`, `client`) for manual fault experiments; it is a debugging aid and never produces a record.

## What a reviewer checks at the plan gate

- The constitution check in [plan.md](plan.md) and the findings F1 to F12 and the decision section 7 in [research.md](research.md).
- The open items O1 to O16 and their recommendations.
- That the contracts describe interfaces (record schema, criteria schema, mover steps, fault points) and no
  implementation code.
