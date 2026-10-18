"""Checks S1.8 and S1.9 (vm-tests/s1/checks_large_space.py): the decisions behind S1.9 over
fabricated observations, and the order in which S1.9 acts. Large files, the filler and the
client writes themselves need the VMs."""

from helpers import RecordingMachine
from s1 import checks_large_space
from s1.checks_large_space import check_pool_out_of_space, space_failures
from s1.context import Lab
from s1.fixture import ARCHIVE

from testlib.faults import FaultEvent
from testlib.recorder import Recorder

TABLE = {"errno": "ENOSPC", "smb_status": "NT_STATUS_DISK_FULL", "write_bytes": 1000}
CLEAN = {
    "errnos": {"create": "ENOSPC", "append": "ENOSPC"},
    "smbclient_output": "putting file ... NT_STATUS_DISK_FULL closing remote file",
    "changed_archive_branches": [],
    "new_sizes": {"s1.9/new.bin": None, "s1.9/new-smbclient.bin": 1000},
    "append_head_intact": True,
}


def test_a_clean_out_of_space_error_everywhere_passes():
    assert space_failures(TABLE, **CLEAN) == []


def test_another_errno_a_missing_smb_status_or_a_written_archive_fails():
    observed = {
        **CLEAN,
        "errnos": {"create": None, "append": "EIO"},
        "smbclient_output": "putting file ... NT_STATUS_OK",
        "changed_archive_branches": ["arch2"],
    }

    assert space_failures(TABLE, **observed) == [
        "create returned errno None, expected ENOSPC",
        "append returned errno EIO, expected ENOSPC",
        "smbclient did not report NT_STATUS_DISK_FULL",
        "archive branch arch2 changed",
    ]


def test_a_shorter_file_at_a_new_path_or_a_damaged_append_target_fails():
    observed = {
        **CLEAN,
        "new_sizes": {"s1.9/new.bin": 0, "s1.9/new-smbclient.bin": 999},
        "append_head_intact": False,
    }

    assert space_failures(TABLE, **observed) == [
        "s1.9/new.bin is visible with 0 of 1000 bytes",
        "s1.9/new-smbclient.bin is visible with 999 of 1000 bytes",
        "the appended file no longer begins with its original bytes",
    ]


class Logged(RecordingMachine):
    """A recording machine whose commands also go into a log shared with the other machine."""

    def __init__(self, name, log, outputs):
        super().__init__(outputs)
        self.name, self.log = name, log

    def succeed(self, command, **kwargs):
        self.log.append((self.name, command))
        return super().succeed(command, **kwargs)


def _step(machine, command):
    if machine == "filler":
        return "filler"
    if machine == "client" and command.endswith((" wb 1000", " ab 1000")):
        return "create" if " wb " in command else "append"
    if machine == "client" and "-c 'put " in command:
        return "put"
    if machine == "server" and command.startswith("rm -rf -- /mnt/pool/s1.9/"):
        return "remove " + command.rsplit("/", 1)[1]
    if machine == "server" and command == "rm -f /mnt/pool/.spike-filler":
        return "unfill"
    return None


def test_each_write_of_s1_9_starts_from_the_precondition(tmp_path, monkeypatch):
    # S1 shakedown (T051): the cifs create left a partial file that used up the free bytes,
    # so the smbclient upload after it met a pool below minfreespace instead of the
    # precondition of the criteria. Each write gets its own filler, and the file a write
    # left on the pool is removed (on the pool branch, so an archive copy would stay for the
    # tree hashes) before the next one.
    monkeypatch.chdir(tmp_path)
    log = []
    outputs = {
        "build_manifest": "{}",
        "errorcode": '{"errno": "ENOSPC", "written": 1000}',
        "stat -c %s": "10",
        "df --output=avail": "100",
        "sha256sum": "0" * 64,
    }
    server, client = Logged("server", log, outputs), Logged("client", log, outputs)
    tables = {
        "out_of_space": {
            "errno": "ENOSPC",
            "smb_status": "NT_STATUS_DISK_FULL",
            "leave_bytes": 100,
            "write_bytes": 1000,
        }
    }
    roots = {"pool": "/mnt/pool"} | {a: f"/mnt/archive/{a}/branch" for a in ARCHIVE}
    lab = Lab(server, client, roots, Recorder(), user=None, tables=tables)

    def filler(machine, mountpoint, leave_bytes, point=""):
        log.append(("filler", mountpoint))
        return FaultEvent("enospc", point, "filled")

    monkeypatch.setattr(checks_large_space.faults, "enospc", filler)
    lab.recorder.run("S1.9", lambda: check_pool_out_of_space(lab))

    steps = [s for s in (_step(m, c) for m, c in log) if s]
    assert steps[:11] == [
        "filler",
        "create",
        "remove new.bin",
        "unfill",
        "filler",
        "put",
        "remove new-smbclient.bin",
        "unfill",
        "filler",
        "append",
        "unfill",
    ]
