"""The S1 data set (vm-tests/s1/fixture.py): every case the spec asks for, a populated trash
beside the branch directory of two archive disks, and nothing whose outcome depends on the
unlink policy of the cache pool (the mergerfs default `epall` or the candidate `ff`). The data
set is populated here into local directories; the file above 4 GiB is left out of that, its
description is checked instead."""

import importlib.util
import os
import re
from pathlib import Path, PurePosixPath

import pytest
from helpers import xattrs_supported

from testlib.fixtures import Fixture, populate
from testlib.manifest import build_manifest, compare

FIXTURE_PY = Path(__file__).resolve().parents[1] / "vm-tests" / "s1" / "fixture.py"
TRASH_NAME = re.compile(r"(.+)\.\d+\.\d+")  # <pool-relative path>.<ctime ns>.<pool inode>
FOUR_GIB = 2**32


def load():
    spec = importlib.util.spec_from_file_location("s1_fixture", FIXTURE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def s1():
    return load()


def topology(serials=("arch0", "arch1", "arch2")):
    return {
        "archiveLayout": [
            {
                "serial": s,
                "data": f"/mnt/archive/{s}",
                "branch": f"/mnt/archive/{s}/branch",
                "trash": f"/mnt/archive/{s}/trash",
            }
            for s in serials
        ]
    }


def entries_on(s1, branches):
    return [e for e in s1.FIXTURE.entries if e.branch in branches]


def files_by_branch(s1):
    found: dict[str, set[str]] = {}
    for e in s1.FIXTURE.entries:
        if e.kind != "dir":
            found.setdefault(e.path, set()).add(e.branch)
    return found


def dirs_of(s1, branch):
    paths = set()
    for e in entries_on(s1, {branch}):
        paths |= {p.as_posix() for p in list(PurePosixPath(e.path).parents)[:-1]}
        if e.kind == "dir":
            paths.add(e.path)
    return paths


def test_the_data_set_has_files_of_each_origin_and_directories_of_each_kind(s1):
    pool_files = {e.path for e in entries_on(s1, {s1.POOL}) if e.kind == "file"}
    archive_files = {e.path for e in entries_on(s1, set(s1.ARCHIVE)) if e.kind == "file"}
    pool_dirs = dirs_of(s1, s1.POOL)
    archive_dirs = set().union(*(dirs_of(s1, b) for b in s1.ARCHIVE))

    assert pool_files and archive_files and not pool_files & archive_files
    assert pool_dirs & archive_dirs, "a directory on the pool and on an archive branch"
    assert archive_dirs - pool_dirs, "a directory that exists only on archive disks"
    assert any(not (dirs_of(s1, b) | {e.path for e in entries_on(s1, {b})}) for b in s1.ARCHIVE)
    assert any(e.kind == "dir" for e in entries_on(s1, {s1.POOL})), "an empty pool directory"


def test_the_data_set_has_one_case_of_every_edge_case_of_the_spec(s1):
    archived = entries_on(s1, set(s1.ARCHIVE))
    names = [PurePosixPath(e.path).name for e in archived]

    assert any(e.kind == "file" and e.size == 0 for e in archived)
    assert any(len(n.encode()) == 255 for n in names)
    assert any(not n.isascii() for n in names)
    assert any(e.extents is not None for e in archived)
    assert any(e.kind == "hardlink" for e in archived)


def test_files_above_four_gib_one_archived_and_one_written_through_the_share(s1):
    archived = [e for e in entries_on(s1, set(s1.ARCHIVE)) if e.size > FOUR_GIB]
    assert len(archived) == 1 and archived[0].branch == "arch1"  # the 10 GiB archive disk
    assert s1.NEW_LARGE.size > FOUR_GIB and s1.NEW_LARGE.branch == s1.POOL
    assert s1.NEW_LARGE not in s1.FIXTURE.entries, "written through the share, not populated"


def test_no_file_lives_on_two_branches_so_no_delete_depends_on_the_unlink_policy(s1):
    shared = {path: b for path, b in files_by_branch(s1).items() if len(b) > 1}
    assert shared == {}


def test_the_trash_of_two_archive_disks_is_populated_and_hidden_from_the_share_view(s1):
    trash_disks = {s for s in s1.ARCHIVE if entries_on(s1, {s1.trash(s)})}
    view = s1.share_view(with_hashes=False)

    assert len(trash_disks) >= 2
    for serial in trash_disks:
        for entry in entries_on(s1, {s1.trash(serial)}):
            if entry.kind == "file":
                assert TRASH_NAME.fullmatch(entry.path), entry.path
            assert entry.path not in view


def test_one_trash_entry_has_the_path_of_a_file_the_check_creates_through_the_share(s1):
    trashed = [
        e
        for s in s1.ARCHIVE
        for e in entries_on(s1, {s1.trash(s)})
        if e.kind == "file" and TRASH_NAME.fullmatch(e.path).group(1) == s1.TRASHED_PATH
    ]

    assert len(trashed) == 1
    assert s1.TRASHED_PATH not in s1.share_view(with_hashes=False)
    assert s1.NEW_AT_TRASHED_PATH.path == s1.TRASHED_PATH
    assert s1.NEW_AT_TRASHED_PATH.branch == s1.POOL
    assert s1.NEW_AT_TRASHED_PATH not in s1.FIXTURE.entries


def test_roots_name_the_branch_and_the_trash_directory_of_each_archive_disk(s1):
    roots = s1.roots(topology())

    assert roots[s1.POOL] == "/mnt/pool"
    assert roots["arch1"] == "/mnt/archive/arch1/branch"
    assert roots[s1.trash("arch2")] == "/mnt/archive/arch2/trash"
    assert set(roots) == {s1.POOL, *s1.ARCHIVE, *(s1.trash(s) for s in s1.ARCHIVE)}


def test_roots_refuse_a_topology_without_the_archive_disks_of_the_data_set(s1):
    with pytest.raises(ValueError, match="arch2"):
        s1.roots(topology(("arch0", "arch1")))


def test_populating_the_data_set_reproduces_every_expected_branch_and_trash(s1, tmp_path):
    if not xattrs_supported(tmp_path):
        pytest.skip("user xattrs are not supported on the test filesystem")
    small = Fixture(s1.FIXTURE.seed, tuple(e for e in s1.FIXTURE.entries if e.size <= FOUR_GIB))
    layout = topology()
    for disk in layout["archiveLayout"]:
        disk.update({k: str(tmp_path) + disk[k] for k in ("data", "branch", "trash")})
    roots = {k: Path(v) for k, v in s1.roots(layout).items()}
    roots[s1.POOL] = tmp_path / "pool"
    for root in roots.values():
        root.mkdir(parents=True)

    populate(small, roots)

    for branch, root in roots.items():
        expected = s1.view(branch, fixture=small)
        assert compare(expected, build_manifest(root)) == [], branch
    for disk in layout["archiveLayout"]:
        assert sorted(os.listdir(disk["data"])) == ["branch", "trash"]
