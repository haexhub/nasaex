"""Check S1.2 (vm-tests/s1/checks_placement.py): the decisions behind it, and the order of the
pool-low step (task T044a) against recording machines. Creating files through the share and
remounting the cache pool need the VMs."""

import copy

import pytest
from helpers import RecordingMachine
from s1 import checks_placement
from s1.checks_placement import NEW, changed_branches, placement_failures, pool_low_failures
from s1.context import Lab, Variant

from testlib.faults import FaultEvent
from testlib.fixtures import Entry
from testlib.recorder import Recorder


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


# --- the pool-low step (task T044a) -----------------------------------------------------------

HASHES = {"arch0": "a", "arch1": "b", "arch2": "c"}
REFUSED = {
    "creates": {
        "mount.cifs": {
            "path": "s1.2/ff/archive-dir/pool-low.txt",
            "report": {"errno": "EROFS", "step": "open", "written": 0},
            "status": "EROFS",
            "holders": [],
        },
        "smbclient": {
            "path": "s1.2/ff/archive-dir/pool-low-smbclient.txt",
            "report": [1, "NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file \\x"],
            "status": "NT_STATUS_MEDIA_WRITE_PROTECTED",
            "holders": [],
        },
    },
    "archive_tree_hashes": {"before": HASHES, "after": HASHES},
    "without_nc": {
        "path": "s1.2/ff/archive-dir/pool-low.txt",
        "available_before": {
            "pool": 16777216,
            "arch0": 5 << 30,
            "arch1": 6 << 30,
            "arch2": 3 << 30,
        },
        "report": {"errno": None, "step": None, "written": 100},
        "holders": ["arch0"],
    },
    "freed_space": {"appeared": True, "waited_s": 21.0, "avail": 900, "target": 800, "samples": 43},
}


def observed(**changes):
    """The clean pool-low step with `changes` applied: a key path joined by "/" -> value."""
    seen = copy.deepcopy(REFUSED)
    for path, value in changes.items():
        *parents, last = path.split("/")
        target = seen
        for key in parents:
            target = target[key]
        target[last] = value
    return seen


def test_a_refused_create_without_holders_passes_and_the_control_on_an_archive_branch_is_recorded():
    assert pool_low_failures(observed()) == []


@pytest.mark.parametrize(
    ("errno", "put"),
    [
        ("ENOSPC", [1, "NT_STATUS_DISK_FULL"]),
        ("EIO", [1, ""]),
        ("EROFS", [0, "NT_STATUS_ACCESS_DENIED opening remote file"]),
    ],
)
def test_the_errno_and_smb_status_of_a_refused_create_are_never_judged_by_name(errno, put):
    seen = observed(
        **{"creates/mount.cifs/report": {"errno": errno, "step": "open", "written": 0}},
        **{"creates/smbclient/report": put},
    )

    assert pool_low_failures(seen) == []


def test_a_create_that_reports_success_fails():
    seen = observed(
        **{"creates/mount.cifs/report": {"errno": None, "step": None, "written": 100}},
        **{"creates/smbclient/report": [0, "putting file /root/x as \\x (0.1 kb/s)"]},
    )

    assert pool_low_failures(seen) == [
        "pool low: the mount.cifs create reported success",
        "pool low: the smbclient create reported success",
    ]


def test_a_create_with_a_holder_fails():
    seen = observed(
        **{"creates/mount.cifs/holders": ["arch0"], "creates/smbclient/holders": ["pool"]}
    )

    assert pool_low_failures(seen) == [
        "pool low: the mount.cifs create lies on ['arch0']",
        "pool low: the smbclient create lies on ['pool']",
    ]


def test_a_changed_archive_tree_hash_fails():
    seen = observed(**{"archive_tree_hashes/after": {**HASHES, "arch2": "x"}})

    assert pool_low_failures(seen) == ["pool low: archive branch arch2 changed"]


def test_the_space_not_back_within_the_bound_fails():
    freed = {"appeared": False, "waited_s": 60.0, "avail": 0, "target": 800, "samples": 121}

    assert pool_low_failures(observed(freed_space=freed)) == [
        "pool low: the space of the filler was not back after 60.0 s (f_bavail of the pool"
        " branch 0, expected at least 800)"
    ]


class Fake(RecordingMachine):
    """Commands go to a log shared with the other machine; an output is a string, a callable
    returning one, or an (exit status, output) tuple for `execute`."""

    def __init__(self, name, log, outputs):
        super().__init__(outputs)
        self.name, self.log = name, log

    def succeed(self, command, **kwargs):
        self.log.append((self.name, command))
        out = super().succeed(command, **kwargs)
        return out() if callable(out) else out

    def execute(self, command, **kwargs):
        out = self.succeed(command, **kwargs)
        return out if isinstance(out, tuple) else (0, out)


MIB = 1 << 20


def _pool_low_lab(tmp_path, monkeypatch, *, samples=(0, 100 * MIB), create=None):
    monkeypatch.chdir(tmp_path)
    log = []
    now = [0.0]

    def sleep(seconds):
        log.append(("host", f"sleep {seconds}"))
        now[0] += seconds

    monkeypatch.setattr(checks_placement, "monotonic", lambda: now[0])
    monkeypatch.setattr(checks_placement, "sleep", sleep)
    polled = iter(samples)

    def pool_free():
        unfilled = any(c == "rm -f /mnt/pool/.spike-filler" for _, c in log)
        return str(next(polled) if unfilled else 100 * MIB)

    def holders():
        nc_removed = any(c.startswith("mergerfs -o") for _, c in log)
        command = log[-1][1]
        return '["arch0"]' if nc_removed and "/pool-low.txt" in command else "[]"

    def filler(machine, mountpoint, leave_bytes, point=""):
        log.append(("filler", f"{mountpoint} {leave_bytes} {point}"))
        return FaultEvent("enospc", point, "filled")

    monkeypatch.setattr(checks_placement.faults, "enospc", filler)
    what = "/mnt/pool:" + ":".join(f"/mnt/archive/arch{i}/branch=NC" for i in range(3))
    server = Fake(
        "server",
        log,
        {
            "s/^What=//p": what,
            "s/^Options=//p": "allow_other,category.create=ff",
            "build_manifest": "{}",
            "lexists": holders,
            "| tail -n +2": "16777216\n5000\n6000\n3000\n",
            "| tail -n1": pool_free,
        },
    )

    def writer():
        if create:
            return create()
        nc_removed = any(c.startswith("mergerfs -o") for _, c in log)
        errno = "null" if nc_removed else '"EROFS"'
        return f'{{"errno": {errno}, "step": null, "written": 0}}'

    client = Fake(
        "client",
        log,
        {"errorcode": writer, "-c 'put ": (1, "NT_STATUS_MEDIA_WRITE_PROTECTED opening x")},
    )
    roots = {"pool": "/mnt/pool", **{f"arch{i}": f"/mnt/archive/arch{i}/branch" for i in range(3)}}
    tables = {"placement_pool_low": {"leave_bytes": 16 * MIB}}
    lab = Lab(server, client, roots, Recorder(), user=None, tables=tables)
    return lab, log


def _step(machine, command):
    if machine in ("filler", "host"):
        return machine if machine == "filler" else command
    if machine == "server" and command.endswith("| tail -n1"):
        return "free"
    if machine == "server" and command.endswith("| tail -n +2"):
        return "available"
    if machine == "client" and "/pool-low.txt wb " in command:
        return "create"
    if machine == "client" and "-c 'put " in command:
        return "put"
    if machine == "server" and command.startswith("mergerfs -o"):
        return "remount without NC"
    if machine == "server" and command == "rm -f /mnt/pool/.spike-filler":
        return "unfill"
    if machine == "server" and command.startswith("rm -rf -- "):
        return "remove " + command.split()[3].split("/")[3]
    return None


def test_the_pool_low_step_fills_creates_measures_the_control_then_frees_and_waits(
    tmp_path, monkeypatch
):
    lab, log = _pool_low_lab(tmp_path, monkeypatch)

    seen = checks_placement._pool_low(lab, Variant("ff", True, {}), "s1.2/ff")

    steps = [s for s in (_step(m, c) for m, c in log) if s]
    assert steps == [
        *("free", "filler", "create", "put", "remount without NC", "available", "create"),
        *("unfill", "remove arch0", "free", "sleep 0.5", "free"),
    ]
    assert ("filler", "/mnt/pool 16777216 S1.2") in log
    assert seen["without_nc"]["holders"] == ["arch0"]
    assert seen["without_nc"]["available_before"] == {
        "pool": 16777216,
        "arch0": 5000,
        "arch1": 6000,
        "arch2": 3000,
    }
    assert seen["creates"]["smbclient"]["status"] == "NT_STATUS_MEDIA_WRITE_PROTECTED"
    assert seen["creates"]["mount.cifs"]["status"] == "EROFS"
    assert seen["freed_space"]["target"] == 100 * MIB - 16 * MIB
    assert seen["freed_space"]["appeared"] is True
    assert pool_low_failures(seen) == []


def test_the_pool_low_step_removes_the_filler_when_a_create_breaks(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("client gone")

    lab, log = _pool_low_lab(tmp_path, monkeypatch, create=broken)

    with pytest.raises(RuntimeError, match="client gone"):
        checks_placement._pool_low(lab, Variant("ff", True, {}), "s1.2/ff")

    assert ("server", "rm -f /mnt/pool/.spike-filler") in log
