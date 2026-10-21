"""The negative-control variants of spikes/phase0/controls/ (research D7): each definition
declares the control, the invariant it violates and the scenario that must flag it as FAIL, and
the control changes its input as declared. Task T064 adds the corrupted-parity control of S2.
Whether the S2 scenarios flag it is measured in the VM (probe T064), not here."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

CONTROLS = Path(__file__).resolve().parents[1] / "controls"
CORRUPTED_PARITY = CONTROLS / "corrupted-parity.sh"
PATTERN = b"\xa5"


def definition(path: Path) -> dict[str, str]:
    """The `#: key: value` lines of a control script."""
    fields = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"#: ([a-z-]+): (.+)", line)
        if match:
            fields[match[1]] = match[2]
    return fields


def test_the_corrupted_parity_control_declares_its_invariant_and_the_scenario_that_flags_it():
    fields = definition(CORRUPTED_PARITY)

    assert fields["control"] == "corrupted-parity"
    assert "bit-exact" in fields["violates"]
    assert fields["flagged-by"] == "S2.1"
    assert os.access(CORRUPTED_PARITY, os.X_OK)


@pytest.fixture
def array(tmp_path):
    """A SnapRAID config with one parity file of 3 blocks of 256 KiB of random bytes."""
    parity = tmp_path / "parity" / "snapraid.parity"
    parity.parent.mkdir()
    parity.write_bytes(os.urandom(3 * 256 * 1024))
    conf = tmp_path / "snapraid.conf"
    conf.write_text(
        f"parity {parity}\ncontent {tmp_path}/snapraid.content\ndata arch0 {tmp_path}/arch0/\n"
    )
    return {"conf": conf, "parity": parity, "tmp": tmp_path}


def run(array, *args):
    env = {**os.environ, "SNAPRAID_CONF": str(array["conf"])}
    return subprocess.run(
        ["bash", str(CORRUPTED_PARITY), *args], env=env, timeout=30, capture_output=True, text=True
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_it_overwrites_every_byte_of_the_parity_file_in_place_and_reports_both_hashes(array):
    before = sha256(array["parity"])
    size = array["parity"].stat().st_size

    result = run(array)

    assert result.returncode == 0, result.stderr
    assert array["parity"].stat().st_size == size
    assert array["parity"].read_bytes() == PATTERN * size
    after = sha256(array["parity"])
    line = f"corrupted {array['parity']}: sha256 {before} -> {after}"
    assert result.stdout.splitlines() == [line]


def test_it_corrupts_every_parity_level_of_the_config(array):
    second = array["tmp"] / "parity" / "snapraid.2-parity"
    second.write_bytes(os.urandom(256 * 1024))
    with array["conf"].open("a") as conf:
        conf.write(f"2-parity {second}\n")

    result = run(array)

    assert result.returncode == 0, result.stderr
    assert array["parity"].read_bytes() == PATTERN * array["parity"].stat().st_size
    assert second.read_bytes() == PATTERN * (256 * 1024)
    assert len(result.stdout.splitlines()) == 2


def test_it_selects_comma_separated_split_and_z_parity_files(array):
    second = array["tmp"] / "parity" / "snapraid.split-2"
    third = array["tmp"] / "parity" / "snapraid.z-parity"
    second.write_bytes(os.urandom(256 * 1024))
    third.write_bytes(os.urandom(256 * 1024))
    array["conf"].write_text(
        f"parity {array['parity']},{second}\n"
        f"z-parity {third}\n"
        f"content {array['tmp']}/snapraid.content\n"
    )

    result = run(array)

    assert result.returncode == 0, result.stderr
    for parity in (array["parity"], second, third):
        assert parity.read_bytes() == PATTERN * parity.stat().st_size
    assert len(result.stdout.splitlines()) == 3


def test_it_preflights_unchanged_hashes_before_corrupting_any_file(array):
    first_before = array["parity"].read_bytes()
    second = array["tmp"] / "parity" / "snapraid.2-parity"
    second.write_bytes(PATTERN * 1024)
    with array["conf"].open("a") as conf:
        conf.write(f"2-parity {second}\n")

    result = run(array)

    assert result.returncode == 1
    assert "unchanged" in result.stderr
    assert array["parity"].read_bytes() == first_before
    assert second.read_bytes() == PATTERN * 1024


def test_a_missing_parity_file_is_refused_and_not_created(array):
    array["parity"].unlink()

    result = run(array)

    assert result.returncode == 1
    assert "corrupted-parity: refused" in result.stderr
    assert not array["parity"].exists()


def test_an_empty_parity_file_is_refused(array):
    array["parity"].write_bytes(b"")

    result = run(array)

    assert result.returncode == 1
    assert "corrupted-parity: refused" in result.stderr


def test_a_config_without_a_parity_file_is_refused(array):
    array["conf"].write_text(f"content {array['tmp']}/snapraid.content\n")

    result = run(array)

    assert result.returncode == 1
    assert "no parity file" in result.stderr


def test_a_parity_file_the_control_cannot_change_is_refused(array):
    """A control that leaves its input unchanged would pass silently: the hashes must differ."""
    array["parity"].write_bytes(PATTERN * 1024)

    result = run(array)

    assert result.returncode == 1
    assert "unchanged" in result.stderr


def test_arguments_are_refused(array):
    result = run(array, "arch0")

    assert result.returncode == 2
    assert "usage" in result.stderr
