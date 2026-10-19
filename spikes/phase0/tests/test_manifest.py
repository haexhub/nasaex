"""Manifest building and comparison: names, sizes, mtimes, modes, xattrs, SHA-256, tree hash;
differences are reported and never repaired."""

import os

import pytest

from testlib.manifest import (
    Difference,
    Item,
    build_manifest,
    compare,
    describe,
    from_json,
    subtree,
    to_json,
    tree_hash,
)


def make_tree(root):
    (root / "d").mkdir()
    (root / "d" / "a.txt").write_bytes(b"alpha")
    (root / "b.txt").write_bytes(b"")
    os.utime(root / "d" / "a.txt", (1_700_000_000, 1_700_000_000))
    os.utime(root / "b.txt", (1_700_000_100, 1_700_000_100))
    os.chmod(root / "d" / "a.txt", 0o640)


@pytest.fixture
def tree(tmp_path):
    make_tree(tmp_path)
    return tmp_path


def test_a_skipped_path_and_everything_below_it_are_left_out(tree):
    (tree / "d" / "sub").mkdir()
    (tree / "d" / "sub" / "c.txt").write_bytes(b"gamma")

    manifest = build_manifest(tree, skip=("b.txt", "d/sub"))

    assert set(manifest) == {"d", "d/a.txt"}


def test_build_manifest_records_every_comparison_field(tree):
    manifest = build_manifest(tree)

    assert set(manifest) == {"d", "d/a.txt", "b.txt"}
    item = manifest["d/a.txt"]
    assert (item.kind, item.size, item.mode, item.mtime) == ("file", 5, 0o640, 1_700_000_000)
    assert item.sha256 == "8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    assert manifest["d"].kind == "dir"


def test_identical_trees_compare_equal(tree):
    assert compare(build_manifest(tree), build_manifest(tree)) == []


def test_each_kind_of_difference_is_reported(tree):
    expected = build_manifest(tree)
    (tree / "d" / "a.txt").write_bytes(b"alphX")  # same size, other bytes
    os.utime(tree / "d" / "a.txt", (1_700_000_000, 1_700_000_000))
    os.chmod(tree / "b.txt", 0o600)
    (tree / "extra.txt").write_bytes(b"x")
    observed = build_manifest(tree)

    diffs = {(d.path, d.kind, d.field) for d in compare(expected, observed)}

    assert diffs == {
        ("d/a.txt", "mismatch", "sha256"),
        ("b.txt", "mismatch", "mode"),
        ("extra.txt", "unexpected", None),
    }


def test_a_missing_entry_is_reported(tree):
    expected = build_manifest(tree)
    (tree / "b.txt").unlink()

    diffs = compare(expected, build_manifest(tree))

    assert [(d.path, d.kind) for d in diffs] == [("b.txt", "missing")]


def test_size_and_mtime_differences_are_reported(tree):
    expected = build_manifest(tree)
    (tree / "d" / "a.txt").write_bytes(b"alpha-longer")
    os.utime(tree / "d" / "a.txt", (1_700_000_555, 1_700_000_555))

    fields = {d.field for d in compare(expected, build_manifest(tree))}

    assert {"size", "mtime", "sha256"} <= fields


def test_xattr_differences_are_reported(tree):
    try:
        expected = build_manifest(tree)
        os.setxattr(tree / "d" / "a.txt", "user.tag", b"1")
    except OSError:
        pytest.skip("user xattrs are not supported on the test filesystem")

    diffs = compare(expected, build_manifest(tree))

    assert [(d.path, d.field) for d in diffs] == [("d/a.txt", "xattrs")]


def test_ignored_fields_are_not_compared(tree):
    expected = build_manifest(tree)
    os.utime(tree / "b.txt", (1_700_009_999, 1_700_009_999))

    assert compare(expected, build_manifest(tree), ignore=("mtime",)) == []
    assert compare(expected, build_manifest(tree)) != []


def test_compare_never_repairs(tree):
    expected = build_manifest(tree)
    (tree / "d" / "a.txt").write_bytes(b"changed")
    before = build_manifest(tree)

    compare(expected, before)

    assert build_manifest(tree) == before
    assert (tree / "d" / "a.txt").read_bytes() == b"changed"


def test_tree_hash_changes_with_any_difference_and_ignores_directory_mtimes(tree):
    base = tree_hash(build_manifest(tree))

    assert tree_hash(build_manifest(tree)) == base
    os.utime(tree / "d", (1_600_000_000, 1_600_000_000))
    assert tree_hash(build_manifest(tree)) == base

    (tree / "new").write_bytes(b"")
    assert tree_hash(build_manifest(tree)) != base


def test_manifest_round_trips_through_json(tree):
    manifest = build_manifest(tree)

    assert from_json(to_json(manifest)) == manifest


def test_item_equality_is_by_value():
    a = Item(kind="file", size=1, mode=0o644, mtime=5, xattrs=(), sha256="a" * 64)

    assert a == Item(kind="file", size=1, mode=0o644, mtime=5, xattrs=(), sha256="a" * 64)


def test_describe_names_each_difference_in_one_line_with_modes_in_octal():
    differences = [
        Difference("a.txt", "missing"),
        Difference("b.txt", "unexpected"),
        Difference("c.txt", "mismatch", "mode", 0o644, 0o755),
        Difference("c.txt", "mismatch", "size", 1, 2),
    ]

    assert describe(differences) == (
        "a.txt missing, b.txt unexpected, c.txt mode 0o644 -> 0o755, c.txt size 1 -> 2"
    )


def test_describe_cuts_a_long_list_and_counts_the_rest():
    differences = [Difference(f"{n}.txt", "missing") for n in range(5)]

    assert describe(differences, limit=2) == "0.txt missing, 1.txt missing (+3 more)"
    assert describe([]) == ""


def test_subtree_keys_the_entries_below_a_directory_relative_to_it():
    item = Item("file", 1, 0o644, 0, (), "c" * 64)
    manifest = {"a": item, "a/b": item, "a/b/c.txt": item, "ab/d.txt": item}

    assert subtree(manifest, "a") == {"b": item, "b/c.txt": item}
    assert subtree(manifest, "") == manifest
    assert subtree(manifest, "missing") == {}


def test_a_symlink_is_recorded_as_other_and_never_followed(tree, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_bytes(b"secret")
    (tree / "link").symlink_to(outside)

    manifest = build_manifest(tree)

    assert manifest["link"].kind == "other"
    assert (manifest["link"].size, manifest["link"].sha256) == (None, None)
    assert not any(path.startswith("link/") for path in manifest)
