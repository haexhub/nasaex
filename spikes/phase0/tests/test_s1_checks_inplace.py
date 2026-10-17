"""Checks S1.3, S1.4 and S1.5 (vm-tests/s1/checks_inplace.py): the decisions behind them, over
fabricated manifests. Acting through the share needs the VMs."""

from s1.checks_inplace import content_differences, old_content_holders

from testlib.manifest import Difference, Item


def item(sha, mode=0o644, mtime=1, xattrs=()):
    return Item("file", 3, mode, mtime, xattrs, sha * 64)


def test_only_type_size_and_content_count_for_the_other_files():
    before = {"a.txt": item("a"), "b.txt": item("b")}
    after = {"a.txt": item("a", mode=0o600, mtime=2, xattrs=(("user.DOSATTRIB", "00"),))}
    after["b.txt"] = item("b")

    assert content_differences(before, after, exclude=()) == []


def test_a_changed_or_new_file_is_reported_unless_it_is_the_one_modified():
    before = {"a.txt": item("a"), "mod.bin": item("m")}
    after = {"a.txt": item("x"), "mod.bin": item("n"), "copy.bin": item("m")}

    assert content_differences(before, after, exclude=("mod.bin",)) == [
        Difference("a.txt", "mismatch", "sha256", "a" * 64, "x" * 64),
        Difference("copy.bin", "unexpected"),
    ]


def test_a_branch_that_still_holds_the_old_content_is_named():
    hashes = {"pool": "new", "arch0": "old"}

    assert old_content_holders(hashes, "old") == ["arch0"]
    assert old_content_holders({"pool": "new"}, "old") == []
