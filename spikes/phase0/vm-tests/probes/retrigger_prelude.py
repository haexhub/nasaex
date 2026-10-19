# Prelude of the retrigger side-effect probe (task T051g), put before boot_probe_setup.py by
# retrigger_effects.nix, after the lines that set PROBE_MODE and ABSENT. When the variant's VM
# definition leaves the pool member ABSENT out, this creates a blank image for it in the
# machine's state directory and attaches it through QEMU_OPTS (read by the start script of the
# VM), so the first boot can format the pool; probe_retrigger_effects.py removes it from
# QEMU_OPTS before the measured boots and hot-plugs the same image after each of them.
import os
import subprocess
from pathlib import Path

from probes.retrigger_effects import attach_qemu_opts

QEMU_OPTS_BEFORE = os.environ.get("QEMU_OPTS", "")
ABSENT_IMAGE = None
if ABSENT:
    _absent_disk = next(d for d in TOPOLOGY["disks"] if d["serial"] == ABSENT)
    Path(server.state_dir).mkdir(parents=True, exist_ok=True)
    ABSENT_IMAGE = str(Path(server.state_dir).resolve() / f"probe-{ABSENT}.qcow2")
    subprocess.run(
        [QEMU_IMG, "create", "-f", "qcow2", ABSENT_IMAGE, f"{_absent_disk['sizeMiB']}M"],
        check=True,
        capture_output=True,
    )
    os.environ["QEMU_OPTS"] = f"{QEMU_OPTS_BEFORE} {attach_qemu_opts(ABSENT, ABSENT_IMAGE)}"
