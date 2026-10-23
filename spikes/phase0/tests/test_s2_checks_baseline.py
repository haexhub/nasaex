"""Checks S2.1 and S2.3 (vm-tests/s2/checks_baseline.py, task T067): which commands they send in
which order, and what they judge, against a scripted history and `server`. The predictions are
those of the real draft criteria docs/phase0/criteria/S2.yaml. What the recovery does to the
disks is measured in the VM (probe-s2-baseline)."""

import json
from pathlib import Path

import pytest
import test_s2_context as ctx
import test_s2_history as base
import yaml
from helpers import REPO_ROOT
from s2 import checks_baseline
from s2.context import Lab

qemu_img = base.qemu_img
CRITERIA = yaml.safe_load((REPO_ROOT / "docs/phase0/criteria/S2.yaml").read_text())
TABLES = CRITERIA["tables"]
ARCH = ("arch0", "arch1", "arch2")
GOOD = {"bit_exact": ["x.bin"], "differs": [], "missing": []}


class Machine(ctx.Machine):
    def execute(self, command, **kw):
        self.events.append(f"$ {command}")
        return super().execute(command, **kw)


class FakeHistory:
    def __init__(self, machine, qemu_img):
        self.machine = machine
        self.disks = {d["serial"]: d for d in base.TOPOLOGY["disks"]}
        self.qemu_img = str(qemu_img)
        self.sync_result = (0, "synced", "/root/s2-logs/sync-1.log")
        self.layout: list[str] = []
        self.replace_error: str | None = None
        self.unsynced = {d: set() for d in ARCH}
        self.observed: dict[str, dict] = {}
        self.compared = {d: GOOD for d in ARCH}

    def log(self, text):
        self.machine.events.append(f"history {text}")

    def reset(self):
        self.log("reset")

    def populate(self, files):
        self.log(f"populate {len(list(files))}")

    def add(self, rel, size, not_on=None):
        self.log(f"add {rel}")
        self.unsynced["arch1"].add(f"branch/{rel}")
        return "arch1"

    def sync(self, snapshots):
        self.log(f"sync snapshots={snapshots}")
        return self.sync_result

    def layout_differences(self):
        return self.layout

    def fail(self, serial, kind="device-ioerror"):
        self.log(f"fail {serial} {kind}")
        return f"FaultEvent({kind})"

    def replace(self, serial, relation="same"):
        self.log(f"replace {serial} {relation}")
        if self.replace_error:
            raise AssertionError(self.replace_error)
        return "/dev/disk/by-id/virtio-spare"

    def observe(self, disk):
        return self.observed[disk]

    def compare(self, disk):
        return self.compared[disk]


CRITERIA_ROWS = {k: v["files"] for k, v in TABLES["predictions"].items() if "files" in v}


def exact(rows):
    """The observation that matches a prediction: recoverable files bit-exact, others missing."""
    return {(r["path"], r["version"]): "bit_exact" if r["recoverable"] else "missing" for r in rows}


@pytest.fixture
def machine(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return Machine(state)


@pytest.fixture
def lab(machine, qemu_img, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    machine.script["python3 -c"] = (0, "[]\n")  # no read-only location
    history = FakeHistory(machine, qemu_img)
    return Lab(history, ctx.Recorder(), TABLES)


def evidence(lab, name):
    return json.loads(Path(f"status/{name}.json").read_text())


def shell(machine):
    """The commands the check sent, without the readonly probe."""
    return [e for e in machine.events if not e.startswith("$ python3")]


# --- S2.1 ----------------------------------------------------------------------------------------


@pytest.fixture
def s21(lab):
    lab.history.observed["arch0"] = exact(CRITERIA_ROWS["S2.1"])
    return lab


def test_s21_runs_the_sequence_with_the_boot_between_the_replacement_and_the_recovery(s21, machine):
    checks_baseline.check_complete_sync_recovery(s21)

    assert shell(machine) == [
        "history reset",
        "history populate 16",
        "history sync snapshots=True",
        "$ snapsync complete 2>&1",
        "history fail arch0 device-ioerror",
        "history replace arch0 same",
        "shutdown",
        "start",
        "unit multi-user.target",
        "settle",
        "$ recover recover arch0 2>&1",
        "$ recover verify arch0 2>&1",
        "$ recover resync arch0 2>&1",
        "$ snapsync complete 2>&1",
        "$ snapsync covered 2>&1",
        "$ recover unlock arch0 2>&1",
    ]


def test_s21_writes_its_evidence(s21, machine):
    checks_baseline.check_complete_sync_recovery(s21)

    seen = evidence(s21, "S2.1-complete-sync-recovery")
    assert seen["failed"] == {"disk": "arch0", "kind": "device-ioerror"}
    assert seen["mismatches"] == [] and seen["failures"] == []
    assert set(seen["steps"]) == {"recover", "verify", "resync", "unlock"}
    assert seen["covered"] == {"complete": 0, "covered": 0}
    assert seen["readonly_after_unlock"] == []
    assert s21.recorder.attached[0][0] == "status/S2.1-complete-sync-recovery.json"


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("sync", "the first sync exited 1"),
        ("complete", "snapsync complete exited 1 after the first sync"),
        ("layout", "the layout of the model differs from snapraid locate: a/a0.bin"),
    ],
)
def test_s21_precondition_that_is_not_met_is_a_harness_error_not_a_fail(
    s21, machine, change, error
):
    if change == "sync":
        s21.history.sync_result = (1, "boom", "/log")
    elif change == "complete":
        machine.script["snapsync complete"] = (1, "")
    else:
        s21.history.layout = ["a/a0.bin: model (0, 1, 1), locate (3, 4, 1)"]

    with pytest.raises(RuntimeError, match=error):
        checks_baseline.check_complete_sync_recovery(s21)
    assert not any(e.startswith("history fail") for e in machine.events)


def test_s21_a_failed_step_of_the_replacement_fails_and_leaves_evidence(s21, machine):
    s21.history.replace_error = "recover lock arch0 exited 1: a location is still writable"

    with pytest.raises(AssertionError, match="still writable"):
        checks_baseline.check_complete_sync_recovery(s21)

    seen = evidence(s21, "S2.1-complete-sync-recovery")
    assert seen["failures"] == ["recover lock arch0 exited 1: a location is still writable"]
    assert "start" not in machine.events  # no boot after a failed replacement


@pytest.mark.parametrize("step", ["recover", "verify", "resync"])
def test_s21_every_step_must_exit_0(s21, machine, step):
    machine.script[f"recover {step}"] = (1 if step != "recover" else 3, "recover.sh: broke\n")

    with pytest.raises(AssertionError, match=f"recover {step} arch0 exited"):
        checks_baseline.check_complete_sync_recovery(s21)

    names = [e.split()[2] for e in machine.events if e.startswith("$ recover ")]
    assert names[-1] == step or step == "recover"  # the run stopped there


def test_s21_a_file_that_does_not_come_back_bit_exact_fails(s21):
    s21.history.observed["arch0"][("a/a3.bin", "synced")] = "differs"

    with pytest.raises(AssertionError, match=r"a/a3.bin \(synced\): predicted recoverable"):
        checks_baseline.check_complete_sync_recovery(s21)

    assert evidence(s21, "S2.1-complete-sync-recovery")["mismatches"] == [
        "a/a3.bin (synced): predicted recoverable, observed differs"
    ]


@pytest.mark.parametrize("category", ["differs", "missing"])
def test_s21_a_surviving_disk_that_changed_fails(s21, category):
    s21.history.compared["arch2"] = {
        "bit_exact": [],
        "differs": [],
        "missing": [],
        category: ["c/c0.bin"],
    }

    with pytest.raises(AssertionError, match=f"arch2 changed: {category} c/c0.bin"):
        checks_baseline.check_complete_sync_recovery(s21)


def test_s21_an_array_that_is_not_covered_again_fails(s21, machine):
    machine.script["snapsync covered"] = (1, "snapsync: not covered\n")

    with pytest.raises(AssertionError, match="snapsync covered exited 1"):
        checks_baseline.check_complete_sync_recovery(s21)


def test_s21_a_location_that_stays_read_only_after_the_unlock_fails(s21, machine):
    machine.script["python3 -c"] = (0, '["/mnt/parity/snapraid.parity"]\n')

    with pytest.raises(AssertionError, match="read-only after the unlock: /mnt/parity"):
        checks_baseline.check_complete_sync_recovery(s21)


# --- S2.3 ----------------------------------------------------------------------------------------


@pytest.fixture
def s23(lab):
    lab.history.observed["arch1"] = exact(CRITERIA_ROWS["S2.3"])
    return lab


def test_s23_writes_the_files_after_the_sync_then_fails_the_disk_by_unplugging(s23, machine):
    checks_baseline.check_written_after_sync(s23)

    assert shell(machine)[:9] == [
        "history reset",
        "history populate 16",
        "history sync snapshots=True",
        "$ snapsync complete 2>&1",
        "history add new/w0.bin",
        "history add new/w1.bin",
        "history fail arch1 device-unplug",
        "history replace arch1 same",
        "shutdown",
    ]
    assert [e for e in shell(machine) if e.startswith("$ recover")] == [
        "$ recover recover arch1 2>&1",
        "$ recover verify arch1 2>&1",
        "$ recover resync arch1 2>&1",
        "$ recover unlock arch1 2>&1",
    ]


def test_s23_reports_the_unsynced_files_from_the_history_and_records_whether_recover_names_them(
    s23, machine
):
    machine.script["recover recover"] = (0, "recover.sh: every file of arch1 recovered\n")
    s23.history.unsynced["arch1"] = {"branch/new/w0.bin", "branch/new/w1.bin"}

    checks_baseline.check_written_after_sync(s23)
    seen = evidence(s23, "S2.3-written-after-sync")

    assert seen["unrecoverable_from_history"] == ["branch/new/w0.bin", "branch/new/w1.bin"]
    assert seen["recover_names_them"] == []
    assert seen["mismatches"] == [] and seen["failures"] == []


def test_s23_accepts_a_recover_that_names_the_files_and_records_the_names(s23, machine):
    machine.script["recover recover"] = (
        3,
        "recover.sh: unrecoverable files of arch1 (renamed *.unrecoverable by the fix):\n"
        "  branch/new/w0.bin\n",
    )

    checks_baseline.check_written_after_sync(s23)
    seen = evidence(s23, "S2.3-written-after-sync")

    assert seen["recover_names_them"] == ["new/w0.bin"]
    assert "$ recover verify arch1 --accept-unrecoverable 2>&1" in machine.events
    assert "$ recover resync arch1 --accept-unrecoverable 2>&1" in machine.events


def test_s23_a_file_written_after_the_sync_that_comes_back_fails(s23):
    s23.history.observed["arch1"][("new/w1.bin", "unsynced")] = "bit_exact"

    with pytest.raises(AssertionError, match=r"new/w1.bin \(unsynced\): predicted unrecoverable"):
        checks_baseline.check_written_after_sync(s23)


def test_s23_a_synced_file_that_is_lost_fails(s23):
    s23.history.observed["arch1"][("b/b2.bin", "synced")] = "missing"

    with pytest.raises(AssertionError, match=r"b/b2.bin \(synced\): predicted recoverable"):
        checks_baseline.check_written_after_sync(s23)


def test_s23_the_other_disks_must_be_unchanged(s23):
    s23.history.compared["arch0"] = {"bit_exact": [], "differs": ["a/a0.bin"], "missing": []}

    with pytest.raises(AssertionError, match="arch0 changed: differs a/a0.bin"):
        checks_baseline.check_written_after_sync(s23)


def test_s23_a_file_that_lands_on_another_disk_is_a_harness_error(s23):
    s23.history.add = lambda rel, size, not_on=None: "arch2"

    with pytest.raises(RuntimeError, match="landed on arch2, the table says arch1"):
        checks_baseline.check_written_after_sync(s23)


def test_the_checks_are_named_by_the_criteria():
    names = {s["id"]: s["check"] for s in CRITERIA["scenarios"]}

    assert checks_baseline.CHECKS == {
        names["S2.1"]: checks_baseline.check_complete_sync_recovery,
        names["S2.3"]: checks_baseline.check_written_after_sync,
    }
