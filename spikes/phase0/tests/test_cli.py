"""spikectl command line: exit status and output of freeze, verify and verdict, and the repository
root found from the current directory (the run verb: test_run_single.py)."""

import pytest
from helpers import (
    commit_all,
    init_repo,
    lock_lines,
    make_root,
    record_doc,
    write_criteria,
    write_record,
)

from spikectl import cli
from spikectl.freeze import freeze
from spikectl.model import find_root


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def test_freeze_prints_the_new_lock_line_and_exits_zero(root, capsys):
    write_criteria(root)

    assert cli.main(["--root", str(root), "freeze", "S1"]) == 0
    assert capsys.readouterr().out == f"froze {lock_lines(root)[0]}\n"


def test_a_refused_freeze_exits_one_with_the_reason_on_stderr(root, capsys):
    assert cli.main(["--root", str(root), "freeze", "S1"]) == 1
    assert capsys.readouterr().err.startswith("freeze: no criteria file for S1")


def test_verify_prints_each_finding_and_exits_one(root, capsys):
    init_repo(root)
    (root / "docs/notes.md").write_text("ran as mallory\n", encoding="utf-8")
    commit_all(root)

    status = cli.main(["--root", str(root), "verify", "--only", "hygiene", "--forbid", "mallory"])

    assert status == 1
    assert capsys.readouterr().out == (
        "host-paths: docs/notes.md:1: user or host name\nverify: 1 finding(s)\n"
    )


def test_a_clean_verify_exits_zero(root, capsys):
    init_repo(root)
    commit_all(root)

    assert cli.main(["--root", str(root), "verify", "--forbid", "mallory"]) == 0
    assert capsys.readouterr().out == "verify: ok\n"


def test_verdict_prints_the_matrix_and_exits_zero_without_errors(root, capsys):
    assert cli.main(["--root", str(root), "verdict"]) == 0
    assert "ADR-0002 eligible: no" in capsys.readouterr().out


def test_verdict_exits_one_when_a_record_carries_an_error(root, capsys):
    write_criteria(root)
    entry = freeze(root, "S1")
    write_record(root, {**record_doc("S1", 1, entry.sha256), "verdict": "FAIL"})

    assert cli.main(["--root", str(root), "verdict"]) == 1
    assert "ERROR S1: S1-20261022T101500Z-v1: typed verdict 'FAIL'" in capsys.readouterr().out


def test_without_root_the_repository_root_is_found_from_the_current_directory(
    root, monkeypatch, capsys
):
    init_repo(root)
    write_criteria(root)
    nested = root / "spikes/phase0"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert cli.main(["freeze", "S1"]) == 0
    assert [line.split()[0] for line in lock_lines(root)] == ["S1.yaml"]


def test_outside_a_git_work_tree_the_start_directory_is_the_root(tmp_path):
    assert find_root(tmp_path) == tmp_path
