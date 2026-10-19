"""spikectl freeze: lock line, schema gate, single freeze, version-file naming."""

import pytest
from helpers import (
    CRITERIA,
    LOCK,
    criteria_doc,
    lock_lines,
    make_root,
    sha256_bytes,
    write_criteria,
)

from spikectl.freeze import FreezeError, freeze
from spikectl.model import LockEntry, criteria_filename, parse_criteria_filename, parse_lock


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def test_freeze_appends_exactly_one_line_with_sha256_of_the_file_bytes(root):
    path = write_criteria(root)

    entry = freeze(root, "S1")

    lines = lock_lines(root)
    assert lines == [f"S1.yaml 1 {sha256_bytes(path.read_bytes())}"]
    assert (entry.file, entry.version, entry.sha256) == ("S1.yaml", 1, lines[0].split()[2])


def test_freeze_marks_the_file_frozen_and_keeps_comments(root):
    path = write_criteria(root)

    freeze(root, "S1")

    text = path.read_text(encoding="utf-8")
    assert "status: frozen" in text
    assert "status: draft" not in text
    assert text.startswith("# test criteria\n")


def test_freeze_never_rewrites_an_earlier_line(root):
    write_criteria(root, spike="S1")
    write_criteria(root, doc=criteria_doc("S2", scenarios=7, review=True))
    freeze(root, "S1")
    before = (root / LOCK).read_bytes()

    freeze(root, "S2")

    after = (root / LOCK).read_bytes()
    assert after.startswith(before)
    assert len(lock_lines(root)) == 2


def test_freeze_refuses_a_file_that_fails_the_schema(root):
    doc = criteria_doc()
    del doc["fallback"]  # required for gating spikes S1 to S5
    path = write_criteria(root, doc)
    original = path.read_bytes()

    with pytest.raises(FreezeError, match="schema"):
        freeze(root, "S1")

    assert path.read_bytes() == original
    assert lock_lines(root) == []


def test_freeze_refuses_to_freeze_twice(root):
    write_criteria(root)
    freeze(root, "S1")
    before = (root / LOCK).read_bytes()

    with pytest.raises(FreezeError, match="already frozen"):
        freeze(root, "S1")

    assert (root / LOCK).read_bytes() == before


def test_freeze_refuses_a_file_that_claims_frozen_without_a_lock_line(root):
    write_criteria(root, status="frozen")

    with pytest.raises(FreezeError, match="draft"):
        freeze(root, "S1")

    assert lock_lines(root) == []


def test_freeze_without_a_criteria_file_or_with_an_unknown_spike_fails(root):
    with pytest.raises(FreezeError, match="no criteria file"):
        freeze(root, "S1")
    with pytest.raises(FreezeError, match="unknown spike"):
        freeze(root, "S9")


def test_version_file_naming():
    assert criteria_filename("S1", 1) == "S1.yaml"
    assert criteria_filename("S3", 2) == "S3.v2.yaml"
    assert parse_criteria_filename("S1.yaml") == ("S1", 1)
    assert parse_criteria_filename("X1.v4.yaml") == ("X1", 4)
    assert parse_criteria_filename("S1.v1.yaml") is None  # version 1 is S1.yaml
    assert parse_criteria_filename("S1.v2.yml") is None
    assert parse_criteria_filename("frozen.lock") is None


def test_blank_lines_in_the_lock_are_not_entries():
    sha = "a" * 64

    entries = parse_lock(f"S1.yaml 1 {sha}\n\nS2.yaml 1 {sha}\n")

    assert entries == [LockEntry("S1.yaml", 1, sha), LockEntry("S2.yaml", 1, sha)]


def test_amendment_is_frozen_as_its_own_version_file(root):
    write_criteria(root, version=1)
    freeze(root, "S1")
    write_criteria(root, version=2)

    entry = freeze(root, "S1")

    assert (entry.file, entry.version) == ("S1.v2.yaml", 2)
    assert [line.split()[:2] for line in lock_lines(root)] == [
        ["S1.yaml", "1"],
        ["S1.v2.yaml", "2"],
    ]


def test_freeze_refuses_a_file_whose_name_and_version_disagree(root):
    write_criteria(root, criteria_doc(version=2), name="S1.yaml")

    with pytest.raises(FreezeError, match="version"):
        freeze(root, "S1")


def test_freeze_refuses_an_amendment_while_the_earlier_version_is_not_frozen(root):
    write_criteria(root, version=2)

    with pytest.raises(FreezeError, match="earlier version"):
        freeze(root, "S1")
    assert lock_lines(root) == []


def test_freeze_refuses_while_the_lock_is_malformed(root):
    path = write_criteria(root)
    original = path.read_bytes()
    (root / LOCK).write_text("S1.yaml one-version\n", encoding="utf-8")

    with pytest.raises(FreezeError, match="frozen.lock:1"):
        freeze(root, "S1")

    assert path.read_bytes() == original
    assert lock_lines(root) == ["S1.yaml one-version"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [("spike: [S1\n", "not valid YAML"), ("- S1\n", "must be a mapping")],
    ids=["broken-yaml", "not-a-mapping"],
)
def test_freeze_refuses_a_criteria_file_that_does_not_load(root, text, reason):
    (root / CRITERIA / "S1.yaml").write_text(text, encoding="utf-8")

    with pytest.raises(FreezeError, match=reason):
        freeze(root, "S1")

    assert lock_lines(root) == []


def test_freeze_refuses_a_status_line_it_cannot_rewrite_to_frozen(root):
    path = write_criteria(root)
    quoted = path.read_text(encoding="utf-8").replace("status: draft", "status: 'draft'")
    path.write_text(quoted, encoding="utf-8")

    with pytest.raises(FreezeError, match="exactly one top-level 'status: draft' line"):
        freeze(root, "S1")

    assert path.read_text(encoding="utf-8") == quoted
    assert lock_lines(root) == []


def test_freeze_refuses_to_append_to_a_lock_without_a_final_newline(root):
    write_criteria(root, spike="S1")
    freeze(root, "S1")
    (root / LOCK).write_text(lock_lines(root)[0], encoding="utf-8")
    before = (root / LOCK).read_bytes()
    path = write_criteria(root, spike="S3")
    original = path.read_bytes()

    with pytest.raises(FreezeError, match="newline"):
        freeze(root, "S3")

    assert (root / LOCK).read_bytes() == before
    assert path.read_bytes() == original


def test_an_amendment_date_written_as_an_unquoted_yaml_date_is_frozen(root):
    write_criteria(root, version=1)
    freeze(root, "S1")
    path = write_criteria(root, version=2)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("date: '2026-10-15'", "date: 2026-10-15"), encoding="utf-8")
    assert "date: 2026-10-15\n" in path.read_text(encoding="utf-8")

    assert freeze(root, "S1").file == "S1.v2.yaml"
