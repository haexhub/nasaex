"""Checks S1.8 and S1.9 (vm-tests/s1/checks_large_space.py): the order in which S1.9 acts and
what it reads, against recording machines (the decisions behind S1.9 are tested in
test_s1_checks_space_leftover.py, task T047a). Large files, the filler and the client writes
themselves need the VMs."""

import hashlib
import json
from pathlib import Path

from helpers import LocalMachine, RecordingMachine
from s1 import checks_large_space
from s1.checks_large_space import check_pool_out_of_space
from s1.context import Lab
from s1.fixture import ARCHIVE

from testlib.faults import FaultEvent
from testlib.recorder import Recorder


class Logged(RecordingMachine):
    """A recording machine whose commands also go into a log shared with the other machine;
    `outputs` maps a command fragment to its output, as in RecordingMachine; an output that is
    callable is called for every command it answers."""

    def __init__(self, name, log, outputs):
        super().__init__(outputs)
        self.name, self.log = name, log

    def succeed(self, command, **kwargs):
        self.log.append((self.name, command))
        out = super().succeed(command, **kwargs)
        return out() if callable(out) else out

    def execute(self, command, **kwargs):
        """An output that is a tuple is (exit status, output); any other exits 0."""
        out = self.succeed(command, **kwargs)
        return out if isinstance(out, tuple) else (0, out)

    def wait_until_succeeds(self, command, **_):
        self.log.append((self.name, command))


SOURCE_ARGS = "/root/s1.9-source.bin 1000"
EMPTY_PUT = 'put "/root/s1.9-empty.bin"'


def _step(machine, command):
    if machine == "filler":
        return "filler"
    if machine == "client" and command.endswith(" wb /root/s1.9-source.bin 0"):
        return "empty create"
    if machine == "client" and EMPTY_PUT in command:
        return "empty put"
    if machine == "client" and command.endswith((f" wb {SOURCE_ARGS}", f" ab {SOURCE_ARGS}")):
        if " wb " in command:
            return "create"
        return "append (fresh session)" if "/mnt/share3/" in command else "append"
    if machine == "client" and "mount -t cifs" in command and "/mnt/share3" in command:
        return "mount fresh session"
    if machine == "client" and command == "umount /mnt/share3":
        return "umount fresh session"
    if machine == "server" and "df --output=avail" in command:
        return "free"
    if machine == "host":
        return command
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


class FakeClock:
    """The poll's clock and sleep: time moves only when the check sleeps."""

    def __init__(self, log):
        self.now, self.log = 0.0, log

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.log.append(("host", f"sleep {seconds}"))
        self.now += seconds


def _run_s1_9(tmp_path, monkeypatch, filler=None, client_sha=None, **kwargs):
    """Run check S1.9 against recording machines; the shared command log and the scenarios."""
    log, lab = _lab_s1_9(tmp_path, monkeypatch, filler, client_sha, **kwargs)
    return log, lab.recorder.scenarios()


ENOSPC_REPORT = '{"errno": "ENOSPC", "step": "fsync", "written": 1000}'


def _lab_s1_9(
    tmp_path,
    monkeypatch,
    filler=None,
    client_sha=None,
    space=None,
    writer_after_mount=None,
    window_create=None,
    window_put=None,
):
    """Run check S1.9 against recording machines; the shared command log and the lab.
    `space` gives the free bytes the server reports once the leftovers are deleted (a callable
    called per sample); before that the pool reports 100 bytes. `writer_after_mount` is called
    instead of the writer that runs through the session of its own. `window_create` is called
    with the fake clock for the empty mount.cifs create in the refusal window (task T047c) and
    returns the writer's report; `window_put` is the exit status and output of the empty
    smbclient put (default: exit 0, no output)."""
    monkeypatch.chdir(tmp_path)
    log = []
    clock = FakeClock(log)
    monkeypatch.setattr(checks_large_space, "monotonic", clock.monotonic)
    monkeypatch.setattr(checks_large_space, "sleep", clock.sleep)
    # The target is the filler (10) plus the two deleted leftovers (10 each), no epsilon.
    monkeypatch.setattr(checks_large_space, "SPACE_EPSILON", 0)

    def avail():
        deleted = any(c.startswith("rm -f -- /mnt/share/s1.9/new-smbclient") for _, c in log)
        return str(space() if deleted and space else 100)

    outputs = {
        "build_manifest": "{}",
        "errorcode": ENOSPC_REPORT,
        "lexists": '["pool"]',
        "stat -c %s": "10",
        "df --output=avail": avail,
        "sha256sum": "0" * 64,
    }
    server = Logged("server", log, outputs)

    def writer():
        if writer_after_mount and "/mnt/share3/" in log[-1][1]:
            return writer_after_mount()
        if window_create and "/window-create.bin " in log[-1][1]:
            return window_create(clock)
        return outputs["errorcode"]

    client = Logged(
        "client",
        log,
        {
            **outputs,
            "errorcode": writer,
            "sha256sum": client_sha or "0" * 64,
            EMPTY_PUT: window_put or (0, ""),
        },
    )
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
    return log, lab


def test_each_write_of_s1_9_starts_from_the_precondition_and_is_retried_after_it(
    tmp_path, monkeypatch
):
    # Each write gets its own filler (S1 shakedown T051: a shared filler let the first partial
    # file use up the free bytes of the next) and is watched on the archive filesystems; then
    # the client deletes the leftovers of the create and the upload through the share, creates
    # two empty files in the refusal window (task T047c; the upload succeeds here and is deleted
    # at once), the check looks at the free bytes of the pool branch (task T047b) and repeats
    # every write without a filler, the append through a mount.cifs session of its own.
    log, _ = _run_s1_9(tmp_path, monkeypatch)

    steps = [s for s in (_step(m, c) for m, c in log) if s]
    assert steps == [
        *("filler", "free", "watch", "create", "end watch", "free", "unfill"),
        *("filler", "free", "watch", "put", "end watch", "free", "unfill"),
        *("filler", "free", "watch", "append", "end watch", "free", "unfill"),
        *("delete new.bin", "delete new-smbclient.bin"),
        *("empty create", "empty put", "delete window-put.bin", "free"),
        *("create", "put", "mount fresh session", "append (fresh session)"),
        *("umount fresh session", "delete new.bin", "delete new-smbclient.bin"),
    ]


def test_the_retries_wait_for_the_freed_space_and_record_the_seconds_until_it_appeared(
    tmp_path, monkeypatch
):
    # The pool branch shows the space of the filler (10) and the deleted leftovers (10 and 10)
    # only at the fourth sample, 1.5 s after the first (btrfs frees it at its next commit).
    samples = iter([0, 0, 0, 30])

    log, _ = _run_s1_9(tmp_path, monkeypatch, space=lambda: next(samples))

    steps = [s for s in (_step(m, c) for m, c in log) if s]
    deleted = steps.index("delete window-put.bin") + 1
    assert steps[deleted : deleted + 8] == [
        *("free", "sleep 0.5", "free", "sleep 0.5", "free", "sleep 0.5", "free", "create"),
    ]
    evidence = json.loads(Path("status/S1.9-out-of-space.json").read_text())
    assert evidence["freed_space"] == {
        "appeared": True,
        "waited_s": 1.5,
        "avail": 30,
        "target": 30,
        "samples": 4,
    }


def test_the_retries_do_not_run_and_the_check_fails_when_the_space_never_appears(
    tmp_path, monkeypatch
):
    log, scenarios = _run_s1_9(tmp_path, monkeypatch, space=lambda: 0)

    steps = [s for s in (_step(m, c) for m, c in log) if s]
    deleted = steps.index("delete new-smbclient.bin") + 1
    after = steps[deleted:]
    assert after.count("sleep 0.5") == 120
    assert after[-2:] == ["delete new.bin", "delete new-smbclient.bin"]
    assert not {"create", "put", "mount fresh session"} & set(after)
    assert scenarios[0]["outcome"] == "fail"
    assert "the freed space never appeared" in scenarios[0]["notes"]
    assert "bound 60 s" in scenarios[0]["notes"]
    evidence = json.loads(Path("status/S1.9-out-of-space.json").read_text())
    assert evidence["freed_space"]["appeared"] is False


def _slow_refused_create(clock):
    """The empty mount.cifs create of the window, refused at open after 0.7 s."""
    clock.now += 0.7
    return '{"errno": "EROFS", "step": "open", "written": 0}'


REFUSED_PUT = (1, "NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file \\s1.9\\window-put.bin")


def test_the_refusal_window_counts_from_the_end_of_the_deletion_until_the_space_appeared(
    tmp_path, monkeypatch
):
    # Task T047c: the space appears at the fourth sample, 1.5 s into the poll; the empty creates
    # between the deletion and the poll took 0.7 s, so the window is 2.2 s.
    samples = iter([0, 0, 0, 30])

    _, lab = _lab_s1_9(
        tmp_path,
        monkeypatch,
        space=lambda: next(samples),
        window_create=_slow_refused_create,
        window_put=REFUSED_PUT,
    )

    settings = lab.required_settings["out_of_space"]
    assert settings["create_refused_window_s"] == 2.2
    assert settings["create_refused_window_space_appeared"] is True
    assert settings["creates_in_window"] == {
        "mount.cifs": "EROFS",
        "smbclient": "NT_STATUS_MEDIA_WRITE_PROTECTED",
    }
    assert "minfreespace" in settings
    evidence = json.loads(Path("status/S1.9-out-of-space.json").read_text())
    assert evidence["create_refused_window_s"] == 2.2
    assert evidence["freed_space"]["waited_s"] == 1.5


def test_the_refusal_window_is_recorded_when_the_bound_passes(tmp_path, monkeypatch):
    _, lab = _lab_s1_9(tmp_path, monkeypatch, space=lambda: 0)

    settings = lab.required_settings["out_of_space"]
    assert settings["create_refused_window_s"] == 60.0
    assert settings["create_refused_window_space_appeared"] is False
    assert lab.recorder.scenarios()[0]["outcome"] == "fail"


def test_the_creates_in_the_window_are_recorded_whatever_they_report_and_never_judged(
    tmp_path, monkeypatch
):
    def accepted_create(clock):
        return '{"errno": null, "step": null, "written": 0}'

    (tmp_path / "refused").mkdir()
    (tmp_path / "accepted").mkdir()
    refused_log, refused = _lab_s1_9(
        tmp_path / "refused",
        monkeypatch,
        window_create=_slow_refused_create,
        window_put=REFUSED_PUT,
    )
    accepted_log, accepted = _lab_s1_9(
        tmp_path / "accepted",
        monkeypatch,
        window_create=accepted_create,
        window_put=(0, "putting file /root/s1.9-empty.bin as \\s1.9\\window-put.bin"),
    )

    # The same outcome and failures whatever the empty creates report.
    judged = [
        [(s["outcome"], s["notes"]) for s in lab.recorder.scenarios()]
        for lab in (refused, accepted)
    ]
    assert judged[0] == judged[1]
    assert accepted.required_settings["out_of_space"]["creates_in_window"] == {
        "mount.cifs": None,
        "smbclient": None,
    }
    evidence = json.loads(Path("status/S1.9-out-of-space.json").read_text())
    assert evidence["creates_in_window"]["smbclient"]["report"][0] == 0
    # A create that succeeded is deleted through the share before the poll, a refused one is not.
    steps = {
        name: [s for s in (_step(m, c) for m, c in log) if s]
        for name, log in (("refused", refused_log), ("accepted", accepted_log))
    }
    window = steps["accepted"].index("empty create")
    assert steps["accepted"][window : window + 5] == [
        *("empty create", "empty put", "delete window-create.bin", "delete window-put.bin"),
        "free",
    ]
    window = steps["refused"].index("empty create")
    assert steps["refused"][window : window + 3] == ["empty create", "empty put", "free"]


def test_the_append_retry_goes_through_a_session_of_its_own_and_the_writers_size_is_recorded(
    tmp_path, monkeypatch
):
    log, _ = _run_s1_9(tmp_path, monkeypatch)

    client = [c for m, c in log if m == "client"]
    mount = next(c for c in client if "mount -t cifs" in c and "/mnt/share3" in c)
    assert "nosharesock" in mount
    retry = next(c for c in client if "/mnt/share3/s1.9/append.bin ab" in c)
    failed = next(c for c in client if "/mnt/share/s1.9/append.bin ab" in c)
    assert client.index(mount) < client.index(retry) and client.index(failed) < client.index(mount)
    # The writer session's size is read right before the retry and before the new session.
    sizes = [i for i, c in enumerate(client) if c == "stat -c %s /mnt/share/s1.9/append.bin"]
    assert sizes[-1] < client.index(mount)
    evidence = json.loads(Path("status/S1.9-out-of-space.json").read_text())
    assert evidence["writes"]["append"]["retry"]["sizes_recorded"] == {
        "writer session before the retry": 10,
        "fresh session before the retry": 10,
    }
    assert "sizes_recorded" not in evidence["writes"]["create"]["retry"]


def test_the_fresh_session_is_unmounted_when_the_append_retry_fails(tmp_path, monkeypatch):
    # (a raising client command is a harness error, which the recorder turns into an error)
    def failing_writer():
        raise RuntimeError("writer failed")

    log, scenarios = _run_s1_9(tmp_path, monkeypatch, writer_after_mount=failing_writer)

    assert scenarios[0]["outcome"] == "error"
    assert ("client", "umount /mnt/share3") in log


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
