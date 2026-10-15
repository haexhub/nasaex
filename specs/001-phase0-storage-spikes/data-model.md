# Data Model: Phase 0 Storage Model Spikes

**Feature**: `001-phase0-storage-spikes` | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

The "data" of Phase 0 is the verdict model: files in git that connect criteria, runs, verdicts and
ADR decisions. There is no database. Schemas: [criteria](contracts/criteria.schema.json) and
[result record](contracts/result-record.schema.json). Storage locations are in the plan.

## Entities

### Spike

One of `S1` (stacked share filesystem), `S2` (parity recovery), `S3` (mover crash safety), `S4`
(degraded modes), `S5` (declarative rebuild).

| Field | Description |
| --- | --- |
| `id` | `S1` .. `S5` |
| `name` | Short title from the spec |
| `gates` | The ADR it contributes to: `S1`-`S4` gate ADR-0002, `S5` gates ADR-0005 |
| `fallback` | Fallback text from FR-031, carried in the criteria file |
| `criteria_versions` | Ordered list of Criteria versions; the last is the **current** one |
| `records` | Zero or more Result records |

Derived: the spike's **current verdict** is the verdict of the newest Result record that names the
current criteria version, or `none` if there is no such record.

### Acceptance scenario

| Field | Description / validation |
| --- | --- |
| `id` | Stable identifier, pattern `S<n>.<m>` (for example `S3.2`); unique inside a spike; never reused across versions for a different meaning |
| `title` | One line |
| `precondition` | Text: the "Given" |
| `action` | Text: the "When" |
| `observable` | Text: the checkable "Then"; must be decidable without interpretation (FR-002). Exception: `S2.7` carries `review: true` for the written procedure (FR-002) |
| `check` | Name of the automated check (function in the spike's test script); required unless `review: true` |
| `cases` | Optional case generator for matrix scenarios (for example `mover-crash-points`), producing case ids `S3.2:<point>:<kill-mode>` |
| `prediction` | For S2 scenarios: the expected set of recoverable and unrecoverable items, as a rule over the fixture (block granularity), or a reference to a table in the criteria file (FR-016) |

Outcome of one execution of a scenario (in a Result record): `pass`, `fail`, `error` (could not
execute, harness or infrastructure failure), `skipped`, `flaky` (differs between repeated runs).
Only `pass` counts as passing; every other value counts as `fail` for the verdict (spec verdict model).
For a scenario with cases, the scenario outcome is `pass` only if every case is `pass`; the record
keeps the case list.

### Criteria version

The frozen wording of one spike's scenarios.

| Field | Description / validation |
| --- | --- |
| `spike` | `S1` .. `S5` |
| `criteria_version` | Integer >= 1 |
| `status` | `draft` or `frozen`. Only `frozen` versions can be named by a record |
| `scenarios` | List of Acceptance scenarios; ids unique; at least the scenarios required by the FRs listed per spike in the spec (FR-012, FR-015, FR-018, FR-023, FR-027) |
| `fallback` | Fallback decision text (FR-031) |
| `amends`, `reason`, `date` | Required for versions >= 2: previous version, why, when (FR-004) |
| `tables` | Data tables the checks read: S2 recoverability predictions, S4 degraded-state table, S3 crash-point list |
| `changelog` | Pre-freeze edit notes (plan open item O2) |

Frozen identity: the pair (`file`, SHA-256 of the file bytes) recorded as a line
`<file> <version> <sha256>` in `docs/phase0/criteria/frozen.lock`. A file named in the lock is immutable;
the lock is append-only.

### Result record

The outcome of one run of one spike. Immutable once committed. Fields (full schema in the contract):

| Field | Description |
| --- | --- |
| `record_id` | `<spike>-<UTC timestamp basic format>-v<criteria version>` |
| `kind` | `spike-run` or `repro` (three-run record, SC-004) |
| `spike`, `criteria_version`, `criteria_sha256` | What was judged; must equal a line of `frozen.lock` |
| `environment` | Environment revision: repository commit, `flake.lock` SHA-256, nixpkgs revision, tool versions observed inside the guest, topology profile, host class descriptor (CPU model class, vCPU count, memory), QEMU version, KVM yes/no, kernel config pre-flight results |
| `date` | UTC start and end |
| `scenarios` | Per scenario: `id`, `outcome`, `cases` (id, outcome, fault point, kill mode), `evidence` (paths inside the evidence archive, SHA-256), `notes` |
| `verdict` | `PASS` or `FAIL`; derived only from scenario outcomes |
| `fallback_decision` | Required when verdict is `FAIL`: which fallback applies (FR-007) |
| `coverage` | Counts for SC-005 (crash points enumerated, executed, per kill mode), prediction comparisons for SC-006, transitions for SC-007, durations for SC-008 |
| `required_settings` | Mount or share settings found necessary (FR-013), mover refresh procedure (S1.10), written recovery procedure reference (S2.7) |
| `residual_risk` | Fixed statements, at least: device-level volatile caches not emulated, virtual disks stand in for real disks |
| `supersedes` | Optional record id of an earlier record this run re-runs after a documented design correction (never deletes the earlier one) |
| `evidence_archive` | Path of `evidence.tar.xz` and its SHA-256 |

Validation rules: the criteria version named must be `frozen`; `verdict` equals `PASS` iff every
scenario of that criteria version appears with outcome `pass`; a scenario missing from the record
counts as `fail`; free text and evidence contain no absolute host paths, user names or host names
(sanitiser plus `spikectl verify`).

### Verdict

`PASS` or `FAIL` for one record. A spike passes only if every scenario passes (no partial pass).
Derived by `spikectl verdict`; never typed by hand.

### ADR gate

The rule linking verdicts to ADR status. Computed, not stored.

| ADR | May be `accepted` only if | If a required spike FAILs |
| --- | --- | --- |
| ADR-0002 | S1, S2, S3 and S4 each have a current PASS record | stays `proposed` until the criteria's fallback is adopted and the ADR text and design document are updated (S4 has no fallback of its own: stays `proposed` until S4 passes) |
| ADR-0005 | S5 has a current PASS record | stays `proposed`, decision amended and S5 re-run (spec FR-031) |

An accepted ADR references the record paths. `spikectl verify` checks this on PRs that touch
`docs/adr/` or `docs/phase0/`.

### Environment

The topology and inputs a run used.

| Field | Description |
| --- | --- |
| `profile` | `large`, `small` or `s5` |
| `disks` | List of (serial, role, size in MiB, filesystem) |
| `vms` | `server`, `client` (S1 only) with vCPU and memory |
| `pins` | nixpkgs revision, `flake.lock` SHA-256, repository commit |
| `tool_versions` | Observed in the guest: mergerfs, snapraid, btrfs-progs, samba, kernel, plus host QEMU version |
| `fault_points` | The named points exercised (see [fault-points](contracts/fault-points.md)) |

### Recovery procedure

The written, tested sequence produced by S2 (detect failure, lock, replace disk, recover, verify,
resynchronise, unlock). Stored as a text rendered by the S2.7 test into the evidence archive and quoted
in the S2 record (`required_settings.recovery_procedure`). The only deliverable judged by review
(FR-002). It must keep the shape of the SnapRAID manual's recovery skeleton (research 3.4) and record the
explicit check after every step.

### Degraded-state table

The S4 state table (healthy, pool degraded, archive degraded, both degraded versus status text, read,
write through share, sync, scrub, mover, direct archive write). Lives as data in `S4.yaml` under
`tables`; the S4 checks read it, so the table and the test cannot drift apart.

### Mover step list (shared artifact)

The nine steps and their hook points; defined in [contracts/mover-steps.md](contracts/mover-steps.md);
S3 enumerates crash points from it. A change after Phase 0 reopens S3 (spec assumption).

## Relationships

```text
Spike 1 ── n Criteria version 1 ── n Acceptance scenario
Criteria version 1 ── 0..n Result record        (record names version + hash; version must be frozen)
Result record 1 ── n scenario outcomes (1 per scenario of that version) ── 0..n cases
Result record 0..1 ── supersedes ── Result record
Spike (S1..S4) n ── gates ── 1 ADR-0002 ;  Spike S5 ── gates ── ADR-0005
Result record 1 ── 1 Environment
```

## State transitions

Criteria version:

```text
draft --(spikectl freeze: append lock line)--> frozen
frozen --(amendment: new version file, reason recorded)--> frozen (older version) + new draft (next version)
```

A frozen version never returns to `draft` and is never edited. Creating version *k+1* leaves every
record of version *k* in place; they are no longer current, so their verdicts no longer count.

Spike verdict (derived):

```text
no record for current version --> none
record for current version, all scenarios pass --> PASS
record for current version, any other outcome --> FAIL (fallback_decision required)
new criteria version frozen --> none (old verdicts void)
```

ADR status (decided by a human through a PR, checked by `spikectl verify`):

```text
proposed --(required spikes current PASS + reviewed PR)--> accepted
proposed --(a required spike FAIL + fallback adopted + ADR and design text updated)--> proposed, fallback adopted
```
