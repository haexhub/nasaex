"""Record writer, RAM-budget preflight and mode gate."""

import datetime
import json

import pytest
from helpers import (
    CRITERIA,
    RESULTS,
    S2_OUTCOMES,
    commit_all,
    criteria_doc,
    init_repo,
    make_root,
    record_doc,
    sha256_bytes,
    write_criteria,
)

from spikectl.freeze import freeze
from spikectl.model import schema_errors
from spikectl.record import (
    DEFAULT_RAM_BUDGET_MIB,
    PreflightError,
    RecordError,
    build_record,
    build_review_record,
    configured_ram_budget_mib,
    make_record_id,
    preflight,
    write_record,
)

PASS_ALL = [{"id": "S1.1", "outcome": "pass"}, {"id": "S1.2", "outcome": "pass"}]
ENVIRONMENT = {
    "repo_commit": "1" * 40,
    "flake_lock_sha256": "2" * 64,
    "nixpkgs_rev": "3" * 40,
    "profile": "large",
    "tool_versions": {"kernel": "6.18.56", "qemu": "10.2.4"},
    "host": {"cpu_class": "test", "vcpus": 2, "memory_gib": 8},
    "kvm": True,
}
NOW = datetime.datetime(2026, 10, 22, 10, 15, 0, tzinfo=datetime.UTC)


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


@pytest.fixture
def kvm(tmp_path_factory):
    # outside the repository root, which must stay clean
    path = tmp_path_factory.mktemp("device") / "kvm-device"
    path.write_text("")
    return path


@pytest.fixture
def clean_locked_repo(root):
    init_repo(root)
    write_criteria(root)
    entry = freeze(root, "S1")
    commit_all(root, "docs(phase0): freeze S1 criteria")
    return root, entry


def run_preflight(root, mode, kvm, *, vm_ram_mib=2048, budget=8192, available=16384):
    preflight(
        root,
        "S1",
        mode,
        vm_ram_mib=vm_ram_mib,
        ram_budget_mib=budget,
        available_ram_mib=lambda: available,
        kvm_path=kvm,
    )


# --- RAM budget, both modes ------------------------------------------------


def test_default_ram_budget_is_8_gib_and_can_be_configured():
    assert DEFAULT_RAM_BUDGET_MIB == 8192
    assert configured_ram_budget_mib({}) == 8192
    assert configured_ram_budget_mib({"SPIKE_VM_RAM_BUDGET_MIB": "4096"}) == 4096
    with pytest.raises(PreflightError):
        configured_ram_budget_mib({"SPIKE_VM_RAM_BUDGET_MIB": "lots"})
    with pytest.raises(PreflightError):
        configured_ram_budget_mib({"SPIKE_VM_RAM_BUDGET_MIB": "0"})


@pytest.mark.parametrize("mode", ["shakedown", "record"])
def test_both_modes_refuse_when_planned_vm_memory_exceeds_the_budget(root, kvm, mode):
    with pytest.raises(PreflightError, match="budget"):
        run_preflight(root, mode, kvm, vm_ram_mib=9000, budget=8192)


@pytest.mark.parametrize("mode", ["shakedown", "record"])
def test_both_modes_refuse_when_the_memory_is_not_available(root, kvm, mode):
    with pytest.raises(PreflightError, match="available"):
        run_preflight(root, mode, kvm, vm_ram_mib=4096, available=3000)


def test_shakedown_runs_without_kvm_on_a_dirty_tree_and_unlocked_criteria(root, tmp_path):
    run_preflight(root, "shakedown", tmp_path / "no-kvm-here")


# --- record mode gate ------------------------------------------------------


def test_record_mode_passes_with_kvm_a_clean_tree_and_locked_criteria(clean_locked_repo, kvm):
    root, _ = clean_locked_repo

    run_preflight(root, "record", kvm)


def test_record_mode_refuses_without_kvm(clean_locked_repo, tmp_path):
    root, _ = clean_locked_repo

    with pytest.raises(PreflightError, match="kvm"):
        run_preflight(root, "record", tmp_path / "missing")


def test_record_mode_refuses_a_dirty_git_tree(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    (root / "stray.txt").write_text("x")

    with pytest.raises(PreflightError, match="dirty"):
        run_preflight(root, "record", kvm)


def test_record_mode_refuses_when_the_current_criteria_version_is_not_locked(root, kvm):
    init_repo(root)
    write_criteria(root)
    commit_all(root)

    with pytest.raises(PreflightError, match="locked"):
        run_preflight(root, "record", kvm)


def test_record_mode_refuses_when_a_newer_draft_amendment_exists(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    write_criteria(root, version=2)
    commit_all(root)

    with pytest.raises(PreflightError, match="locked"):
        run_preflight(root, "record", kvm)


def test_record_mode_refuses_when_the_locked_file_was_edited(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    path = root / CRITERIA / "S1.yaml"
    path.write_text(path.read_text() + "# edit\n")
    commit_all(root)

    with pytest.raises(PreflightError, match="locked"):
        run_preflight(root, "record", kvm)


# --- record contents -------------------------------------------------------


def test_record_id_uses_the_basic_utc_timestamp_and_criteria_version():
    assert make_record_id("S3", 2, NOW) == "S3-20261022T101500Z-v2"
    local = NOW.astimezone(datetime.timezone(datetime.timedelta(hours=2)))
    assert make_record_id("S3", 2, local) == "S3-20261022T101500Z-v2"


def build(root, entry, scenarios=PASS_ALL, evidence_sha256="e" * 64, **kwargs):
    return build_record(
        spike="S1",
        criteria=criteria_doc(),
        lock_entry=entry,
        scenarios=scenarios,
        environment=ENVIRONMENT,
        start=NOW,
        end=NOW + datetime.timedelta(minutes=30),
        evidence_sha256=evidence_sha256,
        **kwargs,
    )


def test_record_validates_against_the_schema_and_names_the_locked_pair(clean_locked_repo):
    root, entry = clean_locked_repo

    record = build(root, entry)

    assert schema_errors(root, "result-record.schema.json", record) == []
    assert (record["criteria_version"], record["criteria_sha256"]) == (1, entry.sha256)
    assert record["record_id"] == "S1-20261022T101500Z-v1"
    assert record["kind"] == "spike-run"


def test_verdict_is_pass_only_if_every_scenario_of_the_criteria_passes(clean_locked_repo):
    root, entry = clean_locked_repo

    assert build(root, entry)["verdict"] == "PASS"
    assert build(root, entry, [PASS_ALL[0]])["verdict"] == "FAIL"  # S1.2 missing
    failed = [PASS_ALL[0], {"id": "S1.2", "outcome": "skipped"}]
    assert build(root, entry, failed)["verdict"] == "FAIL"


def test_a_fail_record_carries_the_criteria_fallback_for_gating_spikes(clean_locked_repo):
    root, entry = clean_locked_repo

    record = build(root, entry, [PASS_ALL[0]])

    assert record["fallback_decision"] == criteria_doc()["fallback"]
    assert "fallback_decision" not in build(root, entry)


def test_residual_risk_always_states_the_fixed_limitations(clean_locked_repo):
    root, entry = clean_locked_repo

    for record in (build(root, entry), build(root, entry, residual_risk=["extra statement"])):
        text = " ".join(record["residual_risk"]).lower()
        assert "volatile" in text and "not emulated" in text
        assert "virtual disks" in text and "real disks" in text
    assert (
        "extra statement" in build(root, entry, residual_risk=["extra statement"])["residual_risk"]
    )


def test_x1_records_are_non_gating_and_have_no_fallback(root):
    write_criteria(root, spike="X1")
    entry = freeze(root, "X1")

    record = build_record(
        spike="X1",
        criteria=criteria_doc("X1"),
        lock_entry=entry,
        scenarios=[{"id": "X1.1", "outcome": "fail"}, {"id": "X1.2", "outcome": "pass"}],
        environment=ENVIRONMENT,
        start=NOW,
        end=NOW,
        evidence_sha256="e" * 64,
    )

    assert record["gating"] is False
    assert "fallback_decision" not in record
    assert schema_errors(root, "result-record.schema.json", record) == []


def test_an_informational_failing_case_does_not_change_the_derived_verdict(clean_locked_repo):
    root, entry = clean_locked_repo
    scenarios = [
        PASS_ALL[0],
        {
            "id": "S1.2",
            "outcome": "pass",
            "cases": [
                {"id": "S1.2:ff", "outcome": "pass", "gating": True},
                {"id": "S1.2:lus", "outcome": "fail", "gating": False},
            ],
        },
    ]

    record = build(root, entry, scenarios)

    assert record["verdict"] == "PASS"
    assert "fallback_decision" not in record
    assert schema_errors(root, "result-record.schema.json", record) == []


def test_supersedes_is_recorded_when_given(clean_locked_repo):
    root, entry = clean_locked_repo

    record = build(root, entry, supersedes="S1-20261001T000000Z-v1")

    assert record["supersedes"] == "S1-20261001T000000Z-v1"


# --- writing ---------------------------------------------------------------


ARCHIVE_SHA = sha256_bytes(b"archive bytes")


def evidence_file(tmp_path):
    path = tmp_path / "evidence.tar.xz"
    path.write_bytes(b"archive bytes")
    return path


def test_record_mode_writes_record_and_evidence_into_the_results_tree(clean_locked_repo, tmp_path):
    root, entry = clean_locked_repo
    archive = evidence_file(tmp_path)
    record = build(root, entry, evidence_sha256=ARCHIVE_SHA)

    target = write_record(root, record, archive, "record")

    assert target == root / RESULTS / "S1" / record["record_id"]
    assert json.loads((target / "record.json").read_text()) == record
    assert (target / "evidence.tar.xz").read_bytes() == b"archive bytes"


def test_shakedown_writes_only_below_the_scratch_directory(clean_locked_repo, tmp_path):
    root, entry = clean_locked_repo
    before = sorted(p for p in (root / RESULTS).rglob("*"))

    record = build(root, entry, evidence_sha256=ARCHIVE_SHA)

    target = write_record(root, record, evidence_file(tmp_path), "shakedown")

    assert sorted(p for p in (root / RESULTS).rglob("*")) == before
    assert "scratch" in target.parts
    assert (target / "record.json").exists()


def test_an_existing_record_is_never_overwritten(clean_locked_repo, tmp_path):
    root, entry = clean_locked_repo
    record = build(root, entry, evidence_sha256=ARCHIVE_SHA)
    write_record(root, record, evidence_file(tmp_path), "record")

    with pytest.raises(RecordError, match="exists"):
        write_record(root, record, evidence_file(tmp_path), "record")


def test_a_record_whose_evidence_hash_differs_from_the_archive_is_not_written(
    clean_locked_repo, tmp_path
):
    root, entry = clean_locked_repo

    with pytest.raises(RecordError, match="evidence"):
        write_record(root, build(root, entry), evidence_file(tmp_path), "record")

    assert not (root / RESULTS / "S1").exists()


def test_a_record_that_fails_the_schema_is_not_written(clean_locked_repo, tmp_path):
    root, entry = clean_locked_repo
    record = build(root, entry, evidence_sha256=ARCHIVE_SHA)
    del record["residual_risk"]

    with pytest.raises(RecordError, match="schema"):
        write_record(root, record, evidence_file(tmp_path), "record")

    assert not (root / RESULTS / "S1").exists()


# --- review records --------------------------------------------------------

EVIDENCE = [{"path": "procedure.txt", "sha256": "b" * 64}]
REVIEW_TIME = datetime.datetime(2026, 10, 23, 12, 0, 0, tzinfo=datetime.UTC)


@pytest.fixture
def s2_root(root):
    write_criteria(root, doc=criteria_doc("S2", scenarios=7, review=True))
    entry = freeze(root, "S2")
    run = record_doc("S2", entry.version, entry.sha256, S2_OUTCOMES)
    return root, run


def review(run, outcome="pass", **overrides):
    arguments = {
        "reviewed_record": run,
        "criteria": criteria_doc("S2", scenarios=7, review=True),
        "outcome": outcome,
        "reviewer": "maintainer",
        "when": REVIEW_TIME,
        "evidence": EVIDENCE,
    }
    return build_review_record(**{**arguments, **overrides})


def test_an_s2_run_with_passing_automated_checks_stores_pass_never_pending(s2_root):
    root, run = s2_root

    assert run["verdict"] == "PASS"
    assert schema_errors(root, "result-record.schema.json", run) == []


def test_a_review_record_names_the_reviewed_record_and_validates(s2_root):
    root, run = s2_root

    record = review(run)

    assert record["record_id"] == "review-S2-20261023T120000Z-v1"
    assert record["kind"] == "review"
    assert record["reviewed_record"] == run["record_id"]
    assert (record["criteria_version"], record["criteria_sha256"]) == (
        run["criteria_version"],
        run["criteria_sha256"],
    )
    assert record["review"] == {
        "scenario": "S2.7",
        "outcome": "pass",
        "reviewer": "maintainer",
        "date": "2026-10-23T12:00:00Z",
        "evidence": EVIDENCE,
    }
    assert schema_errors(root, "result-record.schema.json", record) == []


def test_a_review_record_carries_no_run_fields(s2_root):
    _, run = s2_root

    record = review(run)

    for forbidden in ("environment", "scenarios", "verdict", "evidence_archive", "date"):
        assert forbidden not in record


def test_a_passing_review_has_no_fallback_and_a_failing_one_carries_the_criteria_fallback(s2_root):
    root, run = s2_root

    failing = review(run, "fail")

    assert "fallback_decision" not in review(run)
    assert failing["fallback_decision"] == criteria_doc("S2")["fallback"]
    assert schema_errors(root, "result-record.schema.json", failing) == []


def test_optional_review_notes_are_recorded(s2_root):
    root, run = s2_root

    record = review(run, notes="steps followed on a scratch copy")

    assert record["review"]["notes"] == "steps followed on a scratch copy"
    assert schema_errors(root, "result-record.schema.json", record) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"outcome": "pending"}, "outcome"),
        ({"outcome": "PASS"}, "outcome"),
        ({"reviewer": ""}, "reviewer"),
        ({"evidence": []}, "evidence"),
    ],
)
def test_a_review_with_unusable_input_is_refused(s2_root, overrides, message):
    _, run = s2_root

    with pytest.raises(RecordError, match=message):
        review(run, **overrides)


def test_only_an_s2_spike_run_can_be_reviewed(clean_locked_repo):
    root, entry = clean_locked_repo
    s1_run = build(root, entry)

    with pytest.raises(RecordError, match="S2"):
        review(s1_run)


def test_a_review_record_is_written_without_an_evidence_archive(s2_root):
    root, run = s2_root
    record = review(run)

    target = write_record(root, record, None, "record")

    assert target == root / RESULTS / "S2" / record["record_id"]
    assert json.loads((target / "record.json").read_text()) == record
    assert [p.name for p in target.iterdir()] == ["record.json"]


def test_a_review_record_is_never_overwritten(s2_root):
    root, run = s2_root
    record = review(run)
    write_record(root, record, None, "record")

    with pytest.raises(RecordError, match="exists"):
        write_record(root, record, None, "record")


def test_a_review_record_that_fails_the_schema_is_not_written(s2_root):
    root, run = s2_root
    record = review(run)
    record["review"]["outcome"] = "pending"

    with pytest.raises(RecordError, match="schema"):
        write_record(root, record, None, "record")

    assert not (root / RESULTS / "S2").exists()


def test_a_review_record_with_an_evidence_archive_is_refused(s2_root, tmp_path):
    root, run = s2_root

    with pytest.raises(RecordError, match="archive"):
        write_record(root, review(run), evidence_file(tmp_path), "record")


def test_a_spike_run_record_without_an_evidence_archive_is_refused(clean_locked_repo):
    root, entry = clean_locked_repo

    with pytest.raises(RecordError, match="archive"):
        write_record(root, build(root, entry, evidence_sha256=ARCHIVE_SHA), None, "record")
