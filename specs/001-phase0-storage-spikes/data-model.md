# Data Model: Phase 0 Storage Model Spikes

**Feature**: `001-phase0-storage-spikes` | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

The "data" of Phase 0 is the verdict model: files in git that connect criteria, runs, verdicts and
ADR decisions. There is no database. Schemas: [criteria](contracts/criteria.schema.json) and
[result record](contracts/result-record.schema.json). Storage locations are in the plan.

## Entities

### Spike

One of `S1` (tiered-cache share), `S2` (parity recovery), `S3` (mover crash safety), `S4`
(degraded modes), `S5` (declarative rebuild), or the optional experiment `X1` (overlayfs variant).

| Field | Description |
| --- | --- |
| `id` | `S1` .. `S5`, `X1` |
| `name` | Short title from the spec |
| `gates` | The ADR it contributes to: `S1`-`S4` gate ADR-0002, `S5` gates ADR-0005, `X1` gates `none` |
| `fallback` | Fallback text from FR-031 for S1 to S5, carried in the criteria file; X1 has none |
| `criteria_versions` | Ordered list of Criteria versions; the last is the **current** one |
| `records` | Zero or more Result records |

Derived: for S1 to S5 (except the S2 review rule below), the spike's **current verdict** is the verdict of the
newest `spike-run` Result record that names the current criteria version, or `none` if there is no such record.
For `S2`, the current verdict is derived from that run and its matching review: a failed automated S2.7 step
check is `fail` and remains `fail` even if a later review passes, and the same holds when any other S2 scenario
failed (the verdict is `FAIL` at once, with or without a review sub-record). Otherwise the review outcome is the
final S2.7 outcome, or the status is `pending` while no review sub-record exists for that run. `pending` applies
only while every other S2 scenario passed (gating cases only), the automated S2.7 step checks passed and no review
sub-record exists for the newest spike-run of the current criteria version; it is a derived value that no record stores. For `X1`, `none` is a valid permanent
state (the experiment was skipped); for `S1` to `S5` it blocks the gate.

Reproducibility (proposed rule, spec Clarifications 2026-10-17, U8, for maintainer review): a `repro` record never
counts as the newest spike-run. The current verdict of a spike is `FAIL` when any `repro` record of the current
criteria version that is not older than the newest `spike-run` of that version has the verdict `FAIL` (a gating
scenario `flaky` or failed across the three runs); otherwise the rules above apply unchanged. A passing repro
changes no verdict, and a newer spike-run supersedes repro results from before it. The S2 review scope stays the
newest spike-run.

### Acceptance scenario

| Field | Description / validation |
| --- | --- |
| `id` | Stable identifier, pattern `S<n>.<m>` or `X1.<m>` (for example `S3.2`); unique inside a spike; never reused across versions for a different meaning |
| `title` | One line |
| `precondition` | Text: the "Given" |
| `action` | Text: the "When" |
| `observable` | Text: the checkable "Then"; must be decidable without interpretation (FR-002), using the observable terms of the spec's verdict model (named errno, numeric bound, manifest). Exception: `S2.7` carries `review: true` for the written procedure (FR-002) |
| `check` | Name of the automated check (function in the spike's test script); required unless `review: true`. `S2.7` keeps its automated step checks and additionally carries `review: true` |
| `review`, `review_kind` | `review: true` only for `S2.7`; such a scenario names the kind of the record that judges it in `review_kind: review` (required with `review: true`, forbidden otherwise). An S2 criteria file must carry `S2.7` with `review: true` (enforced by `spikectl verify` and `freeze`, task T037d) |
| `cases` | Optional case generator for matrix scenarios (for example `mover-crash-points`), producing case ids `S3.2:<point>:<kill-mode>`, or variant generators (`mount-variants` for `S1.2`, `S1.6`, `S1.8`) where each case is marked `gating: true` (baseline variant) or `gating: false` (informational variant). The criteria file lists the variants of such a scenario in `variants` (variant name, `gating`, at least one variant gating) and the case entries of the record carry the same flag |
| `prediction` | For S2 scenarios: the expected set of recoverable and unrecoverable items, as a rule over the fixture at parity-block granularity, or a reference to a table in the criteria file (FR-016); the snapshot scenario (S2.9) and the interrupted-sync scenario (S2.4) carry one prediction per variant |

Outcome of one execution of a scenario (in a Result record): `pass`, `fail`, `error` (could not
execute, harness or infrastructure failure), `skipped`, `flaky` (differs between repeated runs).
Only `pass` counts as passing for an ordinary scenario; every other value counts as `fail` for the verdict
(spec verdict model). `S2.7` is `fail` when an automated step check fails; otherwise it is `pending` until its
reviewed sub-record exists (the S2 spike status is `pending` only under the three conditions given for the `verdict` field).
For a scenario with cases, the scenario outcome is `pass` only if every **gating** case is `pass`; the
record keeps the case list; the `gating` flag of a case entry is a boolean and an absent flag means `true`. A case with `gating: false` (a non-baseline variant of `S1.2`, `S1.6` or
`S1.8`: create policy `lus`, `passthrough.io=rw`, `cache.files=auto-full`) is informational: its outcome and
evidence are recorded in the case list but never change the scenario outcome or the verdict.

`S2.7` (reviewed judgement): the spike-run record carries the outcomes of its automated step checks (the `S2.7`
outcome in a spike-run record is the outcome of the step checks only). The scenario outcome that counts for the
verdict is set by a separate **review sub-record** (a record of kind `review`, committed through a PR, see
Result record) that names the S2 record and sets `S2.7` to `pass` or `fail`. A failed step check makes `S2.7`
`fail` without review, and a later passing review cannot override that failure. When the automated checks pass,
the review sub-record sets the final outcome to `pass` or `fail`; until it exists, `S2.7` is `pending`, and the S2
verdict is `pending` only if in addition every other S2 scenario passed (gating cases only); if another S2 scenario
failed, the S2 verdict is `FAIL` at once, with or without a review sub-record, because the review judges S2.7 only
and cannot rescue the run. While the S2 verdict is `pending` no ADR gate may be applied. A failing review carries the
immutable `fallback_decision`, and so does a `FAIL` caused by another failed scenario. A later rerun produces a new
record; the earlier `FAIL` stays as history.

`pending` is a derived value, not a stored one (decided with the schema extension of T037a). A result record is
immutable once committed, so a stored `pending` would have to be rewritten when the review arrives; instead the
review is a second, additive record and the tooling derives the status from the pair: the newest `spike-run`
record of the current criteria version is `pending` only if every other S2 scenario passed (gating cases only), its
automated S2.7 checks pass and no `review` record names it; a failed automated S2.7 check or a failed other S2
scenario is `FAIL` regardless of review records. For that reason
`outcome` and `verdict` in the schema keep only their existing values, and the `verdict` of an S2 spike-run
record is the automated verdict (PASS only if every scenario including the `S2.7` step checks passes); it never
stands for the S2 spike status on its own.


### Criteria version

The frozen wording of one spike's scenarios.

| Field | Description / validation |
| --- | --- |
| `spike` | `S1` .. `S5`, or optional non-gating `X1` |
| `criteria_version` | Integer >= 1 |
| `status` | `draft` or `frozen`. Only `frozen` versions can be named by a record |
| `scenarios` | List of Acceptance scenarios; ids unique; at least the scenarios required by the FRs listed per spike in the spec (FR-012, FR-015, FR-018, FR-023, FR-027; FR-033 for X1) |
| `fallback` | Fallback decision text for S1 to S5 (FR-031); X1 has no fallback |
| `amends`, `reason`, `date` | Required for versions >= 2: previous version, why, when (FR-004) |
| `tables` | Data tables the checks read: S2 recoverability predictions (plain and snapshot variant), S4 degraded-state table, S3 crash-point list (21 points, enumerated in the mover contract), the minimum-age parameter of the mover's selection and the retention period of the trash |
| `changelog` | Pre-freeze edit notes (plan open item O2) |

Frozen identity: the triple (`file`, `version`, SHA-256 of the file bytes) recorded as a line
`<file> <version> <sha256>` in `docs/phase0/criteria/frozen.lock`. A file named in the lock is immutable;
the lock is append-only.

### Result record

The outcome of one run of one spike. Immutable once committed. Fields (full schema in the contract):

| Field | Description |
| --- | --- |
| `record_id` | `<spike>-<UTC timestamp basic format>-v<criteria version>` |
| `kind` | `spike-run` or `repro` (three-run record, SC-004); the review sub-record of S2.7 is a further kind `review` (below). A repeat run (`--repeat 3`) writes one `repro` record per spike and never a `spike-run` record |
| `repro_runs` | `kind: repro` only: the run ids of the three consecutive clean runs. They are not separate records: their per-scenario outcomes are combined in this record's `scenarios` (`pass` only if all three passed, `flaky` if they differ, otherwise the common outcome) and their evidence is in this record's archive |
| `gating` | X1 records only: `false`. The experiment never gates an ADR; absent on S1 to S5 records |
| `spike`, `criteria_version`, `criteria_sha256` | What was judged; must equal a line of `frozen.lock` |
| `environment` | Environment revision: repository commit, `flake.lock` SHA-256, nixpkgs revision, tool versions observed inside the guest, topology profile, host class descriptor (CPU model class, vCPU count, memory), QEMU version, KVM yes/no, kernel config pre-flight results |
| `date` | UTC start and end |
| `scenarios` | Per scenario: `id`, `outcome`, `cases` (id, outcome, `gating` flag, fault point, kill mode), `evidence` (paths inside the evidence archive, SHA-256), `notes` |
| `verdict` | `PASS` or `FAIL` for a run record; derived only from scenario outcomes. The S2 spike status can additionally be `pending` (derived, never stored) only while every other S2 scenario passed (gating cases only), the automated S2.7 checks pass and no review sub-record exists for the newest spike-run of the current criteria version |
| `fallback_decision` | Required on `FAIL` for S1 to S5, including a failing S2 review record: which fallback applies (FR-007); omitted for X1, which has no fallback |
| `coverage` | Counts for SC-005 (crash points enumerated, executed, per kill mode; 21 points, 42 cases as enumerated in the mover contract; the named fault and injection points are not counted), prediction comparisons for SC-006, transitions for SC-007, durations for SC-008 |
| `required_settings` | Mount or share settings found necessary (FR-013: create policy, branch modes, out-of-space settings, rename setting), the procedure for branch changes made directly by the mover (S1.10), written recovery procedure reference (S2.7) |
| `residual_risk` | Fixed statements, at least: device-level volatile caches not emulated, virtual disks stand in for real disks |
| `supersedes` | Optional record id of an earlier record this run re-runs after a documented design correction (never deletes the earlier one) |
| `evidence_archive` | Path of `evidence.tar.xz` and its SHA-256 |
| `reviewed_record`, `review` | `kind: review` only (`spike` is `S2`, `record_id` has the prefix `review-S2-`): `reviewed_record` is the id of the S2 `spike-run` record it judges; `review` holds `scenario` (`S2.7`), `outcome` (`pass` or `fail`), `reviewer`, `date`, `evidence` (entries of the reviewed record's evidence archive that were read, pinned by SHA-256) and optional `notes`. A failing review also carries `fallback_decision`. It carries the criteria version and hash of the reviewed record but none of the other run fields (`environment`, `scenarios`, `verdict`, `evidence_archive`, ...) |

Validation rules: the criteria version named must be `frozen`; `verdict` equals `PASS` iff every
scenario of that criteria version appears with outcome `pass` (variant cases with `gating: false` and the
review rule of `S2.7` as above); a scenario missing from the record counts as `fail`; a record exists only
for a valid run (for S3: the stock-mover negative control was flagged `fail`; otherwise no record is
written and S3 has no verdict); free text and evidence contain no absolute host paths, user names or host names
(sanitiser plus `spikectl verify`).

### Verdict

`PASS` or `FAIL` for one run record. A spike passes only if every scenario passes (no partial pass); variant
cases with `gating: false` do not count. For `S2`, `pending` applies only while every other S2 scenario passed
(gating cases only), the automated S2.7 checks pass and no review sub-record exists for the newest spike-run of the
current criteria version; any automated failure (S2.7 step check or another scenario) is `FAIL`, with or without a
review sub-record and even after a passing review. Derived by
`spikectl verdict`; never typed by hand.

### ADR gate

The rule linking verdicts to ADR status. Computed, not stored.

| ADR | May be `accepted` only if | If a required spike FAILs |
| --- | --- | --- |
| ADR-0002 | S1, S2, S3 and S4 each have a current PASS record (for S2 including the review sub-record of S2.7; a run with another failed S2 scenario is `FAIL`, not `pending`; while S2 is `pending`, meaning every other S2 scenario passed, the automated S2.7 checks passed and no review sub-record exists, no ADR gate may be applied) | stays `proposed` until the criteria's fallback is adopted and the ADR text and design document are updated (S4 has no fallback of its own: stays `proposed` until S4 passes) |
| ADR-0005 | S5 has a current PASS record | stays `proposed`, decision amended and S5 re-run (spec FR-031) |
| (none) | X1 is never a condition: its verdict, or the absence of any X1 record, changes no ADR status | no consequence (FR-034) |

An accepted ADR references the record paths. `spikectl verify` checks this on PRs that touch
`docs/adr/` or `docs/phase0/`.

### Environment

The topology and inputs a run used: the `environment` object of a `spike-run` or `repro` record. The field names
are those of [the result record schema](contracts/result-record.schema.json).

| Field | Description |
| --- | --- |
| `repo_commit`, `flake_lock_sha256`, `nixpkgs_rev` | The pins: repository commit, `flake.lock` SHA-256, nixpkgs revision (required) |
| `profile` | `large` (S1), `small` (S2-S4), `s5`, or `x1` (the `large` disks with the overlay stack, X1 only) (required) |
| `disks` | List of (`serial`, `role`, `size_mib`, `filesystem`); archive disks are btrfs. Optional in the schema today; task T037e makes it required for S1 to S4 records (FR-009) |
| `vm_resources` | Per VM name (`server`, `client` for S1 only) its `vcpus` and `memory_mib` (FR-009) |
| `tool_versions` | Observed in the guest or on the host at run time: `kernel` and `qemu` (required), mergerfs, snapraid, btrfs-progs, snapper, snapshot tooling revision, samba, nix |
| `kernel_config` | Pre-flight results read from the running kernel |
| `host` | `cpu_class`, `vcpus`, `memory_gib` of the host class; no host name (required) |
| `kvm` | Always `true`: a run without KVM never produces a record (required) |

The named fault points exercised are not an environment field: each case entry of a record carries its
`fault_kind` and `fault_point` (see [fault-points](contracts/fault-points.md)).

### Recovery procedure

The written, tested sequence produced by S2 (detect failure, lock, replace disk, recover, verify,
resynchronise, unlock). Stored as a text rendered by the S2.7 test into the evidence archive and quoted
in the S2 record (`required_settings.recovery_procedure`). The only deliverable judged by review
(FR-002); the judgement is carried by the review sub-record of `S2.7`. It must keep the shape of the SnapRAID manual's recovery skeleton (research 3.4) and record the
explicit check after every step.

### Degraded-state table

The S4 state table (healthy, pool degraded, archive degraded, both degraded versus status text, read,
write through share (new files and in-place changes to archived files), sync, scrub, mover, direct archive write; the direct archive write cells of healthy and pool degraded read "not tested: no client route exists", refusals use named errnos such as `EROFS`). Lives as data in `S4.yaml` under
`tables`; the S4 checks read it, so the table and the test cannot drift apart.

### Mover step list (shared artifact)

The five steps (copy, checksum, sync, verification, pool copy removal) and their hook points; defined
in [contracts/mover-steps.md](contracts/mover-steps.md); S3 enumerates crash points from it (21 points,
42 cases). Beyond the five steps it defines the selection (minimum age, no open handle), the copy-time
identity compared at M2 and M5, the trash into which M5 moves the archive copy of a pool file that a client deleted, where the delete left the archive copy in place (expected behaviour, UNVERIFIED until S1 and S3 prove it; a vanished item without archive copy, removed by the client's delete under the measured unlink policy, is a reported outcome with nothing to trash), and no lock that clients must honour. A change after Phase 0 reopens S3 (spec assumption). There are no whiteout steps in the
baseline.

## Relationships

```text
Spike 1 ── n Criteria version 1 ── n Acceptance scenario
Criteria version 1 ── 0..n Result record        (record names version + hash; version must be frozen)
Result record 1 ── n scenario outcomes (1 per scenario of that version) ── 0..n cases
Result record 0..1 ── supersedes ── Result record
Spike (S1..S4) n ── gates ── 1 ADR-0002 ;  Spike S5 ── gates ── ADR-0005 ;  Spike X1 ── gates ── nothing
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
S2 record with a failed automated S2.7 check --> FAIL (a later review cannot override it)
S2 record with another S2 scenario failed and automated S2.7 checks pass, review absent --> FAIL (fallback_decision required; a later review cannot rescue it)
S2 record with all other S2 scenarios passed, automated S2.7 checks pass and no review --> pending
S2 record with automated S2.7 checks pass and a passing review, all other scenarios pass --> PASS
S2 record with automated S2.7 checks pass and a failing review --> FAIL (fallback_decision required)
record for current version, all scenarios pass --> PASS
record for current version, any other outcome --> FAIL (fallback_decision required for S1 to S5)
any repro record of the current version, not older than the newest spike-run, with verdict FAIL --> FAIL (proposed rule, U8)
new criteria version frozen --> none (old verdicts void)
```

ADR status (decided by a human through a PR, checked by `spikectl verify`):

```text
proposed --(required spikes current PASS + reviewed PR)--> accepted
proposed --(a required spike FAIL + fallback adopted + ADR and design text updated)--> proposed, fallback adopted
```
