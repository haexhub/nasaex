"""How `spikectl run` starts processes on the host: the driver in its own process group, killed
with that group at the budget or on an interruption, and host commands whose failure is a
RunError."""

import contextlib
import os
import signal
import time
from pathlib import Path

import pytest

from spikectl import run as run_module
from spikectl.run import RunError


def test_launch_kills_the_process_group_when_wait_is_interrupted(monkeypatch, tmp_path):
    class InterruptedProcess:
        pid = 123

        def __init__(self):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return -signal.SIGKILL

    process = InterruptedProcess()
    killed = []
    monkeypatch.setattr(run_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(run_module.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(KeyboardInterrupt):
        run_module._launch(["driver"], tmp_path, {}, tmp_path / "driver.log", timeout_s=1)

    assert killed == [(123, signal.SIGKILL)]
    assert process.waits == 2


def test_an_interrupted_launch_whose_driver_is_already_gone_still_re_raises(monkeypatch, tmp_path):
    class GoneProcess:
        pid = 123
        waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return 0

    def no_such_group(_pid, _sig):
        raise ProcessLookupError

    process = GoneProcess()
    monkeypatch.setattr(run_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(run_module.os, "killpg", no_such_group)

    with pytest.raises(KeyboardInterrupt):
        run_module._launch(["driver"], tmp_path, {}, tmp_path / "driver.log", timeout_s=1)

    assert process.waits == 2


def process_ended(pid: int, within_s: float = 5.0) -> bool:
    """Whether `pid` is gone (or a zombie awaiting its reaper) within `within_s` seconds."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.05)
    return False


def test_a_driver_over_its_budget_is_killed_with_its_whole_process_group(tmp_path):
    script = "sleep 60 & echo $! > vm.pid; wait"
    env = {"PATH": os.environ["PATH"]}

    status = run_module._launch(["sh", "-c", script], tmp_path, env, tmp_path / "log", timeout_s=1)

    vm = int((tmp_path / "vm.pid").read_text())
    try:
        assert status is None
        assert process_ended(vm), "the driver's child (a VM) outlived the budget"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(vm, signal.SIGKILL)


def test_a_failing_host_command_is_a_run_error_with_its_stderr(tmp_path):
    with pytest.raises(RunError, match="failed: boom$"):
        run_module._command(["sh", "-c", "echo boom >&2; exit 3"], tmp_path)
