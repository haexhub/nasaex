"""spikectl verify: lock integrity, append-only lock, record-to-lock pairing,
criteria-before-record by git ancestry, hygiene scans and the spikes/ reference rule."""

import io
import tarfile

import pytest
from helpers import (
    LOCK,
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


def test_a_tree_without_criteria_or_records_is_clean(root):
    init_repo(root)
    commit_all(root)

    assert verify(root) == []


# --- lock integrity --------------------------------------------------------


def test_a_locked_file_with_matching_hash_passes(root):
    frozen_s1(root)

    assert verify(root, only=("criteria",)) == []


def test_editing_a_locked_file_is_detected(root):
    frozen_s1(root)
    path = root / "docs/phase0/criteria/S1.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# late edit\n", encoding="utf-8")

    assert "lock-hash" in checks(verify(root, only=("criteria",)))


def test_a_lock_line_without_its_file_is_detected(root):
    (root / LOCK).write_text(f"S1.yaml 1 {FAKE_HASH}\n", encoding="utf-8")

    assert "lock-hash" in checks(verify(root, only=("criteria",)))


def test_a_file_claiming_frozen_without_a_lock_line_is_detected(root):
    write_criteria(root, status="frozen")

    assert "lock-missing" in checks(verify(root, only=("criteria",)))


def test_a_draft_file_without_a_lock_line_is_fine(root):
    write_criteria(root, status="draft")

    assert verify(root, only=("criteria",)) == []


def test_a_criteria_file_failing_the_schema_is_detected(root):
    doc = {"spike": "S1", "criteria_version": 1, "status": "draft"}
    write_criteria(root, doc, name="S1.yaml")

    assert "criteria-schema" in checks(verify(root, only=("criteria",)))


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


# --- records ---------------------------------------------------------------


def test_a_record_naming_a_locked_version_and_hash_passes(root):
    entry = frozen_s1(root)
    write_record(root, record_doc("S1", 1, entry.sha256))

    assert verify(root, only=("criteria", "records")) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"criteria_sha256": FAKE_HASH}, "record-lock"),
        ({"criteria_version": 2}, "record-lock"),
    ],
)
def test_a_record_naming_an_unlocked_pair_is_detected(root, mutation, expected):
    entry = frozen_s1(root)
    doc = record_doc("S1", 1, entry.sha256)
    doc.update(mutation)
    doc["record_id"] = f"S1-20261022T101500Z-v{doc['criteria_version']}"
    write_record(root, doc)

    assert expected in checks(verify(root, only=("records",)))


def test_a_record_for_a_spike_without_a_lock_line_is_detected(root):
    write_record(root, record_doc("S2", 1, FAKE_HASH))

    assert "record-lock" in checks(verify(root, only=("records",)))


def test_a_record_failing_the_schema_is_detected(root):
    entry = frozen_s1(root)
    doc = record_doc("S1", 1, entry.sha256)
    del doc["residual_risk"]
    write_record(root, doc)

    assert "record-schema" in checks(verify(root, only=("records",)))


def test_a_fail_record_without_a_fallback_decision_is_detected(root):
    entry = frozen_s1(root)
    doc = record_doc("S1", 1, entry.sha256, {"S1.1": "pass", "S1.2": "fail"})
    del doc["fallback_decision"]
    write_record(root, doc)

    assert "record-schema" in checks(verify(root, only=("records",)))


def test_a_record_directory_not_named_after_the_record_id_is_detected(root):
    entry = frozen_s1(root)
    path = write_record(root, record_doc("S1", 1, entry.sha256))
    path.parent.rename(path.parent.with_name("S1-wrong-name"))

    assert "record-layout" in checks(verify(root, only=("records",)))


def test_an_evidence_archive_that_differs_from_its_recorded_hash_is_detected(root):
    entry = frozen_s1(root)
    path = write_record(root, record_doc("S1", 1, entry.sha256))
    (path.parent / "evidence.tar.xz").write_bytes(b"tampered")

    assert "record-evidence" in checks(verify(root, only=("records",)))


def test_x1_records_are_never_required(root):
    write_criteria(root, spike="X1")
    freeze(root, "X1")

    assert verify(root) == []


def test_an_x1_record_that_claims_to_gate_is_detected(root):
    write_criteria(root, spike="X1")
    entry = freeze(root, "X1")
    doc = record_doc("X1", 1, entry.sha256)
    doc["gating"] = True
    write_record(root, doc)

    assert "record-schema" in checks(verify(root, only=("records",)))


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


# --- hygiene scans ---------------------------------------------------------


def write_doc(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "see /home/alice/project/file",
        "see /Users/bob/Documents",
        "see C:\\Users\\carol\\file",
        "see ~/projects/nasaex",
    ],
)
def test_absolute_host_paths_in_docs_are_detected(root, text):
    init_repo(root)
    write_doc(root, "docs/adr/0100-x.md", text + "\n")
    commit_all(root)

    assert "host-paths" in checks(verify(root, only=("hygiene",)))


def test_host_paths_in_specs_are_detected_too(root):
    init_repo(root)
    write_doc(root, "specs/001-x/plan.md", "/home/alice/x\n")
    commit_all(root)

    assert "host-paths" in checks(verify(root, only=("hygiene",)))


def test_forbidden_user_and_host_names_are_detected_as_words(root):
    init_repo(root)
    write_doc(root, "docs/notes.md", "ran on alice-laptop as mallory\n")
    commit_all(root)

    findings = verify(root, only=("hygiene",), forbidden_names=("alice-laptop", "mallory"))

    assert len([f for f in findings if f.check == "host-paths"]) == 2


def test_a_forbidden_name_inside_a_longer_word_is_not_a_hit(root):
    init_repo(root)
    write_doc(root, "docs/notes.md", "malloryville\n")
    commit_all(root)

    assert verify(root, only=("hygiene",), forbidden_names=("mallory",)) == []


def test_a_forbidden_name_inside_a_hyphenated_name_is_not_a_hit(root):
    init_repo(root)
    write_doc(root, "docs/notes.md", "uses @mallory-space/ui\n")
    commit_all(root)

    assert verify(root, only=("hygiene",), forbidden_names=("mallory",)) == []


def test_guest_paths_survive_the_scan(root):
    init_repo(root)
    write_doc(root, "docs/notes.md", "files live in /mnt/archive/disk0 and /var/lib/snapraid\n")
    commit_all(root)

    assert verify(root, only=("hygiene",)) == []


def test_host_path_inside_an_evidence_archive_is_detected(root):
    init_repo(root)
    entry = frozen_s1(root)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        data = b"log line from /home/alice/work\n"
        info = tarfile.TarInfo("driver.log")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    write_record(root, record_doc("S1", 1, entry.sha256), evidence=buffer.getvalue())
    commit_all(root)

    assert "host-paths" in checks(verify(root, only=("hygiene",)))


@pytest.mark.parametrize(
    "text",
    ["Generated with Claude Code", "Co-Authored-By: someone", "written by an Anthropic model"],
)
def test_authoring_tool_self_references_are_detected(root, text):
    init_repo(root)
    write_doc(root, "docs/phase0/notes.md", text + "\n")
    commit_all(root)

    assert "self-references" in checks(verify(root, only=("hygiene",)))


def test_ordinary_prose_is_not_a_self_reference(root):
    init_repo(root)
    write_doc(root, "docs/notes.md", "An agent-neutral procedure; the claude of a clock.\n")
    commit_all(root)

    assert verify(root, only=("hygiene",)) == []


@pytest.mark.parametrize(
    "rel", ["src/app.py", "backend/main.go", ".github/workflows/ci.yml", "flake.nix"]
)
def test_a_file_outside_spikes_and_docs_referencing_spikes_is_detected(root, rel):
    init_repo(root)
    write_doc(root, rel, "import from spikes/phase0/testlib\n")
    commit_all(root)

    assert "spikes-references" in checks(verify(root, only=("hygiene",)))


@pytest.mark.parametrize(
    "rel",
    [
        "spikes/phase0/README.txt",
        "docs/phase0/notes.md",
        "specs/001-x/plan.md",
        ".github/workflows/phase0.yml",
    ],
)
def test_references_to_spikes_from_allowed_locations_pass(root, rel):
    init_repo(root)
    write_doc(root, rel, "see spikes/phase0/testlib\n")
    commit_all(root)

    assert verify(root, only=("hygiene",)) == []


def test_hygiene_works_without_git_by_walking_the_tree(root):
    write_doc(root, "docs/notes.md", "see /home/alice/x\n")

    assert "host-paths" in checks(verify(root, only=("hygiene",)))
