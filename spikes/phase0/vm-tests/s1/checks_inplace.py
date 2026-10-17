"""Checks S1.3, S1.4 and S1.5 (task T045): in-place changes of archived files through the share.

S1.3 flips bytes of an archive-only file through the share: the client reads the expected new
content, the file on its archive disk has it under the same inode, the pool branch holds no
copy, and every other file of every branch keeps its type, size and content.

S1.4 deletes an archive-only file through the share: it is gone from the share and from every
branch, and the pool branch is unchanged (no marker, no copy). It also measures, for a file at
the same path on the pool branch and an archive branch (the state of a selected file between M1
and M5 of the mover), which branches a client delete removes and the effective `func.unlink` of
the cache pool; recorded in the evidence and the required settings, never a pass condition.

S1.5 deletes an archive file and an archive directory and recreates both through the share:
only the new content shows, before and after the client mounts the share again, and no branch
holds the old content or an old entry. Scaffolding for the spikes, not product code."""

import shlex
from collections.abc import Collection, Mapping

from s1.context import CACHE, Lab
from s1.fixture import POOL

from testlib.fixtures import Entry, Fixture, content_sha256, flipped_sha256
from testlib.manifest import Difference, Manifest, compare, describe

SEED = "S1.3-5"
NEW_SEED = "S1.5-new"

MODIFIED = Entry("s1.3/archived.bin", "arch1", size=200_000)
FLIPS = (0, 65_536, 199_999)

DELETED = Entry("s1.4/archived.txt", "arch0", size=3000)
TWO_BRANCHES = (
    Entry("s1.4/both.txt", POOL, size=2000),
    Entry("s1.4/both.txt", "arch1", size=2000),
)

OLD_FILE = Entry("s1.5/file.txt", "arch0", size=1500)
OLD_DIR = (
    Entry("s1.5/dir/a.txt", "arch0", size=100),
    Entry("s1.5/dir/sub/b.txt", "arch1", size=200),
)
NEW_FILE = Entry("s1.5/file.txt", POOL, size=900)
NEW_IN_DIR = Entry("s1.5/dir/new.txt", POOL, size=300)


def content_differences(
    before: Manifest, after: Manifest, exclude: Collection[str]
) -> list[Difference]:
    """Differences in type, size and content (and paths that appeared or vanished), outside
    the excluded paths."""
    before = {p: i for p, i in before.items() if p not in exclude}
    after = {p: i for p, i in after.items() if p not in exclude}
    return compare(before, after, ignore=("mode", "mtime", "xattrs"))


def old_content_holders(hashes: Mapping[str, str], old_sha256: str) -> list[str]:
    return [key for key, sha in hashes.items() if sha == old_sha256]


def _branch_manifests(lab: Lab) -> dict[str, Manifest]:
    return {key: lab.manifest(key) for key in lab.share_branches}


def check_inplace_modify(lab: Lab) -> None:
    lab.place(Fixture(SEED, (MODIFIED,)))
    before = _branch_manifests(lab)
    inode = lab.inode(MODIFIED.branch, MODIFIED.path)
    lab.flip_through(lab.share, MODIFIED.path, FLIPS)

    expected = flipped_sha256(SEED, MODIFIED, FLIPS)
    seen = {
        "share": lab.share.sha256(MODIFIED.path),
        "branch": lab.sha256(MODIFIED.branch, MODIFIED.path),
        "inode_before": inode,
        "inode_after": lab.inode(MODIFIED.branch, MODIFIED.path),
        "holders": lab.holders(MODIFIED.path),
    }
    failures = [
        f"{where} reads other content" for where in ("share", "branch") if seen[where] != expected
    ]
    if seen["inode_after"] != inode:
        failures.append("the archived file was replaced, not changed in place")
    if seen["holders"] != [MODIFIED.branch]:
        failures.append(f"the modified file lies on {seen['holders']}")
    after = _branch_manifests(lab)
    others = {key: content_differences(before[key], after[key], (MODIFIED.path,)) for key in before}
    failures += [f"{key} changed: {describe(d)}" for key, d in others.items() if d]
    lab.evidence(
        "S1.3-inplace-modify",
        {
            "expected_sha256": expected,
            **seen,
            "other_files": {k: describe(d) for k, d in others.items()},
        },
    )
    assert not failures, "; ".join(failures)


def _unlink_measurement(lab: Lab) -> dict:
    """Which branches a client delete removes for a file on the pool and an archive branch."""
    path = TWO_BRANCHES[0].path
    lab.place(Fixture(SEED, TWO_BRANCHES))
    measured: dict = {"path": path, "holders_before": lab.holders(path)}
    measured["delete_status"] = lab.client_run(f"rm -- {shlex.quote(lab.share.path(path))}")
    measured["holders_after"] = lab.holders(path)
    measured["visible_after"] = {
        "share": lab.share.exists(path),
        "second_session": lab.second.exists(path),
        "cache_pool_on_server": lab.server.execute(f"test -e {CACHE}/{path}")[0] == 0,
    }
    measured["effective_func_unlink"] = lab.effective("func.unlink")
    measured["configured_options"] = lab.cache_unit("Options")
    return measured


def check_inplace_delete(lab: Lab) -> None:
    lab.place(Fixture(SEED, (DELETED,)))
    pool_before = lab.manifest(POOL)
    status, output = lab.client_run(f"rm -- {shlex.quote(lab.share.path(DELETED.path))}")
    seen = {
        "delete": [status, output],
        "visible": lab.share.exists(DELETED.path),
        "holders": lab.holders(DELETED.path),
    }
    pool_changes = compare(pool_before, lab.manifest(POOL))
    failures = []
    if status != 0:
        failures.append(f"the delete failed: {output.strip()}")
    if seen["visible"]:
        failures.append("the deleted file is still visible through the share")
    if seen["holders"]:
        failures.append(f"the deleted file still lies on {seen['holders']}")
    if pool_changes:
        failures.append(f"the pool branch changed: {describe(pool_changes)}")

    try:
        measurement = _unlink_measurement(lab)
    except Exception as error:  # noqa: BLE001 - a measurement, recorded and never judged
        measurement = {"error": f"{type(error).__name__}: {error}"}
    lab.required_settings["unlink_policy"] = {
        "func.unlink": measurement.get("effective_func_unlink"),
        "configured": "no func.unlink option (the mergerfs default)",
        "found_by": "S1.4 (measured, not a pass condition)",
        "measurement": measurement,
    }
    lab.evidence(
        "S1.4-inplace-delete",
        {**seen, "pool_changes": describe(pool_changes), "two_branch_delete": measurement},
    )
    assert not failures, "; ".join(failures)


def _observe_recreated(lab: Lab, new_sha: str) -> list[str]:
    failures = []
    if lab.share.sha256(NEW_FILE.path) != new_sha:
        failures.append("the recreated file does not show the new content")
    listing = lab.share.listdir("s1.5/dir")
    if listing != ["new.txt"]:
        failures.append(f"the recreated directory lists {listing}")
    return failures


def check_delete_then_recreate(lab: Lab) -> None:
    lab.place(Fixture(SEED, (OLD_FILE, *OLD_DIR)))
    old_sha = content_sha256(SEED, OLD_FILE)
    new_sha = content_sha256(NEW_SEED, NEW_FILE)
    file_path, dir_path = lab.share.path(NEW_FILE.path), lab.share.path("s1.5/dir")
    steps = {
        "delete file": lab.client_run(f"rm -- {shlex.quote(file_path)}"),
        "delete directory": lab.client_run(f"rm -r -- {shlex.quote(dir_path)}"),
        "create directory": lab.client_run(f"mkdir -- {shlex.quote(dir_path)}"),
    }
    failures = [f"{step} failed: {out.strip()}" for step, (code, out) in steps.items() if code]
    lab.write_through(lab.share, NEW_FILE.path, NEW_SEED, NEW_FILE)
    lab.write_through(lab.share, NEW_IN_DIR.path, NEW_SEED, NEW_IN_DIR)

    failures += _observe_recreated(lab, new_sha)
    lab.remount_clients()
    failures += [f"after a new mount: {f}" for f in _observe_recreated(lab, new_sha)]
    hashes = {key: lab.sha256(key, NEW_FILE.path) for key in lab.holders(NEW_FILE.path)}
    stale = old_content_holders(hashes, old_sha)
    if stale:
        failures.append(f"the old file content is still on {stale}")
    old_entries = {e.path: lab.holders(e.path) for e in OLD_DIR} | {
        "s1.5/dir/sub": lab.holders("s1.5/dir/sub")
    }
    failures += [f"old entry {p} still on {where}" for p, where in old_entries.items() if where]
    lab.evidence(
        "S1.5-delete-then-recreate",
        {"steps": steps, "file_holders": hashes, "old_entries": old_entries, "failures": failures},
    )
    assert not failures, "; ".join(failures)
