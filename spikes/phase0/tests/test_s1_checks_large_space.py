"""Checks S1.8 and S1.9 (vm-tests/s1/checks_large_space.py): the order in which S1.9 acts and
what it reads, against recording machines (the decisions behind S1.9 are tested in
test_s1_checks_space_leftover.py, task T047a). Large files, the filler and the client writes
themselves need the VMs."""

import hashlib

from helpers import LocalMachine, RecordingMachine
from s1 import checks_large_space
from s1.checks_large_space import check_pool_out_of_space
from s1.context import Lab
from s1.fixture import ARCHIVE

from testlib.faults import FaultEvent
from testlib.recorder import Recorder


class Logged(RecordingMachine):
    """A recording machine whose commands also go into a log shared with the other machine;
    `outputs` maps a command fragment to its output, as in RecordingMachine."""

    def __init__(self, name, log, outputs):
        super().__init__(outputs)
        self.name, self.log = name, log

    def succeed(self, command, **kwargs):
        self.log.append((self.name, command))
        return super().succeed(command, **kwargs)

    def wait_until_succeeds(self, command, **_):
        self.log.append((self.name, command))


SOURCE_ARGS = "/root/s1.9-source.bin 1000"


def _step(machine, command):
    if machine == "filler":
        return "filler"
    if machine == "client" and command.endswith((f" wb {SOURCE_ARGS}", f" ab {SOURCE_ARGS}")):
        return "create" if " wb " in command else "append"
    if machine == "client" and "-c 'put " in command:
        return "put"
    if machine == "client" and command.startswith("rm -f -- /mnt/share/s1.9/"):
        return "delete " + command.split()[3].rsplit("/", 1)[1]
    if machine == "server" and "inotifywait -m" in command:
        return "watch"
    if machine == "server" and command.startswith("touch ") and "marker" in command:
        return "end watch"
    if machine == "server" and command == "rm -f /mnt/pool/.spike-filler":
        return "unfill"
    return None


def _run_s1_9(tmp_path, monkeypatch, filler=None, client_sha=None):
    """Run check S1.9 against recording machines; the shared command log and the scenarios."""
    monkeypatch.chdir(tmp_path)
    log = []
    outputs = {
        "build_manifest": "{}",
        "errorcode": '{"errno": "ENOSPC", "step": "fsync", "written": 1000}',
        "lexists": '["pool"]',
        "stat -c %s": "10",
        "df --output=avail": "100",
        "sha256sum": "0" * 64,
    }
    server = Logged("server", log, outputs)
    client = Logged("client", log, {**outputs, "sha256sum": client_sha or "0" * 64})
    tables = {
        "out_of_space": {
            "errno": "ENOSPC",
            "smb_status": "NT_STATUS_DISK_FULL",
            "leave_bytes": 100,
            "write_bytes": 1000,
        }
    }
    roots = {"pool": "/mnt/pool"}
    for serial in ARCHIVE:
        roots |= {
            serial: f"/mnt/archive/{serial}/branch",
            f"trash:{serial}": f"/mnt/archive/{serial}/trash",
        }
    lab = Lab(server, client, roots, Recorder(), user=None, tables=tables)

    if filler is None:

        def filler(machine, mountpoint, leave_bytes, point=""):
            log.append(("filler", mountpoint))
            return FaultEvent("enospc", point, "filled")

    monkeypatch.setattr(checks_large_space.faults, "enospc", filler)
    lab.recorder.run("S1.9", lambda: check_pool_out_of_space(lab))
    return log, lab.recorder.scenarios()


def test_each_write_of_s1_9_starts_from_the_precondition_and_is_retried_after_it(
    tmp_path, monkeypatch
):
    # Each write gets its own filler (S1 shakedown T051: a shared filler let the first partial
    # file use up the free bytes of the next) and is watched on the archive filesystems; then
    # the client deletes the leftovers of the create and the upload through the share and
    # repeats every write without a filler (spec Clarifications 2026-10-19, proposed (b)).
    log, _ = _run_s1_9(tmp_path, monkeypatch)

    steps = [s for s in (_step(m, c) for m, c in log) if s]
    assert steps == [
        *("filler", "watch", "create", "end watch", "unfill"),
        *("filler", "watch", "put", "end watch", "unfill"),
        *("filler", "watch", "append", "end watch", "unfill"),
        *("delete new.bin", "delete new-smbclient.bin", "create", "put", "append"),
        *("delete new.bin", "delete new-smbclient.bin"),
    ]


def test_s1_9_watches_the_archive_data_filesystems_and_hashes_their_trash(tmp_path, monkeypatch):
    log, _ = _run_s1_9(tmp_path, monkeypatch)

    watch = next(c for m, c in log if "inotifywait -m" in c)
    assert all(f" /mnt/archive/{serial} " in watch + " " for serial in ARCHIVE)
    manifests = [c for m, c in log if m == "server" and "build_manifest" in c]
    assert any("/mnt/archive/arch0/trash" in c for c in manifests)


def test_s1_9_records_the_sizes_of_every_view_and_reads_the_leftover_on_the_pool_branch(
    tmp_path, monkeypatch
):
    log, _ = _run_s1_9(tmp_path, monkeypatch)

    commands = [c for _, c in log]
    for rel in ("s1.9/new.bin", "s1.9/new-smbclient.bin", "s1.9/append.bin"):
        assert f"stat -c %s /mnt/share/{rel}" in commands
        assert f"stat -c %s /mnt/share2/{rel}" in commands
        assert f"stat -c %s /mnt/cache/{rel}" in commands
        assert f"stat -c %s /mnt/pool/{rel}" in commands
    assert any("if=/mnt/pool/s1.9/append.bin" in c and "skip=4096" in c for c in commands)


def test_s1_9_uses_one_seeded_source_checked_equal_on_both_machines(tmp_path, monkeypatch):
    _, scenarios = _run_s1_9(tmp_path, monkeypatch, client_sha="1" * 64)

    assert scenarios[0]["outcome"] == "error"
    assert "source" in scenarios[0]["notes"]


def test_s1_9_removes_a_filler_when_creation_fails(tmp_path, monkeypatch):
    def failing_filler(machine, mountpoint, leave_bytes, point=""):
        raise RuntimeError("filler failed")

    log, scenarios = _run_s1_9(tmp_path, monkeypatch, failing_filler)

    assert scenarios[0]["outcome"] == "error"
    assert ("server", "rm -f /mnt/pool/.spike-filler") in log


def test_a_byte_range_of_a_guest_file_hashes_as_those_bytes(tmp_path):
    # The leftover, the source prefix and the retry's read-back are compared by these hashes
    # (task T047a); the guest shell runs with pipefail, as LocalMachine does.
    data = bytes(range(256)) * 9000
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    machine = LocalMachine()
    sha = checks_large_space._range_sha256

    assert sha(machine, str(path)) == hashlib.sha256(data).hexdigest()
    assert sha(machine, str(path), 4096) == hashlib.sha256(data[4096:]).hexdigest()
    assert sha(machine, str(path), 4096, 100_000) == hashlib.sha256(data[4096:104_096]).hexdigest()
    assert sha(machine, str(path), 0, 0) == hashlib.sha256(b"").hexdigest()
    assert sha(machine, str(path), len(data) + 10) == hashlib.sha256(b"").hexdigest()
    assert sha(machine, str(tmp_path / "missing.bin")) is None
