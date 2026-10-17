"""Check S1.2 (vm-tests/s1/checks_placement.py): the decisions behind it. Creating files through
the share and remounting the cache pool need the VMs."""

from s1.checks_placement import NEW, changed_branches, placement_failures

from testlib.fixtures import Entry


def test_a_branch_whose_tree_hash_changed_is_named():
    before = {"arch0": "a", "arch1": "b", "arch2": "c"}

    assert changed_branches(before, dict(before)) == []
    assert changed_branches(before, {**before, "arch1": "x"}) == ["arch1"]


def test_every_new_path_must_lie_on_the_pool_branch_alone():
    holders = {"n/a.txt": ["pool"], "n/b.txt": ["pool", "arch0"], "n/c": [], "n/d": ["arch2"]}

    assert placement_failures(holders) == [
        "n/b.txt lies on ['pool', 'arch0']",
        "n/c lies on []",
        "n/d lies on ['arch2']",
    ]
    assert placement_failures({"n/a.txt": ["pool"]}) == []


def test_new_data_goes_into_the_archive_only_directory_and_a_fresh_one():
    paths = {entry.path for entry in NEW}

    assert all(isinstance(entry, Entry) and entry.branch == "pool" for entry in NEW)
    assert {"archive-dir/new.txt", "archive-dir/newdir", "fresh", "fresh/top.txt"} <= paths
    assert any(entry.kind == "dir" for entry in NEW)
