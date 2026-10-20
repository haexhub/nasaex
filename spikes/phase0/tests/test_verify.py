"""spikectl verify: lock integrity, record-to-lock pairing, hygiene scans and the spikes/
reference rule (the git group: test_verify_git.py)."""

import hashlib
import io
import lzma
import tarfile
import zlib

import pytest
from helpers import (
    CRITERIA,
    LOCK,
    RESULTS,
    commit_all,
    criteria_doc,
    evidence_tar,
    init_repo,
    make_root,
    record_doc,
    sha256_bytes,
    write_criteria,
    write_record,
)

from spikectl import verify as verify_module
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


def test_an_unknown_check_group_is_refused(root):
    with pytest.raises(ValueError, match="bogus"):
        verify(root, only=("criteria", "bogus"))


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


@pytest.mark.parametrize("group", ["criteria", "records"])
def test_a_malformed_lock_line_is_reported_by_the_criteria_and_records_groups(root, group):
    (root / LOCK).write_text("S1.yaml one-version\n", encoding="utf-8")

    findings = verify(root, only=(group,))

    assert [f.check for f in findings] == ["lock-format"]
    assert "frozen.lock:1" in findings[0].message


def test_a_file_locked_twice_is_detected(root):
    frozen_s1(root)
    (root / LOCK).write_text((root / LOCK).read_text(encoding="utf-8") * 2, encoding="utf-8")

    findings = verify(root, only=("criteria",))

    assert [f.check for f in findings] == ["lock-format"]
    assert "appears twice" in findings[0].message


def test_a_locked_file_whose_status_is_not_frozen_is_detected(root):
    path = write_criteria(root, status="draft")
    (root / LOCK).write_text(f"S1.yaml 1 {sha256_bytes(path.read_bytes())}\n", encoding="utf-8")

    assert checks(verify(root, only=("criteria",))) == {"lock-status"}


@pytest.mark.parametrize("name", ["S1.v1.yaml", "S1.v2.yml"])
def test_a_criteria_file_named_outside_the_scheme_is_detected(root, name):
    write_criteria(root, name=name)

    assert checks(verify(root, only=("criteria",))) == {"criteria-name"}


@pytest.mark.parametrize("text", ["spike: [S1\n", "- S1\n"], ids=["broken-yaml", "not-a-mapping"])
def test_a_criteria_file_that_does_not_load_is_detected(root, text):
    (root / CRITERIA / "S1.yaml").write_text(text, encoding="utf-8")

    assert checks(verify(root, only=("criteria",))) == {"criteria-schema"}


def test_a_criteria_file_that_is_not_utf_8_is_detected(root):
    path = write_criteria(root)
    path.write_bytes(path.read_bytes() + b"# caf\xe9\n")

    findings = verify(root, only=("criteria",))

    assert [f.check for f in findings] == ["criteria-schema"]
    assert findings[0].message.startswith("S1.yaml: ")


def test_a_criteria_file_whose_content_names_another_spike_is_detected(root):
    write_criteria(root, criteria_doc("S3"), name="S1.yaml")

    findings = verify(root, only=("criteria",))

    assert [f.check for f in findings] == ["criteria-schema"]
    assert "disagree with the name" in findings[0].message


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


def raw_record(root, data: bytes):
    """A record.json with arbitrary bytes in the place of an S1 record."""
    path = root / RESULTS / "S1" / "S1-20261022T101500Z-v1" / "record.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)


@pytest.mark.parametrize("data", [b"{not json", b"\xff\xfe"], ids=["broken-json", "not-utf-8"])
def test_a_record_that_does_not_load_is_detected(root, data):
    raw_record(root, data)

    assert checks(verify(root, only=("records",))) == {"record-schema"}


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


def truncated_evidence() -> bytes:
    """A real evidence archive cut short, as an interrupted copy leaves it: it opens, but not
    every member can be read."""
    noise = hashlib.shake_128(b"incompressible").digest(65536)
    data = evidence_tar({"driver.log": b"log\n", "core.bin": noise})
    return data[: len(data) * 3 // 4]


@pytest.mark.parametrize(
    "evidence", [b"not a tar archive", truncated_evidence()], ids=["not-a-tar", "truncated"]
)
def test_an_evidence_archive_that_is_not_a_readable_tar_is_detected(root, evidence):
    entry = frozen_s1(root)
    write_record(root, record_doc("S1", 1, entry.sha256), evidence=evidence)

    findings = verify(root, only=("records",))

    assert [f.check for f in findings] == ["record-evidence"]
    assert "not a readable tar archive" in findings[0].message


@pytest.mark.parametrize("error", [lzma.LZMAError("corrupt xz"), zlib.error("corrupt zlib")])
def test_compressed_archive_errors_are_reported_and_skipped(root, monkeypatch, error):
    entry = frozen_s1(root)
    write_record(root, record_doc("S1", 1, entry.sha256))

    def open_archive(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(verify_module.tarfile, "open", open_archive)

    findings = verify(root, only=("records",))

    assert [f.check for f in findings] == ["record-evidence"]
    assert verify(root, only=("hygiene",), forbidden_names=()) == []


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


def test_binary_files_are_skipped_by_the_text_scans(root):
    init_repo(root)
    for rel in ("docs/phase0/plot.png", "assets/logo.png"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    commit_all(root)

    assert verify(root, only=("hygiene",), forbidden_names=()) == []


def test_binary_evidence_members_are_skipped_and_the_text_members_still_scanned(root):
    init_repo(root)
    entry = frozen_s1(root)
    evidence = evidence_tar({"core.bin": b"\xff\xfe", "driver.log": b"from /home/alice/x\n"})
    write_record(root, record_doc("S1", 1, entry.sha256), evidence=evidence)
    commit_all(root)

    findings = verify(root, only=("hygiene",), forbidden_names=())

    assert [f.message.rsplit("!", 1)[1] for f in findings] == ["driver.log:1: absolute host path"]


def test_an_unreadable_evidence_archive_is_skipped_by_the_scans(root):
    init_repo(root)
    entry = frozen_s1(root)
    write_record(root, record_doc("S1", 1, entry.sha256), evidence=b"not a tar archive")
    commit_all(root)

    assert verify(root, only=("hygiene",), forbidden_names=()) == []
