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
branch mode) are recorded from the gating case.

Then the pool-low step (task T044a, proposed in spec Clarifications 2026-10-19, NC placement):
a filler (fault kind `enospc`) leaves tables.placement_pool_low.leave_bytes free on the pool
branch, below minfreespace, the one state in which the create policy does not choose the pool.
With NC in effect the client creates a small file inside the archive-only directory through
mount.cifs and one with smbclient put: neither may report success, neither path may exist on
any branch, the archive tree hashes must be unchanged; the errno and the SMB status are
recorded, never judged by name (the pinned mergerfs 2.41.1 source predicts EROFS, which Samba
reports as NT_STATUS_MEDIA_WRITE_PROTECTED). With NC removed the client creates the mount.cifs
file once more: the branch that holds it and the available bytes of every branch before that
create are recorded, never judged. The file and the filler are removed and the check waits
until f_bavail of the pool branch is back to its value before the filler less 16 MiB, at most
60 s (a failure otherwise), so that later scenarios do not start inside the window in which
mergerfs refuses creates (probe T051h). Scaffolding for the spikes, not product code."""

import json
import shlex
from collections.abc import Mapping
from time import monotonic, sleep

from probes.enospc_writes import SOURCE_SCRIPT, WRITER
from s1.context import BASELINE_ARCHIVE_MODE, Lab, Variant, under
from s1.fixture import POOL, POOL_BRANCH

from testlib import faults
from testlib.fixtures import Entry, Fixture, expected_manifest
from testlib.manifest import compare, describe, subtree
from testlib.smb import nt_status

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
# The pool-low step (task T044a): the mount.cifs and the smbclient file, from one small source.
POOL_LOW = {
    "mount.cifs": "archive-dir/pool-low.txt",
    "smbclient": "archive-dir/pool-low-smbclient.txt",
}
POOL_LOW_SOURCE = "/root/s1.2-pool-low.bin"
POOL_LOW_BYTES = 100


def changed_branches(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return [key for key in before if after.get(key) != before[key]]


def placement_failures(holders: Mapping[str, list[str]]) -> list[str]:
    return [f"{path} lies on {where}" for path, where in holders.items() if where != [POOL]]


def _reported_success(client: str, report) -> bool:
    if client == "mount.cifs":
        return report["errno"] is None
    status, output = report
    return status == 0 and nt_status(output) is None


def pool_low_failures(seen: Mapping) -> list[str]:
    """The pool-low step with NC in effect: neither create reports success, neither path has a
    holder, the archive tree hashes are unchanged and the space is back within the bound. The
    errno, the SMB status and the control without NC are not read here."""
    failures = []
    for client, create in seen["creates"].items():
        if _reported_success(client, create["report"]):
            failures.append(f"pool low: the {client} create reported success")
    for client, create in seen["creates"].items():
        if create["holders"]:
            failures.append(f"pool low: the {client} create lies on {create['holders']}")
    hashes = seen["archive_tree_hashes"]
    failures += [
        f"pool low: archive branch {key} changed"
        for key in changed_branches(hashes["before"], hashes["after"])
    ]
    freed = seen["freed_space"]
    if not freed["appeared"]:
        failures.append(
            f"pool low: the space of the filler was not back after {freed['waited_s']} s"
            f" (f_bavail of the pool branch {freed['avail']}, expected at least {freed['target']})"
        )
    return failures


def _create(lab: Lab, entry: Entry) -> None:
    if entry.kind == "dir":
        lab.client.succeed(f"mkdir {lab.share.path(entry.path)}")
    else:
        lab.write_through(lab.share, entry.path, SEED, entry)


def _write_report(lab: Lab, rel: str) -> dict:
    """The small source written through the mount.cifs mount by the probe writer: its errno,
    the step that failed and the bytes accepted (a refusal is an outcome, not an error)."""
    args = (lab.share.path(rel), "wb", POOL_LOW_SOURCE, str(POOL_LOW_BYTES))
    return json.loads(lab.python(lab.client, WRITER, *args))


def _available(lab: Lab) -> dict[str, int]:
    """f_bavail in bytes of the filesystem of every share branch (pool and archive)."""
    paths = " ".join(shlex.quote(path) for path in lab.share_branches.values())
    out = lab.server.succeed(f"df --output=avail -B1 {paths} | tail -n +2")
    return dict(zip(lab.share_branches, map(int, out.split()), strict=True))


def _without_nc(lab: Lab, variant: Variant, rel: str) -> dict:
    """The control: the mount.cifs create once more with NC removed; recorded, never judged."""
    control: dict = {"path": rel}
    try:
        with lab.variant(variant.overrides, archive_mode=None):
            control["available_before"] = _available(lab)
            control["report"] = _write_report(lab, rel)
            control["holders"] = lab.holders(rel)
    except Exception as error:  # noqa: BLE001 - a measurement, recorded and never judged
        control["error"] = f"{type(error).__name__}: {error}"
    return control


def _pool_low(lab: Lab, variant: Variant, prefix: str) -> dict:
    # Imported here: checks_large_space imports changed_branches from this module.
    from s1.checks_large_space import SPACE_EPSILON, pool_free_bytes, wait_for_freed_space

    paths = {client: f"{prefix}/{rel}" for client, rel in POOL_LOW.items()}
    leave = lab.tables["placement_pool_low"]["leave_bytes"]
    seen: dict = {"leave_bytes": leave, "free_before_filler": pool_free_bytes(lab)}
    try:
        lab.python(lab.client, SOURCE_SCRIPT, POOL_LOW_SOURCE, str(POOL_LOW_BYTES))
        seen["filler"] = faults.enospc(lab.server, POOL_BRANCH, leave, point="S1.2").detail
        with lab.variant(variant.overrides):
            before = lab.tree_hashes()
            cifs = _write_report(lab, paths["mount.cifs"])
            put = list(lab.smb.put(POOL_LOW_SOURCE, paths["smbclient"]))
            seen["creates"] = {
                "mount.cifs": {"report": cifs, "status": cifs["errno"]},
                "smbclient": {"report": put, "status": nt_status(put[1])},
            }
            seen["archive_tree_hashes"] = {"before": before, "after": lab.tree_hashes()}
            for client, create in seen["creates"].items():
                create |= {"path": paths[client], "holders": lab.holders(paths[client])}
        seen["without_nc"] = _without_nc(lab, variant, paths["mount.cifs"])
    finally:
        lab.server.succeed(f"rm -f {POOL_BRANCH}/{faults.DEFAULT_FILLER}")
        for rel in paths.values():
            for key in lab.holders(rel):
                lab.remove(key, rel)
        lab.client.execute(f"rm -f {POOL_LOW_SOURCE}")
    target = seen["free_before_filler"] - SPACE_EPSILON
    seen["freed_space"] = wait_for_freed_space(
        lambda: pool_free_bytes(lab), target, clock=monotonic, sleep=sleep
    )
    return seen


def check_new_data_placement(lab: Lab, variant: Variant) -> None:
    prefix = f"s1.2/{variant.name}"
    old, new = under(prefix, OLD), under(prefix, NEW)
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

    (probe,) = under(prefix, (WITHOUT_NC,))
    without_nc: dict = {"path": probe.path, "create_policy": variant.overrides}
    try:
        with lab.variant(variant.overrides, archive_mode=None):
            lab.write_through(lab.share, probe.path, SEED, probe)
            without_nc["holders"] = lab.holders(probe.path)
        for key in without_nc["holders"]:
            lab.remove(key, probe.path)
    except Exception as error:  # noqa: BLE001 - a measurement, recorded and never judged
        without_nc["error"] = f"{type(error).__name__}: {error}"

    pool_low = _pool_low(lab, variant, prefix)
    failures += pool_low_failures(pool_low)

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
            "placement_pool_low_without_nc": pool_low.pop("without_nc", None),
            "placement_pool_low": pool_low,
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
