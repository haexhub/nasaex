# The S2.2 and S2.9 probe (task T068): the two checks of vm-tests/s2/checks_history.py run through
# the recorder on the S2 node of vm-tests/s2/default.nix (the criteria file is CRITERIA), against
# the real kernel, mergerfs, SnapRAID 14.4, snapper, snapraid-btrfs, the archive lock and
# guest/recover.sh. One lab for both, so S2.9 records what the workflow recovered that S2.2 did
# not. It measures what the checks conclude and why, it never writes a record.
# Scaffolding for the Phase 0 spikes, not product code.
#
# S2.2: plain sync, the history changes_after_sync, arch0 fails (device-ioerror), the plain sequence
# S2.9: the same with the snapshot workflow
# Result: probe-s2-history-checks.json in the working directory of the driver, the evidence files
# of the checks (status/*.json) and PROBE lines.
import json
import time
from pathlib import Path

from s2.checks_history import CHECKS
from s2.context import Lab
from s2.history import History

from testlib.recorder import Recorder

STARTED = time.monotonic()
SCENARIOS = ("S2.2", "S2.9")
RESULT: dict = {}
recorder = Recorder(criteria=CRITERIA)
lab = Lab(History(server, TOPOLOGY, QEMU_IMG), recorder, CRITERIA["tables"])
scenarios = {s["id"]: s for s in CRITERIA["scenarios"]}

for sid in SCENARIOS:
    started = time.monotonic()
    recorder.run(sid, lambda s=scenarios[sid]: CHECKS[s["check"]](lab))
    result = recorder.scenarios()[-1]
    RESULT[sid] = {**result, "seconds": round(time.monotonic() - started, 1)}
    print(f"PROBE {sid}.result: {json.dumps(result, default=str)[:900]}")
    for evidence in result.get("evidence", []):
        body = json.loads(Path(evidence["path"]).read_text())
        for key, value in body.items():
            print(f"PROBE {sid}.{key}: {json.dumps(value, default=str)[:900]}")
        RESULT[sid]["evidence_body"] = body

RESULT["workflow_value"] = lab.required_settings.get("workflow_value")
print(f"PROBE workflow_value: {json.dumps(RESULT['workflow_value'])}")
RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-s2-history-checks.json").write_text(
    json.dumps(RESULT, indent=2, sort_keys=True, default=str)
)
