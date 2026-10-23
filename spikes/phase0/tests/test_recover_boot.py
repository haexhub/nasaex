"""The recovery sequence of S2 (guest/recover.sh) across a boot (task T066a), on the host with the
fixture of tests/test_recover.py. probe-s2-boot measured why: a lock set again after a boot made
the replacement read-only; after a crash right after step 3 the replacement had lost its layout;
and with the failed disk back beside the replacement, the boot mounted the failed one."""

import json
import subprocess
import sys

import guest_stubs as stubs
import pytest
import test_recover as rec

box = rec.box
run, replace = rec.run, rec.replace


def another_filesystem(box, target):
    """The failed disk back with the same label: mounted at `target` instead of the replacement."""
    path = box["stubs"] / "state.json"
    state = json.loads(path.read_text())
    state.setdefault("uuids", {})[target] = "uuid:the-failed-disk"
    path.write_text(json.dumps(state))


def test_the_replacement_stays_writable_when_the_lock_is_set_again(box):
    # A lock set again after a boot made the replacement read-only too, and fix failed with
    # EROFS. A successful replacement is exempt from every later lock and restore.
    run(box, "lock", "arch2")
    replace(box)

    again = subprocess.run(
        ["bash", str(rec.RECOVER.with_name("archive-lock.sh")), "lock", "arch2 failed"],
        env=box["env"],
        timeout=30,
        capture_output=True,
        text=True,
    )

    assert again.returncode == 0, again.stderr
    assert stubs.mounts(box["stubs"])[box["data"]["arch2"]] == "rw"
    assert stubs.mounts(box["stubs"])[box["data"]["arch1"]] == "ro"


def test_the_recovery_state_lives_on_the_system_disk_by_default():
    out = subprocess.run(
        ["bash", "-c", f"unset RECOVER_STATE_DIR; source {rec.RECOVER}; echo $STATE_ROOT"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert out.stdout == "/var/lib/recover\n"


def test_replace_writes_the_new_filesystem_to_the_disk_before_it_counts(box):
    # After a crash right after step 3 the marker was there but `.snapshots` was not.
    run(box, "lock", "arch2")

    assert replace(box).returncode == 0

    lock, state = box["tmp"] / "lock", box["tmp"] / "state" / "arch2"
    assert stubs.calls(box["stubs"], "sync")[-3:] == [
        ["sync", "-f", box["data"]["arch2"]],
        ["sync", f"{lock}/exempt", str(lock)],
        ["sync", f"{state}/replaced", str(state)],
    ]


def test_replace_fails_its_check_when_the_filesystem_cannot_be_written(box):
    run(box, "lock", "arch2")
    (box["stubs"] / "sync").write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")

    result = replace(box)

    assert result.returncode == 1
    assert "write the filesystem of arch2" in result.stderr
    assert box["data"]["arch2"] not in stubs.mounts(box["stubs"])
    assert run(box, "recover", "arch2").returncode == 1


@pytest.mark.parametrize("step", ["recover", "verify", "resync"])
def test_no_step_runs_on_another_filesystem_than_the_replacement(box, step):
    if step == "recover":
        rec.prepare_recovery(box)
    else:
        rec.prepare_resync(box)
        if step == "verify":
            run(box, "recover", "arch2")
    another_filesystem(box, box["data"]["arch2"])
    before = len(stubs.calls(box["stubs"]))

    result = run(box, step, "arch2", "--accept-unrecoverable" if step != "recover" else "--plain")

    assert result.returncode == 1
    assert f"{box['data']['arch2']} is not mounted from the replacement of step 3" in (
        result.stderr
    )
    tools = {c[0] for c in stubs.calls(box["stubs"])[before:]}
    assert not tools & {"snapraid", "snapraid-btrfs", "mount"}
