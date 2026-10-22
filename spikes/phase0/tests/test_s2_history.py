"""The S2 history (vm-tests/s2/history.py, task T065) against a scripted stand-in for the
`server`: the commands it sends, their order, and its bookkeeping for the parity model. The
guest scripts FILL, _FLIP and _HASHES run here for real on local files; the effects of the
tools (mergerfs, the mover, SnapRAID, the faults) are measured in the VM (probe T065)."""

import base64
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from probes.snapraid_observe import BLOCK, FILL
from s2 import history
from s2.history import History

from testlib.fixtures import Entry, flipped_sha256

PHASE0 = Path(__file__).resolve().parents[1]
# The disks of the `small` profile (nix/profiles.nix), as mkSpike lists them in TOPOLOGY.
SIZES = [
    ("pool0", "pool", 1024),
    ("pool1", "pool", 1024),
    ("arch0", "archive", 640),
    ("arch1", "archive", 1024),
    ("arch2", "archive", 512),
    ("parity0", "parity", 1280),
    ("spare0", "replacement", 640),
    ("spare1", "replacement", 1024),
    ("spare2", "replacement", 512),
    ("spare3", "replacement", 1024),
]
TOPOLOGY = {
    "disks": [
        {"serial": s, "role": r, "sizeMiB": m, "image": f"empty{i}.qcow2"}
        for i, (s, r, m) in enumerate(SIZES)
    ]
}


class Server:
    """Answers the commands of the history; `files` are the paths `test -f` finds, `land` the
    archive disk where the mover puts a file, `starts` the physical start filefrag reports."""

    def __init__(self, state_dir, booted=False):
        self.state_dir = state_dir
        self.booted = booted
        self.commands: list[str] = []
        self.events: list[str] = []
        self.files: set[str] = set()
        self.land = "arch1"
        self.starts: dict[str, int | None] = {}
        self.inodes: dict[str, list[str]] = {}
        self.hashes: dict[str, str | None] = {}
        self.sync_rc = 0
        self.locate = ""
        self.fill = b""

    def crash(self):
        self.events.append("crash")
        self.booted = False

    def start(self):
        self.events.append("start")
        self.booted = True

    def wait_for_unit(self, unit, **_):
        self.events.append(f"unit {unit}")

    def wait_until_succeeds(self, command, **_):
        self.events.append("settle")

    def execute(self, command, **_):
        self.commands.append(command)
        if command.startswith("test -f "):
            return (0 if shlex.split(command)[2] in self.files else 1), ""
        if " sync " in f"{command} " and ("snapraid" in command or "snapsync" in command):
            return self.sync_rc, "sync output"
        return 0, ""

    def succeed(self, command, **_):
        self.commands.append(command)
        words = shlex.split(command) if "python3 -c" not in command else []
        if command.startswith("test -f ") and words[2] not in self.files:
            raise AssertionError(f"command failed: {command}")
        if command.startswith(": > "):
            self.fill = b""
        if command.startswith("echo ") and "base64 -d" in command:
            self.fill += base64.b64decode(words[1])
        if command.startswith("python3 -c") and FILL in shlex.split(command)[2]:
            for path, *_ in json.loads(self.fill):
                if path.startswith(history.CACHE + "/"):
                    path = history.POOL_BRANCH + path[len(history.CACHE) :]
                self.files.add(path)
        if command.startswith("mover-effects mover_copy_item"):
            source = words[2]
            rel = source[len(history.POOL_BRANCH) + 1 :]
            self.files.discard(source)
            self.files.add(f"{history.DATA[self.land]}/branch/{rel}")
        if command.startswith("rm -- "):
            rel = words[2][len(history.CACHE) + 1 :]
            self.files = {f for f in self.files if not f.endswith(f"/branch/{rel}")}
        if command.startswith("stat -c %i "):
            return self.inodes[words[3]].pop(0)
        if command.startswith("filefrag -e "):
            start = self.starts[words[2]]
            if start is None:
                return ""
            return f"   0:        0..      15: {start:10d}..  {start + 15}:"
        if command.startswith("python3 -c") and "file_digest" in command:
            rels = shlex.split(command)[4:]
            return json.dumps({r: self.hashes.get(r) for r in rels})
        if command.startswith("snapraid --test-fmt disk locate"):
            return self.locate
        return ""

    def spec(self) -> list:
        return json.loads(self.fill)


@pytest.fixture
def qemu_img(tmp_path):
    """A qemu-img stand-in that creates the image file and logs its arguments."""
    script = tmp_path / "qemu-img"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nopen(sys.argv[4], 'w').close()\n"
        f"open({str(tmp_path / 'qemu.log')!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def server(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return Server(state)


@pytest.fixture
def lab(server, qemu_img):
    return History(server, TOPOLOGY, str(qemu_img))


def archived(disk, rel):
    return f"{history.DATA[disk]}/branch/{rel}"


def synced_once(lab, server, files):
    """populate (disk, rel, size, physical start) and a plain sync that succeeds."""
    lab.populate([(d, r, s) for d, r, s, _ in files])
    server.starts.update({archived(d, r): p for d, r, _, p in files})
    rc, _, _ = lab.sync(snapshots=False)
    assert rc == 0


# --- reset ---------------------------------------------------------------------------------------


def test_reset_powers_off_replaces_every_disk_image_then_formats_mounts_and_inits(lab, server):
    server.booted = True
    lab.reset()

    assert server.events[:2] == ["crash", "start"]
    assert sorted(p.name for p in Path(server.state_dir).iterdir()) == sorted(
        d["image"] for d in TOPOLOGY["disks"]
    )
    assert server.commands[0] == "spike-mkfs all"
    mounts = [c.split("|| mount ")[1] for c in server.commands if "|| mount " in c]
    assert mounts == [
        "/mnt/pool",
        *history.DATA.values(),
        *history.CONTENT.values(),
        "/mnt/parity",
    ]
    assert "systemctl start mnt-base.mount mnt-cache.mount" in server.commands
    assert server.commands[-1] == "spike-snapshots-init arch0 arch1 arch2"
    assert any(c.startswith("rm -f /var/lib/snapraid/snapraid.*") for c in server.commands)


def test_reset_of_a_machine_that_never_started_does_not_power_it_off(lab, server):
    lab.reset()

    assert "crash" not in server.events


def test_reset_forgets_the_history(lab, server):
    synced_once(lab, server, [("arch0", "a.bin", BLOCK, 100)])

    lab.reset()

    assert lab.files == {} and lab.synced == {d: {} for d in history.ARCH}
    lab.populate([("arch0", "a.bin", 10)])  # a new history may use the path again


# --- the guest scripts against local files -----------------------------------------------------


def test_the_guest_scripts_write_flip_and_hash_the_content_the_history_expects(tmp_path):
    target = tmp_path / "branch" / "dir" / "f.bin"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([[str(target), "dir/f.bin", 3 * BLOCK + 5, 1_700_000_000_5]]))
    env = {**os.environ, "PYTHONPATH": str(PHASE0)}
    python = [sys.executable, "-c"]
    subprocess.run([*python, FILL, history.SEED, str(spec)], env=env, check=True)
    assert os.stat(target).st_mtime_ns == 1_700_000_000_5
    subprocess.run([*python, history._FLIP, str(target), "7", str(BLOCK + 1)], check=True)
    out = subprocess.run(
        [*python, history._HASHES, str(tmp_path / "branch"), "dir/f.bin", "gone.bin"],
        check=True,
        capture_output=True,
        text=True,
    )

    expected = history.File("arch0", 3 * BLOCK + 5, [7, BLOCK + 1]).sha256("dir/f.bin")
    assert json.loads(out.stdout) == {"dir/f.bin": expected, "gone.bin": None}
    assert os.stat(target).st_mtime_ns != 1_700_000_000_5  # SnapRAID sees the change by it


# --- populate, add, modify, delete ---------------------------------------------------------------


def test_populate_writes_seeded_files_into_the_branch_in_order_with_distinct_mtimes(lab, server):
    lab.populate([("arch0", "a.bin", 100), ("arch2", "d/b.bin", 200)])

    spec = server.spec()
    assert [s[:3] for s in spec] == [
        [archived("arch0", "a.bin"), "a.bin", 100],
        [archived("arch2", "d/b.bin"), "d/b.bin", 200],
    ]
    assert len({s[3] for s in spec}) == 2 and all(s[3] % 1_000_000_000 for s in spec)
    assert lab.unsynced == {"arch0": {"branch/a.bin"}, "arch1": set(), "arch2": {"branch/d/b.bin"}}


def test_add_creates_through_the_share_then_moves_the_mover_way(lab, server):
    disk = lab.add("new/x.bin", 300, not_on="arch0")

    assert disk == "arch1"
    assert server.spec() == [[f"{history.CACHE}/new/x.bin", "new/x.bin", 300, server.spec()[0][3]]]
    move = next(c for c in server.commands if c.startswith("mover-effects"))
    assert move == (
        "mover-effects mover_copy_item /mnt/pool/new/x.bin /mnt/base/new/x.bin 0"
        " && mover-effects mover_unlink_pool_copy /mnt/pool/new/x.bin 0"
    )
    assert lab.unsynced["arch1"] == {"branch/new/x.bin"}


def test_add_refuses_a_landing_on_the_disk_that_is_to_fail(lab, server):
    server.land = "arch0"

    with pytest.raises(RuntimeError, match="landed on arch0"):
        lab.add("x.bin", 10, not_on="arch0")


def test_add_refuses_a_file_that_is_on_no_or_on_two_archive_disks(lab, server):
    server.files.add(archived("arch2", "x.bin"))  # a second copy, not the mover's

    with pytest.raises(RuntimeError, match="on one archive disk"):
        lab.add("x.bin", 10)


def test_paths_are_never_reused_within_a_history(lab, server):
    lab.populate([("arch0", "a.bin", 10)])
    server.files.add(archived("arch0", "a.bin"))
    lab.delete("a.bin")

    with pytest.raises(ValueError, match="never reused"):
        lab.populate([("arch0", "a.bin", 10)])
    with pytest.raises(ValueError, match="never reused"):
        lab.add("a.bin", 10)


def flip_ready(server, disk, rel, size, offsets, flips=()):
    """Inode unchanged and the archived content as expected after the flips."""
    server.inodes[archived(disk, rel)] = ["11\n", "11\n"]
    entry = Entry(rel, "archive", size=size)
    server.hashes[rel] = flipped_sha256(history.SEED, entry, [*flips, *offsets])


def test_modify_inverts_bytes_in_place_through_the_share_and_marks_a_synced_file(lab, server):
    synced_once(lab, server, [("arch0", "a.bin", 3 * BLOCK, 100)])
    flip_ready(server, "arch0", "a.bin", 3 * BLOCK, [5, BLOCK])

    lab.modify("a.bin", [5, BLOCK])

    flip = next(c for c in server.commands if "python3 -c" in c and "r+b" in c)
    assert shlex.split(flip)[3:] == [f"{history.CACHE}/a.bin", "5", str(BLOCK)]
    assert lab.files["a.bin"].flips == [5, BLOCK]
    assert lab.changed["arch0"] == {"branch/a.bin"}
    assert lab.unsynced["arch0"] == {"branch/a.bin"}


def test_a_file_changed_after_a_sync_moves_at_the_next_one(lab, server):
    synced_once(lab, server, [("arch0", "a.bin", BLOCK, 100), ("arch0", "b.bin", BLOCK, 200)])
    assert lab.synced["arch0"] == {"branch/a.bin": (0,), "branch/b.bin": (1,)}
    flip_ready(server, "arch0", "a.bin", BLOCK, [1])
    lab.modify("a.bin", [1])
    lab.populate([("arch0", "c.bin", 2 * BLOCK)])
    server.starts.update({archived("arch0", "a.bin"): 300, archived("arch0", "c.bin"): 50})

    assert lab.sync(snapshots=True)[0] == 0

    # removals first, then the new and rewritten files by physical start (probe T057)
    assert lab.synced["arch0"] == {
        "branch/b.bin": (1,),
        "branch/c.bin": (0, 2),
        "branch/a.bin": (3,),
    }
    assert lab.synced_sha["arch0"]["a.bin"] == lab.files["a.bin"].sha256("a.bin")
    assert lab.changed["arch0"] == set() and lab.unsynced["arch0"] == set()
    sync = "snapsync sync --log /root/s2-logs/sync-2.log"
    assert any(c.startswith(sync) for c in server.commands)


def test_modify_refuses_offsets_that_restore_the_content(lab, server):
    lab.populate([("arch0", "a.bin", 100)])

    with pytest.raises(ValueError, match="restore"):
        lab.modify("a.bin", [3, 3])
    assert not any("r+b" in c for c in server.commands)


def test_modify_refuses_a_change_that_was_not_made_in_place(lab, server):
    lab.populate([("arch0", "a.bin", 100)])
    flip_ready(server, "arch0", "a.bin", 100, [3])
    server.inodes[archived("arch0", "a.bin")] = ["11\n", "12\n"]

    with pytest.raises(RuntimeError, match="in place"):
        lab.modify("a.bin", [3])


def test_modify_refuses_an_archived_content_that_is_not_the_expected_change(lab, server):
    lab.populate([("arch0", "a.bin", 100)])
    flip_ready(server, "arch0", "a.bin", 100, [3])
    server.hashes["a.bin"] = "0" * 64

    with pytest.raises(RuntimeError, match="expected change"):
        lab.modify("a.bin", [3])
    assert lab.files["a.bin"].flips == []


def test_delete_through_the_share(lab, server):
    synced_once(lab, server, [("arch0", "a.bin", 100, 100)])
    lab.populate([("arch0", "new.bin", 100)])

    lab.delete("a.bin")
    lab.delete("new.bin")

    assert "rm -- /mnt/cache/a.bin" in server.commands
    assert lab.changed["arch0"] == {"branch/a.bin"}  # synced, now deleted
    assert lab.unsynced["arch0"] == set()  # never synced, gone without a trace
    assert set(lab.files) == set()


def test_delete_refuses_a_file_still_on_its_disk(lab, server):
    lab.populate([("arch0", "a.bin", 100)])
    server.succeed = lambda command, **_: server.commands.append(command) or ""

    with pytest.raises(RuntimeError, match="still on arch0"):
        lab.delete("a.bin")


def test_modify_and_delete_need_a_live_file(lab):
    with pytest.raises(KeyError):
        lab.modify("nope.bin", [1])
    with pytest.raises(KeyError):
        lab.delete("nope.bin")


# --- sync ----------------------------------------------------------------------------------------


def test_a_plain_sync_logs_and_folds_the_files_by_physical_start(lab, server):
    synced_once(lab, server, [("arch2", "a.bin", 3 * BLOCK, 900), ("arch2", "b.bin", 10, 200)])

    assert lab.synced["arch2"] == {"branch/b.bin": (0,), "branch/a.bin": (1, 2, 3)}
    assert "snapraid -l /root/s2-logs/sync-1.log sync 2>&1" in server.commands
    assert lab.synced_sha["arch2"]["b.bin"] == history.File("arch2", 10).sha256("b.bin")


def test_a_failed_sync_keeps_every_change_pending_and_records_its_layout(lab, server):
    lab.populate([("arch1", "a.bin", 2 * BLOCK)])
    server.starts[archived("arch1", "a.bin")] = 10
    server.sync_rc = 137

    rc, out, log = lab.sync(snapshots=False)

    assert (rc, out, log) == (137, "sync output", "/root/s2-logs/sync-1.log")
    assert lab.synced["arch1"] == {} and lab.synced_sha["arch1"] == {}
    assert lab.attempted["arch1"] == {"branch/a.bin": (0, 1)}
    assert lab.unsynced["arch1"] == {"branch/a.bin"}


def test_an_empty_file_takes_no_position_and_needs_no_filefrag(lab, server):
    synced_once(lab, server, [("arch0", "empty.bin", 0, None)])

    assert lab.synced["arch0"] == {"branch/empty.bin": ()}
    assert not any(c.startswith("filefrag") for c in server.commands)


def test_a_file_without_an_extent_is_refused(lab, server):
    lab.populate([("arch0", "a.bin", 10)])
    server.starts[archived("arch0", "a.bin")] = None

    with pytest.raises(RuntimeError, match="no extent"):
        lab.sync(snapshots=False)


# --- failure and replacement ---------------------------------------------------------------------


def test_fail_uses_the_named_host_side_fault(lab, server, monkeypatch):
    called = []
    for kind in history.FAULTS:
        monkeypatch.setitem(
            history.FAULTS, kind, lambda m, s, k=kind: called.append((k, s)) or f"event {k}"
        )

    assert lab.fail("arch0") == "event device-ioerror"
    assert lab.fail("arch2", "device-unplug") == "event device-unplug"
    assert called == [("device-ioerror", "arch0"), ("device-unplug", "arch2")]
    assert lab.failed == ["arch0", "arch2"]
    with pytest.raises(ValueError, match="device-ioerror or device-unplug"):
        lab.fail("arch1", "poweroff")


@pytest.mark.parametrize(
    ("disk", "relation", "spare"),
    [
        ("arch0", "same", "spare0"),
        ("arch0", "larger", "spare1"),
        ("arch0", "smaller", "spare2"),
        ("arch1", "same", "spare1"),
        ("arch1", "smaller", "spare0"),
        ("arch2", "same", "spare2"),
        ("arch2", "larger", "spare0"),
    ],
)
def test_the_spare_of_each_size_relation(lab, disk, relation, spare):
    assert lab.spare(disk, relation)["serial"] == spare


def test_no_spare_and_an_unknown_relation_are_refused(lab):
    with pytest.raises(LookupError, match="smaller"):
        lab.spare("arch2", "smaller")
    with pytest.raises(ValueError, match="same, a larger or a smaller"):
        lab.spare("arch0", "bigger")


def test_replace_runs_steps_1_to_3_with_the_failed_disks_size_and_uses_the_spare_up(lab, server):
    device = lab.replace("arch1")

    assert device == "/dev/disk/by-id/virtio-spare1"
    assert server.commands == [
        "recover detect arch1",
        "recover lock arch1",
        f"recover replace arch1 /dev/disk/by-id/virtio-spare1 {1024 << 20}",
    ]
    assert lab.spare("arch1", "same")["serial"] == "spare3"  # spare1 is taken now


# --- what the checks read ------------------------------------------------------------------------


def test_compare_judges_each_synced_file_by_its_hash(lab, server):
    files = [
        ("arch0", r, 100, p)
        for r, p in (
            ("a.bin", 1),
            ("b.bin", 2),
            ("c.bin", 3),
            ("d.bin", 4),
            ("e.bin", 5),
            ("e.bin.unrecoverable", 6),
        )
    ]
    synced_once(lab, server, files)
    server.hashes = {
        "a.bin": lab.synced_sha["arch0"]["a.bin"],
        "b.bin": "0" * 64,
        "c.bin": None,
        "c.bin.unrecoverable": "1" * 64,
        "d.bin": None,
        "e.bin": None,
        "e.bin.unrecoverable": lab.synced_sha["arch0"]["e.bin.unrecoverable"],
    }

    assert lab.compare("arch0") == {
        "bit_exact": ["a.bin", "e.bin.unrecoverable"],
        "differs": ["b.bin", "c.bin"],
        "missing": ["d.bin", "e.bin"],
    }


def test_layout_differences_against_snapraid_locate(lab, server):
    synced_once(lab, server, [("arch0", "a.bin", 2 * BLOCK, 1), ("arch1", "b.bin", BLOCK, 1)])
    server.locate = "\n".join(
        [
            f"{0:12d} {2 * BLOCK:12d} {1:6d} arch0:branch/a.bin",
            f"{BLOCK:12d} {BLOCK:12d} {1:6d} arch1:branch/b.bin",
            f"{0:12d} {BLOCK:12d} {1:6d} arch2:branch/stray.bin",
        ]
    )

    assert lab.layout_differences() == [
        "arch1:branch/b.bin: model (0, 0, 1), locate (1, 1, 1)",
        "arch2:branch/stray.bin: model None, locate (0, 0, 1)",
    ]
