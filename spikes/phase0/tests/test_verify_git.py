"""spikectl verify, git group: append-only lock history, the base ref the lock must extend, and
criteria-before-record by git ancestry; what the criteria and records groups report is left to
them."""

import pytest
from helpers import (
    LOCK,
    RESULTS,
    commit_all,
    git,
    init_repo,
    make_root,
    record_doc,
    write_criteria,
    write_record,
)

from spikectl.freeze import freeze
from spikectl.verify import verify

FAKE_HASH = "a" * 64


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def checks(findings):
    return {f.check for f in findings}


def frozen_s1(root):
    write_criteria(root, spike="S1")
    return freeze(root, "S1")


def raw_record(root, data: bytes):
    """A record.json with arbitrary bytes in the place of an S1 record."""
    path = root / RESULTS / "S1" / "S1-20261022T101500Z-v1" / "record.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)


# --- append-only lock ------------------------------------------------------


def test_lock_history_that_only_appends_is_accepted(root):
    init_repo(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    commit_all(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\nS2.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    commit_all(root)

    assert verify(root, only=("git",)) == []


def test_a_rewritten_lock_line_in_history_is_detected(root):
    init_repo(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    commit_all(root)
    (root / LOCK).write_text(f"S1.yaml 1 {'b' * 64}\n", encoding="utf-8")
    commit_all(root)

    assert "lock-append-only" in checks(verify(root, only=("git",)))


def test_a_removed_lock_line_in_the_working_tree_is_detected(root):
    init_repo(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\nS2.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    commit_all(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\n", encoding="utf-8")

    assert "lock-append-only" in checks(verify(root, only=("git",)))


def test_lock_must_extend_the_lock_of_a_base_ref(root):
    init_repo(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\nS2.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    base = commit_all(root)
    git(root, "checkout", "-q", "-b", "topic")
    (root / LOCK).write_text(f"S2.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    commit_all(root)

    findings = verify(root, only=("git",), base_ref=base)

    assert "lock-append-only" in checks(findings)


def test_a_lock_that_appends_to_the_lock_of_a_base_ref_passes(root):
    init_repo(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    base = commit_all(root)
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\nS2.yaml 1 {FAKE_HASH}\n", encoding="utf-8")
    commit_all(root)

    assert verify(root, only=("git",), base_ref=base) == []


def test_a_base_ref_that_does_not_exist_is_reported(root):
    init_repo(root)
    commit_all(root)

    findings = verify(root, only=("git",), base_ref="no-such-ref")

    assert [(f.check, f.message) for f in findings] == [
        ("lock-append-only", "base ref no-such-ref does not exist")
    ]


def test_a_broken_git_marker_is_reported_rather_than_skipping_the_history_checks(root):
    (root / ".git").write_text("gitdir: does-not-exist\n", encoding="utf-8")

    assert checks(verify(root, only=("git",))) == {"git"}


# --- criteria before record (git ancestry) ---------------------------------


def test_criteria_commit_before_record_commit_passes(root):
    init_repo(root)
    entry = frozen_s1(root)
    commit_all(root, "docs(phase0): freeze S1 criteria")
    write_record(root, record_doc("S1", 1, entry.sha256))
    commit_all(root, "docs(phase0): record S1 result")

    assert verify(root, only=("git",)) == []


def test_criteria_and_record_in_one_commit_are_detected(root):
    init_repo(root)
    entry = frozen_s1(root)
    write_record(root, record_doc("S1", 1, entry.sha256))
    commit_all(root)

    assert "ancestry" in checks(verify(root, only=("git",)))


def test_a_record_committed_before_the_lock_line_is_detected(root):
    init_repo(root)
    entry = frozen_s1(root)
    lock = (root / LOCK).read_text(encoding="utf-8")
    (root / LOCK).unlink()
    write_record(root, record_doc("S1", 1, entry.sha256))
    commit_all(root, "docs(phase0): record S1 result")
    (root / LOCK).write_text(lock, encoding="utf-8")
    commit_all(root, "docs(phase0): freeze S1 criteria")

    assert "ancestry" in checks(verify(root, only=("git",)))


def test_an_uncommitted_record_is_detected(root):
    init_repo(root)
    entry = frozen_s1(root)
    commit_all(root)
    write_record(root, record_doc("S1", 1, entry.sha256))

    assert "ancestry" in checks(verify(root, only=("git",)))


def test_a_record_whose_lock_line_is_not_committed_is_detected(root):
    init_repo(root)
    entry = frozen_s1(root)
    path = write_record(root, record_doc("S1", 1, entry.sha256))
    git(root, "add", "--", str(path.parent.relative_to(root)))
    git(root, "commit", "-q", "-m", "docs(phase0): record S1 result")

    findings = verify(root, only=("git",))

    assert [f.check for f in findings] == ["ancestry"]
    assert "lock line of S1.yaml is not committed" in findings[0].message


# The git group leaves what the criteria and records groups report to them: no crash, no
# second finding.


def test_a_malformed_lock_is_only_a_lock_format_finding(root):
    init_repo(root)
    (root / LOCK).write_text("S1.yaml one-version\n", encoding="utf-8")
    commit_all(root)

    assert checks(verify(root, forbidden_names=())) == {"lock-format"}


def test_an_unreadable_record_is_a_record_schema_finding_and_hides_no_other_ancestry(root):
    init_repo(root)
    entry = frozen_s1(root)
    commit_all(root)
    raw_record(root, b"{not json")
    commit_all(root)
    write_record(root, record_doc("S1", 1, entry.sha256, stamp="20261023T090000Z"))

    findings = verify(root, forbidden_names=())

    assert sorted((f.check, f.message.split("/")[4]) for f in findings) == [
        ("ancestry", "S1-20261023T090000Z-v1"),
        ("record-schema", "S1-20261022T101500Z-v1"),
    ]


def test_a_committed_record_of_an_unlocked_pair_is_only_a_record_lock_finding(root):
    init_repo(root)
    frozen_s1(root)
    commit_all(root)
    write_record(root, record_doc("S1", 1, FAKE_HASH))
    commit_all(root)

    assert checks(verify(root, forbidden_names=())) == {"record-lock"}
