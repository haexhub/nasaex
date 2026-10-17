"""Check S1.2 (task T044): new files and directories land on the pool branch only (FR-014).

Per mount variant (the baseline create policy `ff` gates, `lus` is informational) the case
places a directory that exists only on the archive disk arch1, then the client creates a file
and a directory with a file inside it and a fresh top-level directory with a file, all through
the share. Every new path must lie on the pool branch alone, the tree hash of every archive
branch must be the same before and after, and the share must show the archive-only and the new
content as one tree (type, size, content; the mode and mtime of a new file are the client's).

After that, with the no-create mode removed from the archive branches, the client creates one
more file inside the archive-only directory: the branch that takes it is recorded as the
evidence of why NC is required, never judged. The required settings (create policy and archive
branch mode) are recorded from the gating case. Scaffolding for the spikes, not product code."""

from collections.abc import Mapping

from s1.context import BASELINE_ARCHIVE_MODE, Lab, Variant
from s1.fixture import POOL

from testlib.fixtures import Entry, Fixture, expected_manifest
from testlib.manifest import compare, describe, subtree

SEED = "S1.2"
OLD = (Entry("archive-dir/old.txt", "arch1", size=500),)
NEW = (
    Entry("archive-dir/new.txt", POOL, size=600),
    Entry("archive-dir/newdir", POOL, kind="dir"),
    Entry("archive-dir/newdir/inner.txt", POOL, size=700),
    Entry("fresh", POOL, kind="dir"),
    Entry("fresh/top.txt", POOL, size=800),
)
WITHOUT_NC = Entry("archive-dir/without-nc.txt", POOL, size=100)


def changed_branches(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return [key for key in before if after.get(key) != before[key]]


def placement_failures(holders: Mapping[str, list[str]]) -> list[str]:
    return [f"{path} lies on {where}" for path, where in holders.items() if where != [POOL]]


def _under(prefix: str, entries: tuple[Entry, ...]) -> tuple[Entry, ...]:
    return tuple(
        Entry(f"{prefix}/{e.path}", e.branch, e.kind, e.size, e.mode, e.mtime) for e in entries
    )


def _create(lab: Lab, entry: Entry) -> None:
    if entry.kind == "dir":
        lab.client.succeed(f"mkdir {lab.share.path(entry.path)}")
    else:
        lab.write_through(lab.share, entry.path, SEED, entry)


def check_new_data_placement(lab: Lab, variant: Variant) -> None:
    prefix = f"s1.2/{variant.name}"
    old, new = _under(prefix, OLD), _under(prefix, NEW)
    lab.place(Fixture(SEED, old))
    failures: list[str] = []
    with lab.variant(variant.overrides):
        before = lab.tree_hashes()
        for entry in new:
            _create(lab, entry)
        after = lab.tree_hashes()
        holders = {entry.path: lab.holders(entry.path) for entry in new}
        view = lab.share.manifest(prefix)
    failures += placement_failures(holders)
    failures += [f"archive branch {key} changed" for key in changed_branches(before, after)]
    expected = subtree(expected_manifest(Fixture(SEED, old + new)), prefix)
    one_tree = compare(expected, view, ignore=("mode", "mtime", "xattrs"))
    if one_tree:
        failures.append(f"the share does not show one tree: {describe(one_tree)}")

    (probe,) = _under(prefix, (WITHOUT_NC,))
    without_nc: dict = {"path": probe.path, "create_policy": variant.overrides}
    try:
        with lab.variant(variant.overrides, archive_mode=None):
            lab.write_through(lab.share, probe.path, SEED, probe)
            without_nc["holders"] = lab.holders(probe.path)
        for key in without_nc["holders"]:
            lab.remove(key, probe.path)
    except Exception as error:  # noqa: BLE001 - a measurement, recorded and never judged
        without_nc["error"] = f"{type(error).__name__}: {error}"

    lab.evidence(
        f"S1.2-{variant.name}",
        {
            "variant": variant.name,
            "gating": variant.gating,
            "overrides": dict(variant.overrides),
            "holders": holders,
            "archive_tree_hashes": {"before": before, "after": after},
            "one_tree_differences": describe(one_tree, limit=None),
            "placement_without_nc": without_nc,
            "failures": failures,
        },
    )
    if variant.gating:
        lab.required_settings["create_policy"] = {
            "category.create": lab.effective("category.create"),
            "archive_branch_mode": BASELINE_ARCHIVE_MODE,
            "found_by": "S1.2",
            "placement_without_nc": without_nc,
        }
    assert not failures, "; ".join(failures)
