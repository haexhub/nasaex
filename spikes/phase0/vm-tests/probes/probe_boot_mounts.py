# Probe of the boot race of S1.11 (task T051a). It measures, it does not judge, and it never
# writes a record. The server boots once with blank disks, `spike-mkfs all` formats them, and
# then it is shut down and started PROBE_BOOTS times (default 20), doing nothing by hand. Per
# boot: the host load and pressure right before the start, the time to multi-user.target and to
# an empty job queue, the systemd state and monotonic activation time of every by-label device
# unit and every mount unit, the udev properties of every disk (initialisation time,
# SYSTEMD_READY, ID_BTRFS_READY), where each by-label link points, and the journal lines that
# name devices, btrfs, timeouts or mounts plus the lines around the first by-label activation.
# Guest times are CLOCK_MONOTONIC seconds since the kernel started. Result:
# probe-boot-mounts.json in the working directory of the driver (rewritten after every boot)
# and a PROBE-RESULT line in the log; boot_mounts.results.md holds the measured outcome.
import json
import os
import re
import shlex
import time
from pathlib import Path

from probes.boot_timing import (
    boot_up,
    first_time,
    journal_lines,
    monotonic_s,
    parse_properties,
    summarize,
    window,
)

BOOTS = int(os.environ.get("PROBE_BOOTS", "20"))
OUTPUT = Path("probe-boot-mounts.json")
DISKS = [d for d in TOPOLOGY["disks"] if d["role"] in ("pool", "archive", "parity")]
ARCHIVE = [d["serial"] for d in DISKS if d["role"] == "archive"]
PARITY = next(d["serial"] for d in DISKS if d["role"] == "parity")
LABELS = ["spikepool", *ARCHIVE, PARITY]
MOUNT_POINTS = [
    "/mnt/pool",
    "/mnt/parity",
    *(f"/mnt/archive/{s}" for s in ARCHIVE),
    *(f"/mnt/archive-content/{s}" for s in ARCHIVE),
    "/mnt/base",
    "/mnt/cache",
]
UNIT_PROPS = (
    "ActiveState,SubState,Result,ActiveEnterTimestampMonotonic,"
    "InactiveExitTimestampMonotonic,JobRunningTimeoutUSec"
)
MANAGER_PROPS = (
    "InitRDTimestampMonotonic,UserspaceTimestampMonotonic,FinishTimestampMonotonic,"
    "DefaultDeviceTimeoutUSec"
)
UDEV_KEYS = ("DEVNAME", "USEC_INITIALIZED", "ID_FS_LABEL", "SYSTEMD_READY", "ID_BTRFS_READY")
JOURNAL_PATTERN = re.compile(
    r"by-label|by\\x2dlabel|BTRFS|[Tt]imed out|Dependency failed|mnt-|virtio_blk|\bvd[a-z]\b"
)
RESULT: dict = {"environment": {}, "commands": [], "boots": []}


def run(command: str, timeout: int = 60) -> str:
    """Record a distinct server command and return its output, requiring success."""
    if command not in RESULT["commands"]:
        RESULT["commands"].append(command)
    return server.succeed(command, timeout=timeout)


def unit_of(path: str, suffix: str) -> str:
    """Return the systemd unit name for a path and unit suffix."""
    return run(f"systemd-escape -p --suffix={suffix} {path}").strip()


def show(unit: str, props: str = UNIT_PROPS) -> dict:
    """Return the requested systemd properties of a unit as a dictionary."""
    return parse_properties(run(f"systemctl show -p {props} {shlex.quote(unit)}"))


def with_seconds(props: dict) -> dict:
    """The properties plus their monotonic timestamps in seconds (`*_s`)."""
    extra = {
        key.removesuffix("TimestampMonotonic") + "_s": monotonic_s(value)
        for key, value in props.items()
        if key.endswith("TimestampMonotonic")
    }
    return props | extra


def host_pressure() -> dict:
    """Read the first pressure-stall summary line for each available host resource."""
    found = {}
    for resource in ("cpu", "io", "memory"):
        path = Path("/proc/pressure") / resource
        if path.exists():
            found[resource] = path.read_text().splitlines()[0]
    return found


def settle() -> None:
    """Wait up to 300 seconds for the server systemd job queue to become empty."""
    server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=300)


def collect(index: int, host_load, pressure, timings: dict) -> dict:
    """Collect one boot record with host metrics, device and mount states, and logs."""
    manager = with_seconds(parse_properties(run(f"systemctl show -p {MANAGER_PROPS}")))
    lines = journal_lines(run("journalctl -b -o short-monotonic --no-pager", timeout=120))
    stage2 = manager.get("Userspace_s") or 0.0
    devices = {}
    for label, unit in DEVICE_UNITS.items():
        props = with_seconds(show(unit))
        status, link = server.execute(f"readlink -f /dev/disk/by-label/{label}")
        devices[label] = props | {
            "active_s": props.get("ActiveEnter_s") if props["ActiveState"] == "active" else None,
            "link": link.strip() if status == 0 else None,
            # journal: device job started, device found, device job timed out (stage 2)
            "expecting_s": first_time(lines, f"Expecting device /dev/disk/by-label/{label}."),
            "found_s": first_time(lines, f"Found device /dev/disk/by-label/{label}.", stage2),
            "timed_out_s": first_time(lines, f"{unit}/start timed out", stage2),
        }
    members = {}
    for disk in DISKS:
        udev = parse_properties(
            run(f"udevadm info --query=property --name=/dev/disk/by-id/virtio-{disk['serial']}")
        )
        members[disk["serial"]] = {k: udev.get(k) for k in UDEV_KEYS} | {
            "initialized_s": monotonic_s(udev.get("USEC_INITIALIZED"))
        }
    mounts = {unit: with_seconds(show(unit)) for unit in MOUNT_UNITS.values()}
    first = min((d["active_s"] for d in devices.values() if d["active_s"]), default=None)
    timeout = min((d["timed_out_s"] for d in devices.values() if d["timed_out_s"]), default=None)
    analyze = server.execute("systemd-analyze 2>&1")[1].strip()
    return {
        "index": index,
        "host_load": list(host_load),
        "host_pressure": pressure,
        **timings,
        "manager": manager,
        "stage2": {
            "udevd_started_s": first_time(lines, "Started Rule-based Manager for Device", stage2),
            "coldplug_finished_s": first_time(lines, "Finished Coldplug All udev Devices", stage2),
        },
        "systemd_analyze": analyze,
        "devices": devices,
        "members": members,
        "mounts": mounts,
        "smbd": server.execute("systemctl is-active samba-smbd.service")[1].strip(),
        "up": None,  # set below, after the mounts are known
        "first_label_active_s": first,
        "journal_matched": [f"[{t:.6f}] {s}" for t, s in lines if JOURNAL_PATTERN.search(s)][:200],
        "journal_around_first_label": [
            f"[{t:.6f}] {s}" for t, s in window(lines, first, before=0.5, after=0.5, limit=100)
        ],
        "journal_around_first_timeout": [
            f"[{t:.6f}] {s}" for t, s in window(lines, timeout, before=0.5, after=0.5, limit=100)
        ],
    }


def save() -> None:
    """Refresh the boot summary and write all collected results to OUTPUT as JSON."""
    RESULT["summary"] = summarize(RESULT["boots"])
    OUTPUT.write_text(json.dumps(RESULT, indent=2, sort_keys=True))


# --- first boot (blank disks), format, environment -------------------------------------------
STARTED = time.monotonic()
start_all()
server.wait_for_unit("multi-user.target")
settle()
for key, command in {
    "kernel": "uname -r",
    "systemd": "systemctl --version | head -1",
    "btrfs_progs": "btrfs --version",
    "mergerfs": "mergerfs -V 2>&1 | head -1",
}.items():
    RESULT["environment"][key] = server.succeed(command).strip()
DEVICE_UNITS = {label: unit_of(f"/dev/disk/by-label/{label}", "device") for label in LABELS}
MOUNT_UNITS = {mp: unit_of(mp, "mount") for mp in MOUNT_POINTS}
RESULT["environment"]["vm_resources"] = TOPOLOGY["vmResources"]
RESULT["environment"]["host_cpus"] = os.cpu_count()
run("spike-mkfs all")
run("sync")
RESULT["fstab_mnt"] = [line for line in run("cat /etc/fstab").splitlines() if " /mnt/" in line]
RESULT["device_timeout_option"] = any("x-systemd.device-timeout" in x for x in RESULT["fstab_mnt"])
RESULT["device_units"] = DEVICE_UNITS
RESULT["mount_units"] = MOUNT_UNITS

# --- the reboots ------------------------------------------------------------------------------
for index in range(BOOTS):
    t0 = time.monotonic()
    server.shutdown()
    load, pressure = os.getloadavg(), host_pressure()
    t1 = time.monotonic()
    server.start()
    server.wait_for_unit("multi-user.target")
    t2 = time.monotonic()
    settle()
    t3 = time.monotonic()
    timings = {
        "shutdown_s": round(t1 - t0, 2),
        "start_to_multi_user_s": round(t2 - t1, 2),
        "start_to_no_jobs_s": round(t3 - t1, 2),
    }
    boot = collect(index, load, pressure, timings)
    boot["up"] = boot_up(boot)
    RESULT["boots"].append(boot)
    save()
    labels = {k: v["active_s"] for k, v in boot["devices"].items()}
    print(f"PROBE boot {index}: up={boot['up']} load={load[0]:.2f} labels={labels} {timings}")

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
save()
print("PROBE-RESULT " + json.dumps(RESULT["summary"], sort_keys=True))
