# The S1 test script (task T049), run by the test driver outside the nix sandbox through
# `spikectl run S1` (spikectl/run.py) with the driver's output directory as working directory.
# mkSpike prepends TOPOLOGY, QEMU_IMG and CRITERIA (docs/phase0/criteria/S1.yaml, parsed); the
# driver provides start_all, server and client. SPIKE_ONLY narrows the run to one scenario; the
# setup (formatting, mounting, the S1 data set, the SMB user, the client mounts) always runs.
# Every scenario of the criteria is recorded through testlib.recorder, the mount-variants cases
# with the gating flag of their variant, and spike-result.json is rewritten after each scenario
# (testlib.spike_result), with the tool versions and the required settings the checks found
# (FR-013). Scaffolding for the spikes, not product code.
import os
import shlex
import subprocess
from pathlib import Path

from s1.context import BASE_UNIT, CACHE_UNIT, Lab
from s1.fixture import FIXTURE, POOL_BRANCH, roots
from s1.scenarios import plan, run_plan

from testlib.recorder import Recorder
from testlib.spike_result import RESULT_FILE, version, write_spike_result

PLANNED = plan(CRITERIA, os.environ.get("SPIKE_ONLY") or None)
SHARE = CRITERIA["tables"]["share"]
recorder = Recorder(criteria=CRITERIA)
lab = Lab(
    server,
    client,
    roots(TOPOLOGY),
    recorder,
    user=SHARE["user"],
    share_name=SHARE["name"],
    tables=CRITERIA["tables"],
)

# --- setup: disks, pools, share, data set, client mounts --------------------------------------
start_all()
server.wait_for_unit("multi-user.target")
server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
server.succeed("spike-mkfs all")
for mount in [POOL_BRANCH, *(a["data"] for a in TOPOLOGY["archiveLayout"])]:
    server.succeed(f"mount {mount}")
server.succeed(f"systemctl start {BASE_UNIT} {CACHE_UNIT}")
server.wait_for_unit("samba.target")
server.succeed(f"printf '%s\\n%s\\n' {lab.password} {lab.password} | smbpasswd -s -a {lab.user}")
lab.place(FIXTURE)
client.wait_for_unit("multi-user.target")
lab.mount_clients()

qemu = subprocess.run([QEMU_IMG, "--version"], capture_output=True, text=True, check=True)
TOOL_VERSIONS = {
    "kernel": version(server.succeed("uname -r")),
    "qemu": version(qemu.stdout),
    "mergerfs": version(server.succeed("mergerfs -V 2>&1")),
    "samba": version(server.succeed("smbd --version")),
    "btrfs_progs": version(server.succeed("btrfs --version")),
    "client_kernel": version(client.succeed("uname -r")),
    "mount_cifs": version(client.succeed("mount.cifs -V")),
    "smbclient": version(client.succeed("smbclient --version")),
}
lab.required_settings["share"] = {
    key: server.succeed(
        f"testparm -s --section-name={SHARE['name']} --parameter-name={shlex.quote(key)}"
        " 2>/dev/null"
    ).strip()
    for key in SHARE["required_keys"]
}


def save() -> None:
    write_spike_result(
        Path(RESULT_FILE), recorder.scenarios(), TOOL_VERSIONS, lab.required_settings
    )


# --- the scenarios ----------------------------------------------------------------------------
save()
run_plan(recorder, lab, PLANNED, on_done=save)
