# The S2.1 and S2.3 probe (task T067): the two checks of vm-tests/s2/checks_baseline.py run through
# the recorder on the S2 node of vm-tests/s2/default.nix (the criteria file is CRITERIA), against
# the real kernel, mergerfs, SnapRAID 14.4, snapper, snapraid-btrfs, the archive lock and
# guest/recover.sh. It measures what the checks conclude and why, it never writes a record.
# Scaffolding for the Phase 0 spikes, not product code.
#
# S2.1: a complete sync, arch0 fails (device-ioerror), the sequence with the boot at
#       boot:after-failure
# S2.3: two files written after the sync, arch1 fails (device-unplug), the sequence
# control: S2.1 again with the corrupted-parity control (controls/corrupted-parity.sh) run after
#       the sync and before the failure; S2.1 must judge it a fail
# Result: probe-s2-baseline.json in the working directory of the driver, the evidence files of the
# checks (status/*.json) and PROBE lines.
import json
import time
from pathlib import Path

from s2.checks_baseline import CHECKS
from s2.context import Lab
from s2.history import History

from testlib.recorder import Recorder

STARTED = time.monotonic()
SCENARIOS = ("S2.1", "S2.3")
RESULT: dict = {}


class Corrupted(History):
    """A history whose disk failure is preceded by the corrupted-parity control."""

    def fail(self, serial, kind="device-ioerror"):
        self.machine.succeed("corrupted-parity")
        return super().fail(serial, kind)


def measure(label: str, scenario: dict, history: History) -> None:
    recorder = Recorder(criteria=CRITERIA)
    lab = Lab(history, recorder, CRITERIA["tables"])
    started = time.monotonic()
    recorder.run(scenario["id"], lambda: CHECKS[scenario["check"]](lab))
    result = recorder.scenarios()[-1]
    RESULT[label] = {**result, "seconds": round(time.monotonic() - started, 1)}
    print(f"PROBE {label}.result: {json.dumps(result, default=str)[:900]}")
    for evidence in result.get("evidence", []):
        body = json.loads(Path(evidence["path"]).read_text())
        for key, value in body.items():
            print(f"PROBE {label}.{key}: {json.dumps(value, default=str)[:900]}")
        RESULT[label]["evidence_body"] = body


scenarios = {s["id"]: s for s in CRITERIA["scenarios"]}
for sid in SCENARIOS:
    measure(sid, scenarios[sid], History(server, TOPOLOGY, QEMU_IMG))
measure("S2.1-control", scenarios["S2.1"], Corrupted(server, TOPOLOGY, QEMU_IMG))

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-s2-baseline.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True, default=str))
