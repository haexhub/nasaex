# Probe of the S1.11 boot failure under load (task T051d). It measures, it does not judge, and
# it never writes a record. boot_probe_setup.py (prepended by boot_mounts.nix) boots the server
# with blank disks, formats them and defines the per-boot measurement (`collect`). This part
# mounts the pools as the S1 test script does, places the S1 data set (s1.fixture, with its file
# above 4 GiB on arch1), and then runs PROBE_BOOTS cycles (default 10): the guest redoes the IO
# of S1.8 (baseline case) and the S1.11 manifest read locally on the cache pool, nothing synced
# (probes.boot_load.heavy_io_steps), then the server is shut down and started at once. With
# PROBE_HOST_IO=1 (variant D2) a host `dd ... conv=fdatasync` of 8 GiB into the probe's state
# directory runs in a loop under nice/ionice -c3 from 2 s before the start until the job queue
# is empty; it stops, and the probe stops after that boot, when the host MemAvailable falls
# below PROBE_MIN_AVAILABLE_MIB (default 6144). Per boot, besides `collect`: the workload steps
# and their times, guest Dirty/Writeback before the shutdown, host load, pressure and memory
# before the start, host IO pressure and memory sampled every 0.5 s from the start to the
# empty job queue, the last change time of every by-label link, and for a boot that did not
# come up: when the missing links appear (up to 120 s), the full udev data of the disks and
# links, the systemd-udevd journal, and a recovery by starting the mount units (the probe stops
# if that fails). No new cycle starts when the longest cycle so far would end past
# PROBE_DEADLINE_S (default 3300 s, budget probe-long is 3600 s). Result: probe-boot-load.json
# in the working directory of the driver (rewritten after every boot) and a PROBE-RESULT line.
import shutil

from probes.boot_load import (
    HostLoad,
    HostSampler,
    heavy_io_steps,
    read_meminfo,
    summarize_load,
)
from probes.boot_timing import boot_up
from s1.context import Lab
from s1.fixture import FIXTURE, NEW_LARGE, POOL_BRANCH, roots

BOOTS = int(os.environ.get("PROBE_BOOTS", "10"))
HOST_IO = os.environ.get("PROBE_HOST_IO") == "1"
FLOOR_MIB = int(os.environ.get("PROBE_MIN_AVAILABLE_MIB", "6144"))
DEADLINE_S = float(os.environ.get("PROBE_DEADLINE_S", "3300"))
OUTPUT = Path("probe-boot-load.json")
LOAD_FILE = Path.cwd().parent / "host-io-load.bin"  # in the probe's state directory
DD = ["nice", "-n", "19", "ionice", "-c3", "dd", "if=/dev/zero", f"of={LOAD_FILE}"]
DD += ["bs=1M", "count=8192", "conv=fdatasync"]
ARCHIVED = next(e for e in FIXTURE.entries if e.path == "large/archived.bin")
BRANCHES = roots(TOPOLOGY)
MEM_KEYS = ("MemAvailable", "Dirty", "Writeback")
VM_KEYS = ("dirty_bytes", "dirty_background_bytes", "dirty_ratio", "dirty_expire_centisecs")
# Last change time of each by-label link as guest CLOCK_MONOTONIC seconds (udev rewrites a
# link on every change event, so this is the last write, not the first).
_LINK_TIMES = """import json, os, sys, time
off = time.clock_gettime(time.CLOCK_REALTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)
out = {}
for label in sys.argv[1].split(","):
    try:
        out[label] = round(os.lstat("/dev/disk/by-label/" + label).st_ctime - off, 6)
    except FileNotFoundError:
        out[label] = None
print(json.dumps(out))"""
# Polls for the by-label links up to argv[2] seconds: monotonic time each was first seen.
_WAIT_LINKS = """import json, os, sys, time
labels, end = sys.argv[1].split(","), time.monotonic() + float(sys.argv[2])
seen = {}
while True:
    for label in labels:
        if label not in seen and os.path.lexists("/dev/disk/by-label/" + label):
            seen[label] = round(time.monotonic(), 3)
    if len(seen) == len(labels) or time.monotonic() > end:
        break
    time.sleep(0.1)
print(json.dumps({"checked_from_s": round(end - float(sys.argv[2]), 3), "seen_s": seen}))"""


def guest_python(code: str, *args: str, timeout: int = 60) -> dict:
    """Run Python in the guest with quoted arguments and decode its JSON output."""
    quoted = " ".join(shlex.quote(a) for a in args)
    return json.loads(run(f"python3 -c {shlex.quote(code)} {quoted}", timeout=timeout))


def host_memory() -> dict:
    """Return host MemAvailable, Dirty and Writeback in KiB, or None when absent."""
    info = read_meminfo()
    return {key: info.get(key) for key in MEM_KEYS}


def heavy_io(index: int) -> list[dict]:
    """Run the guest IO steps with this cycle's flip offset and record each outcome and time."""
    steps = heavy_io_steps(
        "/mnt/cache", BRANCHES[ARCHIVED.branch], ARCHIVED.path, NEW_LARGE.size, 2**32 + 1 + index
    )
    done = []
    for name, command in steps:
        if command not in RESULT["workload_commands"]:
            RESULT["workload_commands"].append(command)
        t = time.monotonic()
        status, out = server.execute(command, timeout=1800)
        seconds = round(time.monotonic() - t, 2)
        done.append({"step": name, "status": status, "seconds": seconds, "out": out[-300:]})
    return done


def failure_detail() -> dict:
    """Wait for missing label links and collect guest udev, systemd and IO diagnostics."""
    labels = ",".join(LABELS)
    detail: dict = {"links_seen": guest_python(_WAIT_LINKS, labels, "120", timeout=180)}
    detail["link_times_after_wait"] = guest_python(_LINK_TIMES, labels)
    for command in (
        "ls -l /dev/disk/by-label/",
        "udevadm settle --timeout=0; echo settle_exit=$?",
        "systemctl list-jobs --no-legend",
        "journalctl -b -u systemd-udevd -o short-monotonic --no-pager",
        "cat /proc/pressure/io",
        *(f"udevadm info --query=all --name=/dev/disk/by-label/{label}" for label in LABELS),
        *(f"udevadm info --query=all --name=/dev/disk/by-id/virtio-{d['serial']}" for d in DISKS),
    ):
        detail[command] = server.execute(f"{command} 2>&1", timeout=60)[1]
    return detail


def recover() -> dict:
    """Attempt to start the guest mounts and SMB service, returning status and output tail."""
    units = " ".join(MOUNT_UNITS.values())
    status, out = server.execute(f"systemctl start {units} samba-smbd.service 2>&1", timeout=300)
    return {"status": status, "out": out[-2000:]}


def save_load() -> None:
    """Update the load-aware boot summary and rewrite the probe's JSON output file."""
    RESULT["summary"] = summarize_load(RESULT["boots"])
    OUTPUT.write_text(json.dumps(RESULT, indent=2, sort_keys=True))


# --- the S1 setup: pools mounted as the S1 test script does, the S1 data set --------------------
for mount in [POOL_BRANCH, *(a["data"] for a in TOPOLOGY["archiveLayout"])]:
    run(f"mount {mount}")
run("systemctl start mnt-base.mount mnt-cache.mount")
Lab(server, None, BRANCHES, None).place(FIXTURE)
RESULT["variant"] = {
    "host_io": HOST_IO,
    "dd": DD if HOST_IO else None,
    "min_available_mib": FLOOR_MIB,
    "boots_requested": BOOTS,
    "deadline_s": DEADLINE_S,
}
RESULT["workload_commands"] = []
RESULT["environment"]["host"] = {
    "kernel": os.uname().release,
    "tools": {tool: shutil.which(tool) for tool in ("nice", "ionice", "dd")},
    "vm": {key: Path(f"/proc/sys/vm/{key}").read_text().strip() for key in VM_KEYS},
}
RESULT["setup_seconds"] = round(time.monotonic() - STARTED, 1)

# --- the cycles ---------------------------------------------------------------------------------
cycles: list[float] = []
for index in range(BOOTS):
    if cycles and time.monotonic() - STARTED + max(cycles) > DEADLINE_S:
        RESULT["stopped"] = f"deadline: {index} cycles fit into {DEADLINE_S} s"
        break
    c0 = time.monotonic()
    workload = heavy_io(index)
    guest_before = run("grep -E '^(Dirty|Writeback):' /proc/meminfo").split("\n")
    t0 = time.monotonic()
    server.shutdown()
    load, pressure, memory = os.getloadavg(), host_pressure(), host_memory()
    loader = HostLoad(DD, LOAD_FILE, floor_mib=FLOOR_MIB) if HOST_IO else None
    sampler = HostSampler()
    t1 = time.monotonic()
    loader_started = False
    sampler_started = False
    t2 = None
    try:
        if loader:
            loader.start()
            loader_started = True
            time.sleep(2)
        sampler.start()
        sampler_started = True
        server.start()
        server.wait_for_unit("multi-user.target")
        t2 = time.monotonic()
        settle()
        t3 = time.monotonic()
    finally:
        try:
            host_io = loader.stop() if loader_started else None
        finally:
            host_window = (
                sampler.stop(until_multi_user_s=t2 - t1 if t2 is not None else None)
                if sampler_started
                else None
            )
    timings = {
        "shutdown_s": round(t1 - t0, 2),
        "start_to_multi_user_s": round(t2 - t1, 2),
        "start_to_no_jobs_s": round(t3 - t1, 2),
    }
    boot = collect(index, load, pressure, timings)
    boot["up"] = boot_up(boot)
    boot |= {
        "workload": workload,
        "workload_s": round(sum(step["seconds"] for step in workload), 1),
        "guest_before_shutdown": [line for line in guest_before if line],
        "host_memory_before_start_kib": memory,
        "host_window": host_window,
        "host_io_load": host_io,
        "link_times": guest_python(_LINK_TIMES, ",".join(LABELS)),
    }
    if not boot["up"]:
        boot["failure"] = failure_detail()
        boot["recovery"] = recover()
    RESULT["boots"].append(boot)
    cycles.append(time.monotonic() - c0)
    boot["cycle_s"] = round(cycles[-1], 1)
    save_load()
    labels = {k: v["active_s"] for k, v in boot["devices"].items()}
    print(
        f"PROBE boot {index}: up={boot['up']} load={load[0]:.2f}"
        f" io_some={host_window['io_some_share']} avg10max={host_window['io_some_avg10_max']}"
        f" workload_s={boot['workload_s']} labels={labels} {timings}"
    )
    if host_io and host_io["aborted"]:
        RESULT["stopped"] = f"host load aborted: {host_io['aborted']}"
        break
    if not boot["up"] and boot["recovery"]["status"] != 0:
        RESULT["stopped"] = "the mounts did not come back after a failed boot"
        break

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
save_load()
print("PROBE-RESULT " + json.dumps(RESULT["summary"], sort_keys=True))
