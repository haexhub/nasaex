"""The in-VM scaffolding scripts run on the host against temporary directories: the blocking
hook protocol and the shared move primitives."""

import os
import subprocess
import time
from pathlib import Path

import pytest

GUEST = Path(__file__).resolve().parents[1] / "guest"
HOOK = GUEST / "spike_hook.sh"
EFFECTS = GUEST / "mover-effects.sh"
M1_ORDER = [
    "mid:M1:tmp-created",
    "mid:M1:tmp-partial",
    "mid:M1:tmp-data-synced",
    "mid:M1:renamed",
    "mid:M1:parent-synced",
    "mid:M1:item-1-done",
]
M5_ORDER = ["mid:M5:item-validated", "mid:M5:unlinked", "mid:M5:item-1-done"]


@pytest.fixture
def hooks(tmp_path):
    directory = tmp_path / "hooks"
    directory.mkdir()
    return directory


def env(hooks, extra_path=None):
    path = os.environ["PATH"] if extra_path is None else f"{extra_path}:{os.environ['PATH']}"
    return {**os.environ, "SPIKE_HOOK_DIR": str(hooks), "PATH": path}


def arm(hooks, point):
    (hooks / "armed").mkdir(exist_ok=True)
    (hooks / "armed" / point).touch()


def wait_for(path, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} did not appear"
        time.sleep(0.01)


def release(hooks, point):
    with open(hooks / "go" / point, "w") as fifo:  # blocks until the hook reads
        fifo.write("\n")


def test_an_unarmed_hook_returns_immediately_and_touches_nothing(hooks):
    result = subprocess.run(
        ["bash", str(HOOK), "post:M5"], env=env(hooks), timeout=10, capture_output=True, check=False
    )

    assert result.returncode == 0
    assert list(hooks.iterdir()) == []


def test_an_armed_hook_reports_the_point_and_blocks_until_the_fifo_is_written(hooks):
    arm(hooks, "post:M5")
    process = subprocess.Popen(["bash", str(HOOK), "post:M5"], env=env(hooks))

    wait_for(hooks / "reached" / "post:M5")
    assert process.poll() is None  # blocked at the point
    release(hooks, "post:M5")

    assert process.wait(timeout=10) == 0
    assert not (hooks / "armed" / "post:M5").exists()  # a retry passes the point freely
    assert (hooks / "reached" / "post:M5").exists()


def test_a_killed_blocked_hook_leaves_the_point_armed_for_the_host_to_clear(hooks):
    arm(hooks, "post:M1")
    process = subprocess.Popen(["bash", str(HOOK), "post:M1"], env=env(hooks))
    wait_for(hooks / "reached" / "post:M1")

    process.kill()
    process.wait(timeout=10)

    assert (hooks / "armed" / "post:M1").exists()


def test_the_hook_never_syncs_or_drops_caches(hooks, tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for tool in ("sync", "fsync", "fdatasync"):
        stub = stubs / tool
        stub.write_text(f"#!/bin/sh\ntouch {tmp_path}/{tool}-was-called\n")
        stub.chmod(0o755)
    arm(hooks, "post:M2")
    process = subprocess.Popen(["bash", str(HOOK), "post:M2"], env=env(hooks, str(stubs)))
    wait_for(hooks / "reached" / "post:M2")
    release(hooks, "post:M2")
    process.wait(timeout=10)

    assert not list(tmp_path.glob("*-was-called"))


def make_source(tmp_path, size=1000):
    src = tmp_path / "pool" / "docs" / "a.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(bytes(range(256)) * (size // 256) + bytes(size % 256))
    os.utime(src, (1_700_000_000, 1_700_000_000))
    src.chmod(0o640)
    return src


def run_effects(hooks, *args):
    return subprocess.run(
        ["bash", str(EFFECTS), *args], env=env(hooks), timeout=20, capture_output=True, check=False
    )


def test_copy_item_gives_the_same_bytes_mode_and_mtime_at_the_final_name(hooks, tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "base" / "docs" / "a.bin"

    result = run_effects(hooks, "mover_copy_item", str(src), str(dest), "0")

    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == src.read_bytes()
    assert (dest.stat().st_mode & 0o777, int(dest.stat().st_mtime)) == (0o640, 1_700_000_000)
    assert [p.name for p in dest.parent.iterdir()] == ["a.bin"]  # no temporary name left


def test_copy_item_atomically_replaces_an_older_archived_version(hooks, tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "base" / "docs" / "a.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"old archived version")

    run_effects(hooks, "mover_copy_item", str(src), str(dest), "0")

    assert dest.read_bytes() == src.read_bytes()


def test_copy_item_passes_its_points_in_the_contract_order_for_the_first_item(hooks, tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "base" / "docs" / "a.bin"
    for point in M1_ORDER:
        arm(hooks, point)
    process = subprocess.Popen(
        ["bash", str(EFFECTS), "mover_copy_item", str(src), str(dest), "1"], env=env(hooks)
    )

    for point in M1_ORDER:
        wait_for(hooks / "reached" / point)
        tmp_files = [p for p in dest.parent.iterdir() if p.name.startswith(".a.bin.tmp-")]
        if point == "mid:M1:tmp-created":
            assert tmp_files and tmp_files[0].stat().st_size == 0
        if point == "mid:M1:tmp-partial":
            assert tmp_files[0].stat().st_size == src.stat().st_size // 2
        if point == "mid:M1:tmp-data-synced":
            assert tmp_files[0].read_bytes() == src.read_bytes()
        if point == "mid:M1:renamed":
            assert dest.read_bytes() == src.read_bytes() and not tmp_files
        release(hooks, point)

    assert process.wait(timeout=20) == 0


def test_hooks_fire_only_for_the_first_item(hooks, tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "base" / "docs" / "a.bin"
    for point in M1_ORDER:
        arm(hooks, point)

    result = run_effects(hooks, "mover_copy_item", str(src), str(dest), "0")

    assert result.returncode == 0
    assert not (hooks / "reached").exists()


def test_unlink_pool_copy_removes_the_file_and_passes_its_points(hooks, tmp_path):
    victim = make_source(tmp_path)
    for point in M5_ORDER:
        arm(hooks, point)
    process = subprocess.Popen(
        ["bash", str(EFFECTS), "mover_unlink_pool_copy", str(victim), "1"], env=env(hooks)
    )

    for point in M5_ORDER:
        wait_for(hooks / "reached" / point)
        assert victim.exists() == (point == "mid:M5:item-validated")
        release(hooks, point)

    assert process.wait(timeout=20) == 0
    assert not victim.exists()


def test_a_failed_copy_reports_failure_and_leaves_the_destination_alone(hooks, tmp_path):
    dest = tmp_path / "base" / "a.bin"
    dest.parent.mkdir()
    dest.write_bytes(b"keep me")

    result = run_effects(hooks, "mover_copy_item", str(tmp_path / "missing"), str(dest), "0")

    assert result.returncode != 0
    assert dest.read_bytes() == b"keep me"
