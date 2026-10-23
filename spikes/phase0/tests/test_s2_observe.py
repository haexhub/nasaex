"""What the S2 checks read from the history (vm-tests/s2/history.py, task T067) against the
scripted `server` of tests/test_s2_history.py: every version of every file of a disk (the
synced one and the one written or changed after the last sync) judged by its hash, and a step
of the recovery sequence that fails."""

import pytest
import test_s2_history as base
from s2 import history

BLOCK = base.BLOCK
lab, server, qemu_img = base.lab, base.server, base.qemu_img


def test_observe_judges_the_synced_and_the_unsynced_version_of_each_file(lab, server):
    base.synced_once(lab, server, [("arch0", "a.bin", 100, 1), ("arch0", "b.bin", 100, 2)])
    base.flip_ready(server, "arch0", "a.bin", 100, [5])
    lab.modify("a.bin", [5])
    lab.populate([("arch0", "new.bin", 100)])
    synced_a = lab.synced_sha["arch0"]["a.bin"]
    server.hashes = {
        "a.bin": synced_a,  # the disk holds the synced content of a.bin
        "b.bin": None,
        "b.bin.unrecoverable": "1" * 64,
        "new.bin": lab.files["new.bin"].sha256("new.bin"),
        "new.bin.unrecoverable": None,
    }

    assert lab.observe("arch0") == {
        ("a.bin", "synced"): "bit_exact",
        ("a.bin", "unsynced"): "differs",
        ("b.bin", "synced"): "differs",
        ("new.bin", "unsynced"): "bit_exact",
    }


def test_observe_names_a_missing_file_and_leaves_other_disks_out(lab, server):
    base.synced_once(lab, server, [("arch0", "a.bin", 100, 1), ("arch1", "b.bin", 100, 2)])
    lab.populate([("arch1", "late.bin", 100)])
    server.hashes = {"b.bin": None, "late.bin": None}

    assert lab.observe("arch1") == {
        ("b.bin", "synced"): "missing",
        ("late.bin", "unsynced"): "missing",
    }


def test_a_deleted_file_has_only_its_synced_version(lab, server):
    base.synced_once(lab, server, [("arch0", "a.bin", 100, 1)])
    server.files.add(base.archived("arch0", "a.bin"))
    lab.delete("a.bin")
    server.hashes = {"a.bin": None}

    assert lab.observe("arch0") == {("a.bin", "synced"): "missing"}


def test_a_step_that_exits_non_zero_fails_the_observable_it_is_not_a_harness_error(lab, server):
    execute = server.execute

    def failing(command, **kw):
        if command.startswith("recover lock"):
            server.commands.append(command)
            return 1, "recover.sh: check failed: a location is still writable\n"
        return execute(command, **kw)

    server.execute = failing

    with pytest.raises(AssertionError, match="recover lock arch1 exited 1.*still writable"):
        lab.replace("arch1")
    assert server.commands == ["recover detect arch1 2>&1", "recover lock arch1 2>&1"]
    assert lab.spare("arch1", "same")["serial"] == "spare1"  # the spare was not used up


def test_replace_sends_the_steps_with_their_output(lab, server):
    lab.replace("arch2")

    assert server.commands == [
        "recover detect arch2 2>&1",
        "recover lock arch2 2>&1",
        f"recover replace arch2 /dev/disk/by-id/virtio-spare2 {512 << 20} 2>&1",
    ]
    assert history.ARCH == ("arch0", "arch1", "arch2")
