# Probe of the two-device pool race of S1.11 (task T051f). It measures, it does not judge, and
# it never writes a record. boot_probe_setup.py (prepended by boot_mounts.nix through
# pool_race.nix) boots the server with blank disks, formats them and defines the per-boot
# measurement of T051a (`collect`); this part shuts the server down and starts it PROBE_BOOTS
# times (default 100), doing nothing by hand. Per boot, besides `collect`: the udev monitor log
# of the pool members (every kernel and udev event with ID_BTRFS_READY, SYSTEMD_READY, SEQNUM,
# SYNTH_UUID), the btrfs scan lines with their udev worker PIDs, which member the pool link
# (fstab source of /mnt/pool) points to and when it last changed, each member's udev state at
# that time (from the monitor log), the link's udev stack directory, the pool device unit, and
# udevd's worker limit (StatusText) and task count. For a boot that did not come up or whose
# not-ready member never became ready: the read-only capture of s1.boot_diag, then whether the
# pool device unit appears within PROBE_LATE_S seconds (default 60). PROBE_STOP_AT_FAILURE=1
# (the default) stops after the first such boot. No new boot starts when the longest boot so
# far would end past PROBE_DEADLINE_S (default 3300 s; budget probe-long is 3600 s).
# PROBE_RECOVER (after the late wait of an incomplete boot, unset by default) re-evaluates the
# stuck member by hand and measures what comes back: `change` runs `udevadm trigger
# --action=change --settle` on it, `scan` runs `btrfs device scan` on it (an ioctl, no uevent);
# then the time until the pool device unit and mnt-pool are active by themselves (up to 30 s
# and 10 s), and whether `systemctl start` of the three pool mounts then succeeds. Result:
# probe-pool-race.json (rewritten after every boot), boot-<n>-diagnostics.json, a PROBE-RESULT
# line; pool_race.results.md holds the measured outcome.
from probes.boot_timing import boot_up
from probes.pool_race import (
    STATE_KEYS,
    classify,
    parse_monitor,
    scan_lines,
    state_at,
    summarize_race,
    until_active_command,
)
from s1 import boot_diag

RECOVER_COMMANDS = {
    "change": "udevadm trigger --action=change --settle /dev/{dev}",
    "scan": "btrfs device scan /dev/{dev} && udevadm settle",
}
try:
    BOOTS = int(os.environ.get("PROBE_BOOTS", "100"))
    LATE_S = int(os.environ.get("PROBE_LATE_S", "60"))
    DEADLINE_S = float(os.environ.get("PROBE_DEADLINE_S", "3300"))
except ValueError as exc:
    raise ValueError("PROBE_BOOTS, PROBE_LATE_S and PROBE_DEADLINE_S must be numeric") from exc
if BOOTS < 1:
    raise ValueError("PROBE_BOOTS must be at least 1")
if LATE_S < 0:
    raise ValueError("PROBE_LATE_S must not be negative")
if DEADLINE_S <= 0:
    raise ValueError("PROBE_DEADLINE_S must be positive")

STOP_VALUE = os.environ.get("PROBE_STOP_AT_FAILURE", "1")
if STOP_VALUE not in {"0", "1"}:
    raise ValueError("PROBE_STOP_AT_FAILURE must be 0 or 1")
STOP_AT_FAILURE = STOP_VALUE == "1"

RECOVER = os.environ.get("PROBE_RECOVER", "")
if RECOVER and RECOVER not in RECOVER_COMMANDS:
    raise ValueError(f"PROBE_RECOVER must be one of: {', '.join(RECOVER_COMMANDS)}")
POOL_MOUNTS = "mnt-pool.mount mnt-base.mount mnt-cache.mount"
OUTPUT = Path("probe-pool-race.json")
POOL_SERIALS = [d["serial"] for d in DISKS if d["role"] == "pool"]
MONITOR_LOG = "/run/probe-udev-monitor.log"
EVENT_KEYS = ("SEQNUM", "SYNTH_UUID", *STATE_KEYS)
# Where a link points and its last change time as guest CLOCK_MONOTONIC seconds.
_LINK = """import json, os, sys, time
off = time.clock_gettime(time.CLOCK_REALTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)
try:
    st = os.lstat(sys.argv[1])
    print(json.dumps({"target": os.path.basename(os.path.realpath(sys.argv[1])),
                      "ctime_s": round(st.st_ctime - off, 6)}))
except FileNotFoundError:
    print(json.dumps({"target": None, "ctime_s": None}))"""

POOL_SOURCE = next(
    line.split()[0] for line in RESULT["fstab_mnt"] if line.split()[1] == "/mnt/pool"
)
if POOL_SOURCE.startswith("/dev/disk/by-uuid/"):  # variant delay-both-uuid: set that fsid
    members = " ".join(f"/dev/disk/by-id/virtio-{s}" for s in POOL_SERIALS)
    RESULT["btrfstune"] = run(
        f"btrfstune -f -U {POOL_SOURCE.rsplit('/', 1)[1]} /dev/disk/by-id/virtio-{POOL_SERIALS[0]}"
        f" 2>&1; udevadm settle; blkid -s UUID {members}",
        timeout=300,
    )
POOL_UNIT = unit_of(POOL_SOURCE, "device")
RESULT["pool_source"] = POOL_SOURCE
RESULT["pool_unit"] = POOL_UNIT
RESULT["environment"]["guest_cpus"] = run("nproc").strip()
# The rule files udevd uses (udevadm cat prints each with its path), recorded, never required.
RESULT["environment"]["udev_rules"] = server.execute(
    "udevadm cat 64-btrfs.rules 99-local.rules 2>&1"
)[1]
RESULT["environment"]["probe_units"] = server.execute(
    "systemctl cat probe-udev-monitor.service probe-pool-retrigger.service 2>&1"
)[1]


def compact(event: dict) -> dict:
    keep = {k: event["props"][k] for k in EVENT_KEYS if k in event["props"]}
    return {"source": event["source"], "t": event["t"], "action": event["action"]} | keep


def race_data() -> dict:
    members = {
        s: run(f"readlink -f /dev/disk/by-id/virtio-{s}").strip().removeprefix("/dev/")
        for s in POOL_SERIALS
    }
    events = parse_monitor(run(f"cat {MONITOR_LOG}"))
    lines = journal_lines(run("journalctl -b -o short-monotonic --no-pager", timeout=120))
    link = json.loads(run(f"python3 -c {shlex.quote(_LINK)} {POOL_SOURCE}"))
    race = classify(
        events,
        list(members.values()),
        scan_lines(lines, "spikepool"),
        link["target"],
        link["ctime_s"],
    )
    stack = POOL_SOURCE.rsplit("/", 1)[1]
    unit = with_seconds(show(POOL_UNIT))
    active = unit.get("ActiveEnter_s") if unit["ActiveState"] == "active" else None
    return race | {
        # Each member's state as of its last processed event when the pool device unit became
        # active (PID 1 makes it active only when the device the link points to is ready).
        "state_at_unit_active": (
            {m: state_at(events, m, active) for m in members.values()} if active else None
        ),
        "members": members,
        "monitor_first_event_s": events[0]["t"] if events else None,
        "member_events": {
            dev: [compact(e) for e in events if e["sysname"] == dev] for dev in members.values()
        },
        "link_stack": server.execute(
            f'for d in /run/udev/links/*{stack}*; do echo "$d"; ls -l "$d"; done 2>&1'
        )[1],
        "pool_device_unit": unit,
        "udevd": parse_properties(
            run("systemctl show -p StatusText,TasksCurrent systemd-udevd.service")
        ),
        "pool_journal": [
            f"[{t:.6f}] {s}"
            for t, s in lines
            if re.search(r"spikepool|by-uuid|by\\x2duuid|mnt-pool|Dependency failed|retrigger", s)
        ][:100],
    }


def wait_late(seconds: int) -> float | None:
    """Monotonic guest time at which the pool device unit became active, polling `seconds`."""
    status, out = server.execute(
        until_active_command(POOL_UNIT, seconds, 0.5), timeout=seconds + 30
    )
    return float(out.strip()) if status == 0 else None


def uptime() -> float:
    return float(run("cut -d' ' -f1 /proc/uptime").strip())


def poll_active(unit: str, seconds: int) -> float | None:
    """Guest uptime at which `unit` was first seen active, polling `seconds` (None: not seen)."""
    status, out = server.execute(until_active_command(unit, seconds), timeout=seconds + 30)
    return float(out.strip()) if status == 0 else None


def recover(stuck: str) -> dict:
    """Re-evaluate the stuck member by hand (PROBE_RECOVER) and measure what comes back."""
    command = RECOVER_COMMANDS[RECOVER].format(dev=stuck)
    t0 = uptime()
    status, out = server.execute(f"{command} 2>&1", timeout=120)
    unit_at = poll_active(POOL_UNIT, 30)
    mount_at = poll_active("mnt-pool.mount", 10)
    data: dict = {
        "command": command,
        "status": status,
        "output": out,
        "started_s": t0,
        "pool_unit_active_after_s": round(unit_at - t0, 3) if unit_at else None,
        "mnt_pool_active_by_itself_after_s": round(mount_at - t0, 3) if mount_at else None,
        "member_udev": server.execute(
            f"udevadm info --query=property --name=/dev/{stuck} | grep -E '_READY='"
        )[1],
        "link": server.execute(f"readlink -f {POOL_SOURCE}")[1].strip(),
    }
    if mount_at is None:
        status, out = server.execute(f"systemctl start {POOL_MOUNTS} 2>&1", timeout=120)
        data["systemctl_start"] = {"status": status, "output": out}
    data["mounts_after"] = server.execute(f"systemctl is-active {POOL_MOUNTS}")[1].split()
    return data


RESULT["race_summary"] = summarize_race([])

# --- the reboots ------------------------------------------------------------------------------
longest = 0.0
for index in range(BOOTS):
    if time.monotonic() - STARTED + longest > DEADLINE_S:
        RESULT["stopped"] = f"deadline before boot {index}"
        break
    t0 = time.monotonic()
    server.shutdown()
    load, pressure = os.getloadavg(), host_pressure()
    host_at_start = boot_diag.host_state()
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
    boot["race"] = race_data()
    incomplete = not boot["up"] or boot["race"]["stuck"]
    if incomplete:
        units = {unit: m["ActiveState"] == "active" for unit, m in boot["mounts"].items()}
        diagnostics = boot_diag.capture_if_incomplete(server, units, host_at_start=host_at_start)
        # None: every mount of /etc/fstab is mounted (a stuck member does not imply that).
        name = f"boot-{index}-diagnostics.json" if diagnostics else None
        if name:
            Path(name).write_text(json.dumps(diagnostics, indent=2, sort_keys=True))
        boot["diagnostics_file"] = name
        boot["pool_unit_active_late_s"] = wait_late(LATE_S)
        boot["late_wait_s"] = LATE_S
        if RECOVER and boot["race"]["stuck"]:
            boot["recover"] = recover(boot["race"]["not_ready_member"])
            print(f"PROBE recover {index}: " + json.dumps(boot["recover"], sort_keys=True))
    RESULT["boots"].append(boot)
    RESULT["race_summary"] = summarize_race(RESULT["boots"])
    save()
    longest = max(longest, time.monotonic() - t0)
    race = boot["race"]
    print(
        f"PROBE boot {index}: up={boot['up']} stuck={race['stuck']} "
        f"not_ready={race['not_ready_member']} changes={race['retrigger_changes']} "
        f"gap_ms={race['scan_gap_ms']} link={race['link_target']} {timings}"
    )
    if incomplete and STOP_AT_FAILURE:
        RESULT["stopped"] = f"first failure at boot {index}"
        break

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
save()
print("PROBE-RESULT " + json.dumps(RESULT["race_summary"], sort_keys=True))
