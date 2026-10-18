"""The ENOSPC probes (vm-tests/probes/probe_enospc_control.py, probe_nc_moveonenospc.py): the
guest writer that reports the step and the bytes of a failing write, the seeded source both
VMs generate, and the classification of what a failed write left behind. ENOSPC itself is
simulated with /dev/full; the full pool needs the VMs."""

import hashlib
import json
import subprocess
import sys

import pytest
from probes.enospc_writes import SOURCE_SCRIPT, WRITER, leftover

BLOCK = 1 << 20


def python(code: str, *args) -> str:
    """Run guest Python code locally with stringified arguments and return stdout."""
    done = subprocess.run(
        [sys.executable, "-c", code, *map(str, args)], capture_output=True, text=True, check=True
    )
    return done.stdout


@pytest.fixture
def source(tmp_path):
    """Create a seeded source file spanning three blocks and a partial block."""
    path = tmp_path / "source.bin"
    python(SOURCE_SCRIPT, path, 3 * BLOCK + 5)
    return path


def test_the_source_is_seeded_and_the_same_on_every_machine(tmp_path, source):
    """Check source generation is deterministic, correctly sized and varied."""
    again = tmp_path / "again.bin"
    python(SOURCE_SCRIPT, again, 3 * BLOCK + 5)
    assert source.read_bytes() == again.read_bytes()
    assert source.stat().st_size == 3 * BLOCK + 5
    assert len(set(source.read_bytes()[:4096])) > 200  # not a constant pattern


def test_a_complete_write_copies_the_source_and_reports_no_error(tmp_path, source):
    """Check a successful write copies every byte and reports no failed step."""
    target = tmp_path / "target.bin"
    report = json.loads(python(WRITER, target, "wb", source, 3 * BLOCK + 5))
    assert report == {"errno": None, "step": None, "written": 3 * BLOCK + 5}
    assert target.read_bytes() == source.read_bytes()


def test_an_append_keeps_the_head_and_adds_the_source(tmp_path, source):
    """Check an append preserves existing bytes and copies the requested prefix."""
    target = tmp_path / "target.bin"
    target.write_bytes(b"head")
    json.loads(python(WRITER, target, "ab", source, BLOCK))
    assert target.read_bytes() == b"head" + source.read_bytes()[:BLOCK]


def test_a_full_device_reports_enospc_at_the_write_step(source):
    """Check a full device reports ENOSPC before accepting any bytes."""
    report = json.loads(python(WRITER, "/dev/full", "wb", source, BLOCK))
    assert report == {"errno": "ENOSPC", "step": "write", "written": 0}


def sha(data: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of the supplied bytes."""
    return hashlib.sha256(data).hexdigest()


SOURCE = bytes(range(256)) * 16


@pytest.mark.parametrize(
    ("size", "content", "expected"),
    [
        (None, None, "absent"),
        (len(SOURCE), SOURCE, "complete"),
        (100, SOURCE[:100], "partial, prefix intact"),
        (100, bytes(100), "partial, prefix differs"),
        (0, b"", "empty"),
        (len(SOURCE), bytes(len(SOURCE)), "full size, content differs"),
    ],
)
def test_leftover_classifies_size_and_prefix(size, content, expected):
    """Check leftover classification for absent, empty, intact and differing files."""
    found = None if size is None else {"size": size, "sha256": sha(content)}
    prefix = None if size is None else sha(SOURCE[:size])
    assert leftover(found, prefix, len(SOURCE)) == expected
