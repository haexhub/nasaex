"""Single-spike execution core of `spikectl run` (task T049a; the suite orchestration of T138
builds on it). Scaffolding for the Phase 0 spikes, not product code.

One run: build the cached test driver `packages.driver-<spike>` and evaluate its topology
(passthru `spike` of nix/mkspike.nix), refuse what must not start (the RAM-budget preflight and
the mode gate of spikectl.record, then the state directory), run the driver outside the nix
sandbox in a fresh state directory on disk (research D2), bound it by the spike's budget, and
write scratch output (shakedown) or the record (record mode).

The test script of a spike runs with its output directory as working directory and reads the
environment variable SPIKE_ONLY (a scenario id, or unset). It writes `spike-result.json` there:
{"scenarios": <Recorder.scenarios()>, "tool_versions": {...}} and optionally "required_settings"
and "coverage". Every expected scenario the result lacks becomes `error`."""

import datetime
import getpass
import json
import os
import shutil
import signal
import socket
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from spikectl.model import load_criteria, read_lock, sha256_hex
from spikectl.record import (
    SCRATCH_DIR,
    PreflightError,
    _newest_criteria_file,
    build_record,
    configured_ram_budget_mib,
    preflight,
    read_available_ram_mib,
    write_record,
)
from testlib.evidence import build_evidence
from testlib.sanitiser import Sanitiser

FLAKE = "./spikes/phase0"  # relative to the repository root
FLAKE_LOCK = Path("spikes/phase0/flake.lock")
STATE_ENV = "SPIKE_STATE_DIR"
DEFAULT_STATE_DIR = Path("spikes/phase0/state")
REQUIRED_FREE_GIB = 100  # spec, Reference runner
# AF_UNIX socket paths below the state directory (and the vde plug, which resolves symlinks)
# are limited to 107 bytes; the same limit as the probe runner of nix/apps.nix.
MAX_STATE_PATH = 70
# The driver ends itself at its globalTimeout (the budget) and stops its VMs; the outer kill
# only catches a driver that does not.
GRACE_S = 120
RESULT_FILE = "spike-result.json"
GIB = 2**30


class RunError(Exception):
    """The run cannot be carried out."""


def _command(argv: list[str], cwd: Path) -> str:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RunError(f"{' '.join(argv)} failed: {done.stderr.strip()[-500:]}")
    return done.stdout


def _launch(argv: list[str], cwd: Path, env: dict, log: Path, timeout_s: float) -> int | None:
    """Run the driver; its exit status, or None when it was killed at the timeout. The driver
    runs in its own process group so that its VMs go down with it."""
    with log.open("wb") as handle:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            return process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return None


@dataclass
class Host:
    """What a run needs from the machine it runs on; replaced by fakes in the unit tests."""

    command: Callable[[list[str], Path], str] = _command
    launch: Callable[[list[str], Path, dict, Path, float], int | None] = _launch
    read_proc: Callable[[str], str] = lambda name: Path("/proc", name).read_text()
    free_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free
    available_ram_mib: Callable[[], int] = read_available_ram_mib
    kvm_path: Path = field(default_factory=lambda: Path("/dev/kvm"))
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC)


@dataclass(frozen=True)
class Driver:
    path: Path
    topology: dict  # profile, vmResources, disks, archiveLayout (nix/mkspike.nix)
    vm_ram_mib: int
    budget_s: int


def resolve_driver(root: Path, spike: str, host: Host) -> Driver:
    attr = f"{FLAKE}#driver-{spike.lower()}"
    path = host.command(["nix", "build", "--no-link", "--print-out-paths", attr], root).strip()
    try:
        meta = json.loads(host.command(["nix", "eval", "--json", f"{attr}.spike"], root))
    except RunError as error:
        raise RunError(
            f"{attr} carries no spike topology (still a placeholder?): {error}"
        ) from None
    return Driver(Path(path), meta["topology"], meta["totalVmRamMiB"], meta["budgetMinutes"] * 60)


def filesystem_type(path: Path, mounts: str) -> str:
    """Type of the filesystem holding `path`: the longest mount point of /proc/self/mounts
    that contains it (mount points there escape a space as \\040)."""
    target = path.resolve().as_posix()
    best, kind = "", "unknown"
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1].encode().decode("unicode_escape")
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) > len(best):
            best, kind = point, fields[2]
    return kind


def check_state_dir(base: Path, mode: str, host: Host) -> dict:
    """Machine state lives on a disk, never on a tmpfs; record mode needs 100 GiB free. The
    result names no path, so it can go into the evidence."""
    kind = filesystem_type(base, host.read_proc("self/mounts"))
    free_gib = host.free_bytes(base) // GIB
    result = {
        "filesystem": kind,
        "free_gib": free_gib,
        "required_free_gib": REQUIRED_FREE_GIB,
        "enough_free": free_gib >= REQUIRED_FREE_GIB,
    }
    if kind == "tmpfs":
        raise PreflightError("the state directory is on a tmpfs; machine state must be on disk")
    if mode == "record" and not result["enough_free"]:
        raise PreflightError(
            f"the state directory has {free_gib} GiB free, record mode needs "
            f"{REQUIRED_FREE_GIB} GiB"
        )
    return result


def _expected_ids(root: Path, spike: str, only: str | None) -> list[str]:
    path = _newest_criteria_file(root, spike)
    ids = [s["id"] for s in load_criteria(path)["scenarios"]] if path else []
    if only is None:
        return ids
    if ids and only not in ids:
        raise RunError(f"{only} is not a scenario of the {spike} criteria")
    return [only]


def _read_result(path: Path) -> dict:
    """What the test script reported; nothing when it wrote no (or a truncated) result."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _complete(reported: list[dict], expected: list[str], reason: str) -> list[dict]:
    """The reported scenarios plus an `error` for every expected one that never finished
    (the note format of testlib.recorder.Recorder.complete)."""
    seen = {s["id"] for s in reported}
    missing = [
        {"id": sid, "outcome": "error", "notes": f"{sid}: not finished ({reason})"}
        for sid in expected
        if sid not in seen
    ]
    return [*reported, *missing]


def _host_description(host: Host) -> dict:
    """CPU vendor and family only, never a model string or a host name."""
    cpu = dict(
        (key.strip(), value.strip())
        for key, _, value in (
            line.partition(":") for line in host.read_proc("cpuinfo").splitlines()
        )
    )
    total_kib = next(
        int(line.split()[1])
        for line in host.read_proc("meminfo").splitlines()
        if line.startswith("MemTotal:")
    )
    return {
        "cpu_class": f"{cpu.get('vendor_id', 'unknown')} family {cpu.get('cpu family', '?')}",
        "vcpus": os.cpu_count() or 1,
        "memory_gib": max(1, total_kib * 1024 // GIB),
    }


def build_environment(root: Path, driver: Driver, tool_versions: dict, host: Host) -> dict:
    lock = (root / FLAKE_LOCK).read_bytes()
    commit = _command(["git", "rev-parse", "HEAD"], root).strip()
    disks = [
        {"serial": d["serial"], "role": d["role"], "size_mib": d["sizeMiB"]}
        | ({"filesystem": d["filesystem"]} if d["filesystem"] else {})
        for d in driver.topology["disks"]
    ]
    return {
        "repo_commit": commit,
        "flake_lock_sha256": sha256_hex(lock),
        "nixpkgs_rev": json.loads(lock)["nodes"]["nixpkgs"]["locked"]["rev"],
        "profile": driver.topology["profile"],
        "disks": disks,
        "vm_resources": driver.topology["vmResources"],
        "tool_versions": tool_versions,
        "host": _host_description(host),
        "kvm": True,
    }


def _state_dir(root: Path, env: Mapping[str, str], run_id: str) -> tuple[Path, Path]:
    base = Path(env[STATE_ENV]) if env.get(STATE_ENV) else root / DEFAULT_STATE_DIR
    base.mkdir(parents=True, exist_ok=True)
    state = base.resolve() / run_id
    if len(str(state)) > MAX_STATE_PATH:
        raise PreflightError(
            f"the state directory path is too long for the driver's sockets "
            f"({len(str(state))} > {MAX_STATE_PATH}); set {STATE_ENV} to a shorter directory"
        )
    return base, state


def run_spike(
    root: Path,
    spike: str,
    mode: str,
    *,
    only: str | None = None,
    env: Mapping[str, str] = os.environ,
    host: Host | None = None,
) -> Path:
    """Run one spike; the scratch output directory (shakedown) or the record directory."""
    host = host or Host()
    expected = _expected_ids(root, spike, only)
    driver = resolve_driver(root, spike, host)
    budget_mib = configured_ram_budget_mib(env)
    available = host.available_ram_mib()
    preflight(
        root,
        spike,
        mode,
        vm_ram_mib=driver.vm_ram_mib,
        ram_budget_mib=budget_mib,
        available_ram_mib=lambda: available,
        kvm_path=host.kvm_path,
    )
    start = host.clock()
    run_id = f"{spike}-{start.astimezone(datetime.UTC):%Y%m%dT%H%M%SZ}"
    base, state = _state_dir(root, env, run_id)
    checks = {
        "state_dir": check_state_dir(base, mode, host),
        "vm_ram": {
            "budget_mib": budget_mib,
            "planned_mib": driver.vm_ram_mib,
            "available_mib": available,
        },
    }

    out = state / "out" if mode == "record" else root / SCRATCH_DIR / spike / run_id
    (state / "run").mkdir(parents=True)
    out.mkdir(parents=True)
    driver_env = {k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    driver_env["XDG_RUNTIME_DIR"] = str(state / "run")
    if only is not None:
        driver_env["SPIKE_ONLY"] = only
    argv = [str(driver.path / "bin/nixos-test-driver"), "--output_directory", str(out)]
    status = host.launch(
        [*argv, "--junit-xml", "junit.xml"],
        out,
        driver_env,
        out / "driver.log",
        driver.budget_s + GRACE_S,
    )
    end = host.clock()
    shutil.rmtree(state / "run")  # multi-GiB machine state, never evidence

    result = _read_result(out / RESULT_FILE)
    if status is None:
        reason = f"budget of {driver.budget_s} s exhausted"
    elif status != 0:
        reason = f"driver exited with status {status}"
    else:
        reason = "the test script did not report it"
    scenarios = _complete(result.get("scenarios", []), expected, reason)
    time_budget = {"budget_s": driver.budget_s, "finished_within_budget": status is not None}
    evidence_env = {"preflight": checks, "time_budget": time_budget}

    if mode == "shakedown":
        (out / "environment.json").write_text(json.dumps(evidence_env, indent=2) + "\n")
        outcome = {"scenarios": scenarios, "time_budget": time_budget}
        (out / "outcome.json").write_text(json.dumps(outcome, indent=2) + "\n")
        return out

    environment = build_environment(root, driver, result.get("tool_versions", {}), host)
    (out / "environment.json").write_text(
        json.dumps(evidence_env | {"environment": environment}, indent=2) + "\n"
    )
    criteria_path = _newest_criteria_file(root, spike)
    lock_entry = next(e for e in read_lock(root) if e.file == criteria_path.name)
    sanitiser = Sanitiser(
        user_names=[getpass.getuser()],
        host_names=[socket.gethostname()],
        host_roots=[str(root), str(base.resolve()), str(Path.home())],
    )
    passing = {s["id"] for s in scenarios if s["outcome"] == "pass"}
    archive = build_evidence(out, state / "evidence", sanitiser, passing)
    record = build_record(
        spike=spike,
        criteria=load_criteria(criteria_path),
        lock_entry=lock_entry,
        scenarios=scenarios,
        environment=environment,
        start=start,
        end=end,
        evidence_sha256=archive.sha256,
        required_settings=result.get("required_settings"),
        coverage=result.get("coverage"),
    )
    return write_record(root, record, archive.path, "record")
