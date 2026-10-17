"""Single-spike execution core of `spikectl run` (task T049a). No VM runs: the nix commands, the
driver process and the facts of the host (/proc, free space, KVM) are replaced by fakes, the
rest (preflight and mode gate of spikectl.record, record writer, evidence) is the real code."""

import datetime
import json
import shutil
import signal
import tarfile
import tempfile
from pathlib import Path

import pytest
from helpers import (
    CRITERIA,
    RESULTS,
    commit_all,
    criteria_doc,
    init_repo,
    make_root,
    write_criteria,
)

from spikectl import cli
from spikectl import run as run_module
from spikectl.freeze import freeze
from spikectl.model import schema_errors
from spikectl.record import PreflightError
from spikectl.run import GRACE_S, Host, RunError, run_spike

STORE = Path("/nix/store/0000000000000000000000000000000-nixos-test-driver-s1")
START = datetime.datetime(2026, 10, 22, 10, 15, 0, tzinfo=datetime.UTC)
TOPOLOGY = {
    "profile": "large",
    "vmResources": {
        "server": {"vcpus": 2, "memory_mib": 1536},
        "client": {"vcpus": 1, "memory_mib": 768},
    },
    "disks": [
        {"serial": "pool0", "role": "pool", "sizeMiB": 8192, "filesystem": "btrfs", "image": "a"},
        {
            "serial": "arch0",
            "role": "archive",
            "sizeMiB": 6144,
            "filesystem": "btrfs",
            "image": "b",
        },
        {
            "serial": "parity0",
            "role": "parity",
            "sizeMiB": 12288,
            "filesystem": "ext4",
            "image": "c",
        },
        {
            "serial": "spare0",
            "role": "replacement",
            "sizeMiB": 6144,
            "filesystem": "",
            "image": "d",
        },
    ],
    "archiveLayout": [],
}
SPIKE_META = {"topology": TOPOLOGY, "totalVmRamMiB": 2304, "budgetMinutes": 20}
CPUINFO = "processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel name\t: Some CPU\n"
MEMINFO = "MemTotal:       32768000 kB\nMemAvailable:   16000000 kB\n"
GIB = 2**30


class FakeHost(Host):
    """The host with nix, the driver and /proc replaced; `report` is what the test script
    writes into its output directory (None: it writes nothing)."""

    def __init__(self, state_base, *, fstype="ext4", free_gib=200, ram=16000, kvm=True, **kw):
        self.calls: list[list[str]] = []
        self.launches: list[dict] = []
        self.state_base = state_base
        self.fstype = fstype
        self.free_gib = free_gib
        self.report = kw.pop("report", {"scenarios": [], "tool_versions": {}})
        self.status = kw.pop("status", 0)
        self.meta = kw.pop("meta", SPIKE_META)
        self.times = iter([START, START + datetime.timedelta(minutes=12)])
        kvm_path = Path(tempfile.mkdtemp(prefix="p0kvm")) / "kvm"
        if kvm:
            kvm_path.write_text("")
        super().__init__(
            command=self._command,
            launch=self._launch,
            read_proc=self._read_proc,
            free_bytes=lambda _path: self.free_gib * GIB,
            available_ram_mib=lambda: ram,
            kvm_path=kvm_path,
            clock=lambda: next(self.times),
        )

    def _command(self, argv, cwd):
        self.calls.append(argv)
        if argv[:2] == ["nix", "build"]:
            return f"{STORE}\n"
        if argv[:2] == ["nix", "eval"]:
            if self.meta is None:
                raise RunError("attribute 'spike' missing")
            return json.dumps(self.meta)
        raise AssertionError(f"unexpected command {argv}")

    def _launch(self, argv, cwd, env, log, timeout_s):
        state = Path(env["XDG_RUNTIME_DIR"]).parent
        self.launches.append(
            {
                "argv": argv,
                "cwd": cwd,
                "env": env,
                "timeout": timeout_s,
                "state_existed": state.is_dir(),
                "run": Path(env["XDG_RUNTIME_DIR"]),
            }
        )
        (Path(env["XDG_RUNTIME_DIR"]) / "vm-state-server").mkdir()
        log.write_text(f"driver log of {cwd}\n")
        (Path(cwd) / "junit.xml").write_text("<testsuites/>\n")
        if self.report is not None:
            (Path(cwd) / "spike-result.json").write_text(json.dumps(self.report))
        return self.status

    def _read_proc(self, name):
        if name == "self/mounts":
            base = self.state_base.resolve()
            return f"/dev/root / ext4 rw 0 0\nfs {base} {self.fstype} rw 0 0\n"
        return {"cpuinfo": CPUINFO, "meminfo": MEMINFO}[name]


@pytest.fixture
def state_base():
    # short and outside every repository: the driver's socket paths limit the length
    path = Path(tempfile.mkdtemp(prefix="p0st"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def root(tmp_path):
    root = make_root(tmp_path / "repo")
    write_criteria(root, criteria_doc(scenarios=3))
    return root


@pytest.fixture
def locked_root(root):
    lock = root / "spikes/phase0/flake.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"nodes": {"nixpkgs": {"locked": {"rev": "a" * 40}}}}))
    init_repo(root)
    freeze(root, "S1")
    commit_all(root, "docs(phase0): freeze S1 criteria")
    return root


def env_for(state_base, **extra):
    return {"SPIKE_STATE_DIR": str(state_base), "PATH": "/bin", "DISPLAY": ":0", **extra}


def scenario(sid, outcome="pass"):
    return {"id": sid, "outcome": outcome, "duration_s": 1.0}


# --- execution of the cached driver ---------------------------------------------------------------


def test_shakedown_runs_the_cached_driver_outside_the_sandbox_in_a_fresh_state_dir(
    root, state_base
):
    host = FakeHost(state_base, report={"scenarios": [scenario("S1.1")], "tool_versions": {}})

    out = run_spike(root, "S1", "shakedown", only="S1.1", env=env_for(state_base), host=host)

    assert host.calls[0][:3] == ["nix", "build", "--no-link"]
    assert host.calls[0][-1] == "./spikes/phase0#driver-s1"
    assert host.calls[1][:3] == ["nix", "eval", "--json"]
    assert host.calls[1][-1] == "./spikes/phase0#driver-s1.spike"
    (launch,) = host.launches
    assert launch["argv"][0] == str(STORE / "bin/nixos-test-driver")
    assert launch["argv"][1:] == ["--output_directory", str(out), "--junit-xml", "junit.xml"]
    assert launch["cwd"] == out
    assert launch["run"] == state_base / "S1-20261022T101500Z" / "run"
    assert launch["env"]["SPIKE_ONLY"] == "S1.1"
    assert "DISPLAY" not in launch["env"]
    assert out == root / "spikes/phase0/scratch/S1/S1-20261022T101500Z"
    assert not launch["run"].exists(), "machine state is removed after the run"
    assert not (root / RESULTS / "S1").exists(), "shakedown never writes a record"
    outcome = json.loads((out / "outcome.json").read_text())
    assert [s["id"] for s in outcome["scenarios"]] == ["S1.1"]


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


def test_run_removes_machine_state_when_driver_launch_is_interrupted(root, state_base):
    host = FakeHost(state_base)

    def interrupt(*_args):
        raise KeyboardInterrupt

    host.launch = interrupt
    with pytest.raises(KeyboardInterrupt):
        run_spike(root, "S1", "shakedown", env=env_for(state_base), host=host)

    assert not (state_base / "S1-20261022T101500Z" / "run").exists()


def test_every_run_gets_its_own_state_directory(root, state_base):
    first = FakeHost(state_base)
    run_spike(root, "S1", "shakedown", env=env_for(state_base), host=first)
    second = FakeHost(state_base)
    second.times = iter([START + datetime.timedelta(hours=1), START + datetime.timedelta(hours=2)])

    run_spike(root, "S1", "shakedown", env=env_for(state_base), host=second)

    assert first.launches[0]["run"] != second.launches[0]["run"]
    assert "SPIKE_ONLY" not in second.launches[0]["env"]


def test_an_only_id_that_the_criteria_do_not_name_is_refused(root, state_base):
    host = FakeHost(state_base)
    with pytest.raises(RunError, match="S1.9"):
        run_spike(root, "S1", "shakedown", only="S1.9", env=env_for(state_base), host=host)
    assert host.launches == []


def test_a_driver_without_topology_is_refused_before_anything_starts(root, state_base):
    host = FakeHost(state_base, meta=None)
    with pytest.raises(RunError):
        run_spike(root, "S1", "shakedown", env=env_for(state_base), host=host)
    assert host.launches == []


# --- preflight before either mode -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shakedown", "record"])
def test_the_ram_budget_preflight_runs_before_either_mode(locked_root, state_base, mode):
    host = FakeHost(state_base)
    env = env_for(state_base, SPIKE_VM_RAM_BUDGET_MIB="2048")

    with pytest.raises(PreflightError, match="budget"):
        run_spike(locked_root, "S1", mode, env=env, host=host)
    assert host.launches == []


def test_record_mode_without_kvm_never_starts(locked_root, state_base):
    host = FakeHost(state_base, kvm=False)
    with pytest.raises(PreflightError, match="kvm"):
        run_spike(locked_root, "S1", "record", env=env_for(state_base), host=host)
    assert host.launches == []


@pytest.mark.parametrize("mode", ["shakedown", "record"])
def test_a_state_directory_on_a_tmpfs_is_refused(locked_root, state_base, mode):
    host = FakeHost(state_base, fstype="tmpfs")
    with pytest.raises(PreflightError, match="tmpfs"):
        run_spike(locked_root, "S1", mode, env=env_for(state_base), host=host)
    assert host.launches == []


def test_record_mode_refuses_less_than_100_gib_free_in_the_state_directory(locked_root, state_base):
    host = FakeHost(state_base, free_gib=99)
    with pytest.raises(PreflightError, match="100 GiB"):
        run_spike(locked_root, "S1", "record", env=env_for(state_base), host=host)
    assert host.launches == []


def test_shakedown_runs_with_less_free_space_and_keeps_the_check_result(root, state_base):
    host = FakeHost(state_base, free_gib=40)

    out = run_spike(root, "S1", "shakedown", env=env_for(state_base), host=host)

    checks = json.loads((out / "environment.json").read_text())["preflight"]
    assert checks["state_dir"] == {
        "filesystem": "ext4",
        "free_gib": 40,
        "required_free_gib": 100,
        "enough_free": False,
    }
    assert checks["vm_ram"] == {"budget_mib": 8192, "planned_mib": 2304, "available_mib": 16000}
    assert str(state_base) not in (out / "environment.json").read_text()


def test_a_state_directory_path_too_long_for_the_driver_sockets_is_refused(root, tmp_path):
    base = tmp_path / ("x" * 80)
    host = FakeHost(base)
    with pytest.raises(PreflightError, match="too long"):
        run_spike(root, "S1", "shakedown", env=env_for(base), host=host)
    assert host.launches == []


# --- budget ---------------------------------------------------------------------------------------


def test_scenarios_unfinished_at_the_budget_become_errors(root, state_base):
    report = {"scenarios": [scenario("S1.1")], "tool_versions": {}}
    host = FakeHost(state_base, report=report, status=None)

    out = run_spike(root, "S1", "shakedown", env=env_for(state_base), host=host)

    assert host.launches[0]["timeout"] == 20 * 60 + GRACE_S
    outcome = json.loads((out / "outcome.json").read_text())
    by_id = {s["id"]: s for s in outcome["scenarios"]}
    assert by_id["S1.1"]["outcome"] == "pass"
    assert [by_id[i]["outcome"] for i in ("S1.2", "S1.3")] == ["error", "error"]
    assert "budget" in by_id["S1.2"]["notes"]
    assert outcome["time_budget"] == {"budget_s": 1200, "finished_within_budget": False}


def test_a_driver_that_reports_nothing_turns_every_scenario_into_an_error(root, state_base):
    host = FakeHost(state_base, report=None, status=1)

    out = run_spike(root, "S1", "shakedown", env=env_for(state_base), host=host)

    outcome = json.loads((out / "outcome.json").read_text())
    assert {s["outcome"] for s in outcome["scenarios"]} == {"error"}
    assert "status 1" in outcome["scenarios"][0]["notes"]


# --- record mode ----------------------------------------------------------------------------------


def test_record_mode_writes_the_record_with_disks_and_vm_resources_from_the_topology(
    locked_root, state_base
):
    report = {
        "scenarios": [scenario("S1.1"), scenario("S1.2"), scenario("S1.3", "fail")],
        "tool_versions": {"kernel": "6.18.56", "qemu": "10.2.4", "samba": "4.23.10"},
        "required_settings": {"create_policy": "ff"},
    }
    host = FakeHost(state_base, report=report)

    target = run_spike(locked_root, "S1", "record", env=env_for(state_base), host=host)

    assert target == locked_root / RESULTS / "S1" / "S1-20261022T101500Z-v1"
    record = json.loads((target / "record.json").read_text())
    assert schema_errors(locked_root, "result-record.schema.json", record) == []
    env = record["environment"]
    assert env["disks"] == [
        {"serial": "pool0", "role": "pool", "size_mib": 8192, "filesystem": "btrfs"},
        {"serial": "arch0", "role": "archive", "size_mib": 6144, "filesystem": "btrfs"},
        {"serial": "parity0", "role": "parity", "size_mib": 12288, "filesystem": "ext4"},
        {"serial": "spare0", "role": "replacement", "size_mib": 6144},
    ]
    assert env["vm_resources"] == TOPOLOGY["vmResources"]
    assert env["profile"] == "large" and env["kvm"] is True
    assert env["nixpkgs_rev"] == "a" * 40
    assert env["host"] == {
        "cpu_class": "AuthenticAMD family 25",
        "vcpus": env["host"]["vcpus"],
        "memory_gib": 31,
    }
    assert record["verdict"] == "FAIL" and "fallback_decision" in record
    assert record["required_settings"] == {"create_policy": "ff"}
    assert record["date"] == {"start": "2026-10-22T10:15:00Z", "end": "2026-10-22T10:27:00Z"}
    assert str(state_base) not in (target / "record.json").read_text()


def test_record_mode_keeps_the_check_results_in_the_evidence_without_host_paths(
    locked_root, state_base
):
    report = {
        "scenarios": [scenario(f"S1.{n}") for n in (1, 2, 3)],
        "tool_versions": {"kernel": "6.18.56", "qemu": "10.2.4"},
    }
    host = FakeHost(state_base, report=report)

    target = run_spike(locked_root, "S1", "record", env=env_for(state_base), host=host)

    with tarfile.open(target / "evidence.tar.xz") as tar:
        names = tar.getnames()
        environment = json.loads(tar.extractfile("environment.json").read())
        log = tar.extractfile("driver.log").read().decode()
    assert {"environment.json", "driver.log", "junit.xml"} <= set(names)
    assert environment["preflight"]["state_dir"]["enough_free"] is True
    assert environment["preflight"]["vm_ram"]["planned_mib"] == 2304
    assert environment["time_budget"]["finished_within_budget"] is True
    assert str(state_base) not in log
    assert json.loads((target / "record.json").read_text())["verdict"] == "PASS"


# --- command line ---------------------------------------------------------------------------------


def test_the_run_verb_hands_spike_mode_and_only_to_the_core(monkeypatch, root, capsys):
    seen = {}

    def fake_run(root_arg, spike, mode, *, only, **_):
        seen.update(root=root_arg, spike=spike, mode=mode, only=only)
        return root_arg / "out"

    monkeypatch.setattr(cli, "run_spike", fake_run)

    status = cli.main(["--root", str(root), "run", "S1", "--mode", "shakedown", "--only", "S1.2"])

    assert status == 0
    assert seen == {"root": root, "spike": "S1", "mode": "shakedown", "only": "S1.2"}
    assert "out" in capsys.readouterr().out


def test_the_run_verb_reports_a_refused_preflight(monkeypatch, root, capsys):
    def refuse(*_args, **_kwargs):
        raise PreflightError("no usable kvm device")

    monkeypatch.setattr(cli, "run_spike", refuse)

    assert cli.main(["--root", str(root), "run", "S1", "--mode", "record"]) == 1
    assert "kvm" in capsys.readouterr().err
    assert (root / CRITERIA / "S1.yaml").exists()
