"""spikectl verify for review records (S2.7): layout, schema, lock pairing, the reviewed record,
its pinned evidence entries and commit order."""

import json

import pytest
from helpers import (
    RESULTS,
    S2_OUTCOMES,
    commit_all,
    criteria_doc,
    evidence_tar,
    git,
    init_repo,
    make_root,
    record_doc,
    review_doc,
    sha256_bytes,
    write_criteria,
    write_record,
    write_review,
)

from spikectl.freeze import freeze
from spikectl.verify import verify


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def checks(findings):
    return {f.check for f in findings}


PROCEDURE = b"1. restore the failed disk with snapraid fix\n"


def frozen_s2(root, version=1):
    write_criteria(root, doc=criteria_doc("S2", version=version, scenarios=7, review=True))
    return freeze(root, "S2")


def s2_run_with_procedure(root, entry, stamp="20261022T101500Z"):
    run = record_doc("S2", entry.version, entry.sha256, S2_OUTCOMES, stamp)
    write_record(root, run, evidence=evidence_tar({"procedure.txt": PROCEDURE}))
    return run


def procedure_evidence():
    return [{"path": "procedure.txt", "sha256": sha256_bytes(PROCEDURE)}]


def s2_with_review(root, **review_changes):
    entry = frozen_s2(root)
    run = s2_run_with_procedure(root, entry)
    review = review_doc(run, evidence=procedure_evidence())
    review.update(review_changes)
    write_review(root, review)
    return entry, run, review


def test_a_review_record_without_an_evidence_archive_passes(root):
    s2_with_review(root)

    assert verify(root, only=("criteria", "records")) == []


def test_a_failing_review_record_with_a_fallback_passes(root):
    entry = frozen_s2(root)
    run = s2_run_with_procedure(root, entry)
    write_review(root, review_doc(run, "fail", evidence=procedure_evidence()))

    assert verify(root, only=("criteria", "records")) == []


def test_a_review_record_failing_the_schema_is_detected(root):
    _, _, review = s2_with_review(root)
    del review["review"]["reviewer"]
    write_review(root, review)

    assert "record-schema" in checks(verify(root, only=("records",)))


def test_a_review_record_with_run_fields_is_detected(root):
    s2_with_review(root, verdict="PASS")

    assert "record-schema" in checks(verify(root, only=("records",)))


def test_a_failing_review_without_a_fallback_decision_is_detected(root):
    entry = frozen_s2(root)
    run = s2_run_with_procedure(root, entry)
    review = review_doc(run, "fail", evidence=procedure_evidence())
    del review["fallback_decision"]
    write_review(root, review)

    assert "record-schema" in checks(verify(root, only=("records",)))


def test_a_review_directory_not_named_after_the_record_id_is_detected(root):
    _, _, review = s2_with_review(root)
    rec_dir = root / "docs/phase0/results/S2" / review["record_id"]
    rec_dir.rename(rec_dir.with_name("review-S2-wrong-name"))

    assert "record-layout" in checks(verify(root, only=("records",)))


def test_a_review_naming_an_unlocked_pair_is_detected(root):
    s2_with_review(root, criteria_sha256="f" * 64)

    assert "record-lock" in checks(verify(root, only=("records",)))


def test_a_review_of_a_record_that_does_not_exist_is_detected(root):
    s2_with_review(root, reviewed_record="S2-20250101T000000Z-v1")

    assert "record-review" in checks(verify(root, only=("records",)))


def test_a_review_of_a_non_spike_run_record_is_detected(root):
    entry = frozen_s2(root)
    run = s2_run_with_procedure(root, entry)
    run_path = root / RESULTS / "S2" / run["record_id"] / "record.json"
    run = json.loads(run_path.read_text())
    run["kind"] = "repro"
    run["repro_runs"] = ["run-a"]
    run_path.write_text(json.dumps(run), encoding="utf-8")
    write_review(root, review_doc(run, evidence=procedure_evidence()))

    findings = verify(root, only=("records",))

    assert any(f.check == "record-review" and "S2 spike-run" in f.message for f in findings)


def test_a_review_naming_a_different_criteria_version_than_the_reviewed_record_is_detected(root):
    entry = frozen_s2(root)
    run = s2_run_with_procedure(root, entry)
    entry2 = frozen_s2(root, version=2)
    review = review_doc(run, evidence=procedure_evidence())
    review.update(
        criteria_version=2,
        criteria_sha256=entry2.sha256,
        record_id="review-S2-20261023T120000Z-v2",
    )
    write_review(root, review)

    assert "record-review" in checks(verify(root, only=("records",)))


def review_with_evidence(root, evidence):
    entry = frozen_s2(root)
    run = s2_run_with_procedure(root, entry)
    write_review(root, review_doc(run, evidence=evidence))


def test_a_review_whose_evidence_entry_is_not_in_the_reviewed_archive_is_detected(root):
    review_with_evidence(root, [{"path": "missing.txt", "sha256": "c" * 64}])

    assert "record-evidence" in checks(verify(root, only=("records",)))


def test_a_review_whose_evidence_hash_differs_from_the_archive_entry_is_detected(root):
    review_with_evidence(root, [{"path": "procedure.txt", "sha256": "d" * 64}])

    assert "record-evidence" in checks(verify(root, only=("records",)))


def test_a_review_of_an_unreadable_archive_is_detected(root):
    entry = frozen_s2(root)
    run = record_doc("S2", entry.version, entry.sha256, S2_OUTCOMES)
    write_record(root, run, evidence=b"not a tar archive")
    write_review(root, review_doc(run, evidence=procedure_evidence()))

    assert "record-evidence" in checks(verify(root, only=("records",)))


def test_a_review_committed_after_criteria_and_reviewed_record_passes(root):
    init_repo(root)
    entry = frozen_s2(root)
    commit_all(root, "docs(phase0): freeze S2 criteria")
    run = s2_run_with_procedure(root, entry)
    commit_all(root, "docs(phase0): record S2 result")
    write_review(root, review_doc(run, evidence=procedure_evidence()))
    commit_all(root, "docs(phase0): review S2.7")

    assert verify(root, only=("git",)) == []


def test_a_review_in_the_commit_of_the_reviewed_record_is_detected(root):
    init_repo(root)
    entry = frozen_s2(root)
    commit_all(root, "docs(phase0): freeze S2 criteria")
    run = s2_run_with_procedure(root, entry)
    write_review(root, review_doc(run, evidence=procedure_evidence()))
    commit_all(root, "docs(phase0): record S2 result and review")

    findings = verify(root, only=("git",))

    assert any(f.check == "ancestry" and "review" in f.message for f in findings)


def test_an_uncommitted_review_is_detected(root):
    init_repo(root)
    entry = frozen_s2(root)
    commit_all(root, "docs(phase0): freeze S2 criteria")
    run = s2_run_with_procedure(root, entry)
    commit_all(root, "docs(phase0): record S2 result")
    write_review(root, review_doc(run, evidence=procedure_evidence()))

    assert "ancestry" in checks(verify(root, only=("git",)))


def test_an_uncommitted_reviewed_record_is_reported_once_as_its_own_ancestry(root):
    init_repo(root)
    entry = frozen_s2(root)
    commit_all(root, "docs(phase0): freeze S2 criteria")
    run = s2_run_with_procedure(root, entry)
    review = write_review(root, review_doc(run, evidence=procedure_evidence()))
    git(root, "add", "--", str(review.parent.relative_to(root)))
    git(root, "commit", "-q", "-m", "docs(phase0): review S2.7")

    findings = verify(root, only=("git",))

    assert [f.check for f in findings] == ["ancestry"]
    assert f"{run['record_id']}/record.json is not committed" in findings[0].message


def test_a_review_whose_branch_lacks_the_reviewed_record_commit_is_detected(root):
    init_repo(root)
    entry = frozen_s2(root)
    commit_all(root, "docs(phase0): freeze S2 criteria")
    git(root, "branch", "review")
    run = s2_run_with_procedure(root, entry)
    commit_all(root, "docs(phase0): record S2 result")
    git(root, "checkout", "-q", "review")
    write_review(root, review_doc(run, evidence=procedure_evidence()))
    commit_all(root, "docs(phase0): review S2.7")
    git(root, "merge", "-q", "--no-edit", "main")

    findings = verify(root, only=("git",))

    assert [f.check for f in findings] == ["ancestry"]
    assert "is not an ancestor" in findings[0].message
