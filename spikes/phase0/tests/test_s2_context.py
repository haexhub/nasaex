"""The shared ground of the S2 checks (vm-tests/s2/context.py, task T067): the recovery sequence
step by step with its flags, the boot at `boot:after-failure`, the history of a criteria table,
the judgement of an observation against a prediction table, and the evidence files. Against a
scripted stand-in for the `server`; what the tools do is measured in the VM (probe-s2-baseline)."""

import hashlib
import json
from pathlib import Path

import pytest
import test_s2_history as base
from s2.context import Lab, Recovery, prediction_mismatches
from s2.history import History

qemu_img = base.qemu_img

TABLES = {
    "base_set": [
        {"disk": "arch0", "path": "a/a0.bin", "size": 100},
        {"disk": "arch1", "path": "b/b0.bin", "size": 200},
    ],
    "histories": {
        "later": [
            {"action": "add", "path": "new/w0.bin", "size": 50, "lands_on": "arch1"},
            {"action": "modify", "path": "b/b0.bin", "offsets": [3, 9]},
            {"action": "delete", "path": "a/a0.bin"},
        ]
    },
}


class Machine:
    """Answers `execute` from `script` (command prefix -> (rc, output)), records everything."""

    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.booted = True
        self.commands: list[str] = []
        self.events: list[str] = []
        self.script: dict[str, tuple[int, str]] = {}

    def execute(self, command, **_):
        self.commands.append(command)
        for prefix, answer in self.script.items():
            if command.startswith(prefix):
                return answer
        return 0, ""

    def succeed(self, command, **_):
        rc, out = self.execute(command)
        if rc != 0:
            raise AssertionError(f"command failed: {command}")
        return out

    def shutdown(self):
        self.events.append("shutdown")
        self.booted = False

    def start(self):
        self.events.append("start")
        self.booted = True

    def wait_for_unit(self, unit, **_):
        self.events.append(f"unit {unit}")

    def wait_until_succeeds(self, command, **_):
        self.events.append("settle")


class StubHistory:
    """What the Lab asks of the history for the table-driven parts."""

    def __init__(self, machine, lands="arch1"):
        self.machine = machine
        self.lands = lands
        self.calls: list[tuple] = []

    def populate(self, files):
        self.calls.append(("populate", list(files)))

    def add(self, rel, size, not_on=None):
        self.calls.append(("add", rel, size))
        return self.lands

    def modify(self, rel, offsets):
        self.calls.append(("modify", rel, list(offsets)))

    def delete(self, rel):
        self.calls.append(("delete", rel))


class Recorder:
    def __init__(self):
        self.attached: list[tuple[str, str]] = []

    def attach(self, path, sha256):
        self.attached.append((path, sha256))


@pytest.fixture
def machine(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return Machine(state)


@pytest.fixture
def lab(machine):
    return Lab(StubHistory(machine), Recorder(), TABLES)


# --- the recovery sequence -----------------------------------------------------------------------


def test_the_steps_run_in_the_order_given_each_with_its_flags(machine, lab):
    recovery = Recovery(lab, "arch0")

    recovery.run("recover", "verify", "resync", "unlock")

    assert machine.commands == [
        "recover recover arch0 2>&1",
        "recover verify arch0 2>&1",
        "recover resync arch0 2>&1",
        "recover unlock arch0 2>&1",
    ]
    assert list(recovery.results) == ["recover", "verify", "resync", "unlock"]
    assert recovery.failures() == []


def test_plain_goes_to_recover_and_resync_only(machine, lab):
    Recovery(lab, "arch2", plain=True).run("recover", "verify", "resync", "unlock")

    assert machine.commands == [
        "recover recover arch2 --plain 2>&1",
        "recover verify arch2 2>&1",
        "recover resync arch2 --plain 2>&1",
        "recover unlock arch2 2>&1",
    ]


def test_listed_files_are_accepted_by_the_steps_after_the_recover_that_lists_them(machine, lab):
    machine.script["recover recover"] = (3, "recover.sh: unrecoverable files of arch0\n")
    recovery = Recovery(lab, "arch0", plain=True)

    recovery.run("recover", "verify", "resync", "unlock")

    assert machine.commands[1:] == [
        "recover verify arch0 --accept-unrecoverable 2>&1",
        "recover resync arch0 --plain --accept-unrecoverable 2>&1",
        "recover unlock arch0 2>&1",
    ]
    assert recovery.failures(listed_ok=True) == []
    assert recovery.failures() == [
        "recover recover arch0 exited 3: recover.sh: unrecoverable files of arch0"
    ]


def test_the_sequence_stops_at_the_first_step_that_exits_with_another_status(machine, lab):
    machine.script["recover verify"] = (1, "x\nrecover.sh: check failed: files fail\n")
    recovery = Recovery(lab, "arch0")

    recovery.run("recover", "verify", "resync", "unlock")

    assert [c.split()[1] for c in machine.commands] == ["recover", "verify"]
    assert recovery.failures() == [
        "recover verify arch0 exited 1: recover.sh: check failed: files fail"
    ]


def test_a_later_call_goes_on_after_an_earlier_one(machine, lab):
    recovery = Recovery(lab, "arch0")
    recovery.run("recover", "verify", "resync")

    recovery.run("unlock")

    assert list(recovery.results) == ["recover", "verify", "resync", "unlock"]


def test_a_failed_sequence_runs_no_more_steps_in_a_later_call(machine, lab):
    machine.script["recover recover"] = (1, "no summary\n")
    recovery = Recovery(lab, "arch0")
    recovery.run("recover", "verify")

    recovery.run("resync", "unlock")

    assert [c.split()[1] for c in machine.commands] == ["recover"]


def test_the_summary_keeps_the_exit_status_and_the_last_lines_of_every_step(machine, lab):
    machine.script["recover recover"] = (0, "\n".join(f"line {i}" for i in range(40)) + "\n")
    recovery = Recovery(lab, "arch0")
    recovery.run("recover")

    summary = recovery.summary()

    assert summary["recover"]["rc"] == 0
    assert summary["recover"]["out"][-1] == "line 39" and len(summary["recover"]["out"]) == 20


# --- boot at boot:after-failure ------------------------------------------------------------------


def test_the_boot_takes_the_failed_disk_out_and_starts_the_machine_again(tmp_path, qemu_img):
    machine = Machine(tmp_path / "state")
    machine.state_dir.mkdir()
    history = History(machine, base.TOPOLOGY, str(qemu_img))
    lab = Lab(history, Recorder(), TABLES)

    lab.boot_after_failure("arch0")

    assert machine.events == ["shutdown", "start", "unit multi-user.target", "settle"]
    image = base.TOPOLOGY["disks"][2]["image"]  # arch0
    assert (machine.state_dir / image).exists()
    log = (tmp_path / "qemu.log").read_text().splitlines()
    assert log == [f"create -f qcow2 {machine.state_dir / image} 640M"]


# --- the history of a criteria table -------------------------------------------------------------


def test_the_base_set_is_populated_in_the_order_of_the_table(lab):
    lab.populate_base()

    assert lab.history.calls == [
        ("populate", [("arch0", "a/a0.bin", 100), ("arch1", "b/b0.bin", 200)])
    ]


def test_a_history_is_applied_action_by_action(lab):
    lab.apply("later")

    assert lab.history.calls == [
        ("add", "new/w0.bin", 50),
        ("modify", "b/b0.bin", [3, 9]),
        ("delete", "a/a0.bin"),
    ]


def test_a_file_that_lands_on_another_disk_than_the_table_says_is_a_harness_error(machine):
    lab = Lab(StubHistory(machine, lands="arch2"), Recorder(), TABLES)

    with pytest.raises(RuntimeError, match="new/w0.bin landed on arch2, the table says arch1"):
        lab.apply("later")


# --- what the checks read from the guest ---------------------------------------------------------


def test_the_read_only_locations_after_the_unlock(machine, lab):
    machine.script["python3 -c"] = (0, '["/mnt/parity/snapraid.parity"]\n')

    assert lab.readonly_locations() == ["/mnt/parity/snapraid.parity"]
    machine.script["python3 -c"] = (0, "[]\n")
    assert lab.readonly_locations() == []


def test_snapsync_complete_and_covered_report_their_exit_status(machine, lab):
    machine.script["snapsync covered"] = (1, "snapsync: not covered\n")

    assert lab.snapsync("complete").rc == 0
    covered = lab.snapsync("covered")

    assert (covered.rc, covered.out) == (1, "snapsync: not covered\n")
    assert machine.commands == ["snapsync complete 2>&1", "snapsync covered 2>&1"]


# --- evidence ------------------------------------------------------------------------------------


def test_evidence_is_written_and_attached_by_its_hash(lab, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    lab.evidence("S2.1-complete-sync", {"b": 1, "a": ("x",)})

    path = Path("status/S2.1-complete-sync.json")
    assert json.loads(path.read_text()) == {"a": ["x"], "b": 1}
    assert lab.recorder.attached == [
        ("status/S2.1-complete-sync.json", hashlib.sha256(path.read_bytes()).hexdigest())
    ]


# --- a prediction table against an observation ---------------------------------------------------


ROWS = [
    {"path": "a.bin", "version": "synced", "recoverable": True},
    {"path": "b.bin", "version": "synced", "recoverable": False},
    {"path": "b.bin", "version": "unsynced", "recoverable": False},
]


def test_an_observation_that_matches_the_table_has_no_mismatch():
    observed = {
        ("a.bin", "synced"): "bit_exact",
        ("b.bin", "synced"): "missing",
        ("b.bin", "unsynced"): "differs",
    }

    assert prediction_mismatches(ROWS, observed) == []


def test_every_kind_of_deviation_is_named():
    observed = {
        ("a.bin", "synced"): "differs",  # predicted recoverable
        ("b.bin", "synced"): "bit_exact",  # predicted lost
        ("c.bin", "unsynced"): "missing",  # not in the table
    }

    assert prediction_mismatches(ROWS, observed) == [
        "a.bin (synced): predicted recoverable, observed differs",
        "b.bin (synced): predicted unrecoverable, observed bit_exact",
        "b.bin (unsynced) was not observed",
        "c.bin (unsynced) was observed but is not in the prediction",
    ]
