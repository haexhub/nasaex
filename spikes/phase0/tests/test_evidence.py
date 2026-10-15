"""Evidence archive: sanitised, deterministic evidence.tar.xz within the size target;
disk images, serial logs of passing scenarios and file contents are excluded."""

import hashlib
import os
import tarfile

from testlib.evidence import MAX_ARCHIVE_BYTES, build_evidence, trim_log
from testlib.sanitiser import Sanitiser

SANITISER = Sanitiser(user_names=("alice",), host_names=("buildbox",), host_roots=("/srv/work",))


def stage(tmp_path, files):
    staging = tmp_path / "staging"
    for name, data in files.items():
        path = staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return staging


def members(archive_path):
    with tarfile.open(archive_path, "r:xz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}


def test_archive_is_named_evidence_tar_xz_and_holds_the_kept_files(tmp_path):
    staging = stage(
        tmp_path,
        {
            "junit.xml": "<testsuites/>",
            "driver.log": "driver output",
            "environment.json": "{}",
            "manifests/expected-S1.json": "{}",
            "snapraid/sync.log": "log",
            "status/status.json": "{}",
        },
    )

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing={"S1.1"})

    assert result.path.name == "evidence.tar.xz"
    assert set(members(result.path)) == {
        "junit.xml",
        "driver.log",
        "environment.json",
        "manifests/expected-S1.json",
        "snapraid/sync.log",
        "status/status.json",
    }
    assert result.sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert result.within_target


def test_host_data_is_removed_before_packing_and_guest_paths_survive(tmp_path):
    staging = stage(
        tmp_path,
        {"driver.log": "from /home/alice/x on buildbox, mounted /mnt/archive/disk0, /srv/work/s"},
    )

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing=set())

    text = members(result.path)["driver.log"].decode()
    assert "alice" not in text and "buildbox" not in text and "/srv/work" not in text
    assert "/mnt/archive/disk0" in text


def test_disk_images_and_file_contents_are_excluded(tmp_path):
    staging = stage(
        tmp_path,
        {
            "driver.log": "x",
            "disks/empty0.qcow2": b"QFI\xfb" + b"\0" * 1024,
            "manifests/disk.img": b"\0" * 1024,
            "content/live/one.bin": b"user data",
        },
    )

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing=set())

    assert set(members(result.path)) == {"driver.log"}
    assert {"disks/empty0.qcow2", "manifests/disk.img", "content/live/one.bin"} <= set(
        result.excluded
    )


def test_serial_logs_of_passing_scenarios_are_excluded_and_failing_ones_kept(tmp_path):
    staging = stage(
        tmp_path,
        {"serial/S1.1.log": "boot noise " * 100, "serial/S1.2.log": "kernel panic context"},
    )

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing={"S1.1"})

    assert set(members(result.path)) == {"serial/S1.2.log"}
    assert "serial/S1.1.log" in result.excluded


def test_member_hashes_are_those_of_the_sanitised_bytes(tmp_path):
    staging = stage(tmp_path, {"driver.log": "by alice"})

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing=set())

    stored = members(result.path)["driver.log"]
    assert result.members["driver.log"] == hashlib.sha256(stored).hexdigest()


def test_packing_twice_gives_identical_bytes(tmp_path):
    staging = stage(tmp_path, {"driver.log": "a" * 1000, "environment.json": "{}"})

    first = build_evidence(staging, tmp_path / "one", SANITISER, passing=set())
    os.utime(staging / "driver.log", (1, 1))
    second = build_evidence(staging, tmp_path / "two", SANITISER, passing=set())

    assert first.sha256 == second.sha256


def test_a_compressible_log_stays_within_the_two_mib_target(tmp_path):
    staging = stage(tmp_path, {"driver.log": "boot line " * 2_000_000})

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing=set())

    assert MAX_ARCHIVE_BYTES == 2 * 1024 * 1024
    assert result.size <= MAX_ARCHIVE_BYTES
    assert result.within_target


def test_an_archive_over_the_target_is_reported(tmp_path):
    staging = stage(tmp_path, {"driver.log": os.urandom(3 * 1024 * 1024).hex()})

    result = build_evidence(staging, tmp_path / "out", SANITISER, passing=set())

    assert not result.within_target


def test_trim_log_keeps_head_and_tail_with_a_marker():
    text = "\n".join(f"line {i}" for i in range(1000))

    trimmed = trim_log(text, max_lines=100)

    lines = trimmed.splitlines()
    assert lines[0] == "line 0" and lines[-1] == "line 999"
    assert any("trimmed" in line for line in lines)
    assert len(lines) <= 101
    assert trim_log("short", max_lines=100) == "short"
