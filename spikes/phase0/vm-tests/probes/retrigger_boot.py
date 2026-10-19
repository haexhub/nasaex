# Boot observation of the retrigger probes: the settings, helpers and per-boot measurement of the
# side-effect probe of T051g, shared with the probe of the retrigger variants of T051i. It
# measures, it does not judge, and it never writes a record. retrigger_effects.nix and
# retrigger_v2.nix put the prelude (PROBE_MODE, ABSENT, retrigger_prelude.py) and
# boot_probe_setup.py (first boot with blank disks, `spike-mkfs all`, the helpers
# run/show/settle and DEVICE_UNITS/MOUNT_UNITS) before this part and the plan of their probe
# (probe_retrigger_effects.py, probe_retrigger_v2.py) after it.
# Per boot (`observe`): host time to multi-user.target and to an empty job queue; state, result
# and activation times of every mount, the pool device unit, local-fs.target, multi-user.target,
# smbd and the retrigger unit (exit status, start and exit time, its journal); the activation
# order; per block device the kernel and udev events of the udev monitor of T051f (whole boot
# and inside the unit's run); journal line counts (Found device, Mounting, Mounted, Unmount,
# SMB start/stop, device time-outs, dependency failures) and the matching lines; the reader's
# outcome changes (errors after its first good read) and the mount table changes under /mnt;
# the race data of T051f (not-ready member, retriggers reaching it). `hotplug` attaches the
# member ABSENT through the QEMU monitor, `idempotence` repeats the unit's commands during a
# write. `save_effects` rewrites OUTPUT, which the plan may set to its own file name.
from probes.boot_timing import boot_up
from probes.pool_race import classify, parse_monitor, scan_lines, until_active_command
from probes.retrigger_effects import (
    activation_order,
    attach_commands,
    count_lines,
    count_mount_events,
    count_uevents,
    monitor_reply,
    parse_mount_log,
    parse_reader_log,
    parse_sweep,
    reader_errors_after_ok,
    summarize_effects,
)

try:
    BOOTS = int(os.environ.get("PROBE_BOOTS", "30" if PROBE_MODE == "normal" else "5"))
    SWEEP_BOOTS = int(os.environ.get("PROBE_SWEEP_BOOTS", "2"))
    DEADLINE_S = float(os.environ.get("PROBE_DEADLINE_S", "1260"))
except ValueError as exc:
    raise ValueError("PROBE_BOOTS, PROBE_SWEEP_BOOTS, PROBE_DEADLINE_S must be numeric") from exc
IDEMPOTENCE = os.environ.get("PROBE_IDEMPOTENCE", "hand,hand,hand,restart,restart").split(",")
if not set(IDEMPOTENCE) <= {"hand", "restart", "control"}:
    raise ValueError("PROBE_IDEMPOTENCE: a comma-separated list of hand, restart, control")
SWEEP = parse_sweep(os.environ.get("PROBE_SWEEP", "1:2,2:3,3:4,4:5,6:7,31:32"))

OUTPUT = Path("probe-retrigger-effects.json")
UNIT = "probe-pool-retrigger.service"
POOL_UNIT = DEVICE_UNITS["spikepool"]
POOL_SERIALS = [d["serial"] for d in DISKS if d["role"] == "pool"]
MEMBER_PATHS = " ".join(f"/dev/disk/by-id/virtio-{s}" for s in POOL_SERIALS)
UNIT_COMMANDS = f"udevadm settle --timeout=30; udevadm trigger --action=add --settle {MEMBER_PATHS}"
DATA = "/mnt/cache/probe-t051g"
DELAY_FILE = "/var/lib/probe-t051g-delay"
WATCH = [
    *MOUNT_UNITS.values(),
    POOL_UNIT,
    "local-fs.target",
    "multi-user.target",
    "samba-smbd.service",
    UNIT,
]
ORDERED = [
    POOL_UNIT,
    "mnt-pool.mount",
    "mnt-base.mount",
    "mnt-cache.mount",
    UNIT,
    "local-fs.target",
    "samba-smbd.service",
    "multi-user.target",
]
PROPS = (
    "LoadState,ActiveState,SubState,Result,ActiveEnterTimestampMonotonic,"
    "InactiveEnterTimestampMonotonic,ExecMainStatus,ExecMainStartTimestampMonotonic,"
    "ExecMainExitTimestampMonotonic,NRestarts,MainPID,InvocationID"
)
PATTERNS = {
    "found_device": r"Found device ",
    "found_pool": r"Found device .*spikepool",
    "mounting": r"Mounting /mnt/",
    "mounted": r"Mounted /mnt/",
    "unmount": r"Unmount",
    "smb_start": r"Start(ing|ed) .*SMB",
    "smb_stop": r"Stopp(ing|ed) .*SMB",
    "device_timeout": r"Timed out waiting for device",
    "dependency_failed": r"Dependency failed",
}
SELECTED = re.compile(
    r"Found device|Mounting /mnt|Mounted /mnt|Unmount|SMB|Timed out|Dependency failed|"
    r"probe-pool-retrigger|BTRFS: device|BTRFS (error|warning)|spikepool|udevadm"
)
RESULT.update(mode=PROBE_MODE, absent=ABSENT, unit_commands=UNIT_COMMANDS, idempotence=[])
RESULT["environment"]["guest_cpus"] = run("nproc").strip()
RESULT["environment"]["probe_units"] = server.execute(
    f"systemctl cat {UNIT} probe-reader.service probe-mountlog.service 2>&1"
)[1]
RESULT["environment"]["udev_rules"] = server.execute("udevadm cat 99-local.rules 2>&1")[1]


def uptime() -> float:
    return float(run("cut -d' ' -f1 /proc/uptime").strip())


def reboot() -> dict:
    t0 = time.monotonic()
    server.shutdown()
    t1 = time.monotonic()
    server.start()
    server.wait_for_unit("multi-user.target")
    t2 = time.monotonic()
    settle()
    t3 = time.monotonic()
    return {
        "shutdown_s": round(t1 - t0, 2),
        "start_to_multi_user_s": round(t2 - t1, 2),
        "start_to_no_jobs_s": round(t3 - t1, 2),
    }


def journal() -> list:
    return journal_lines(run("journalctl -b -o short-monotonic --no-pager", timeout=120))


def logs(since: float | None = None) -> dict:
    """The monitor events, the reader transitions and the mount events (at or after `since`)."""
    events = parse_monitor(run("cat /run/probe-udev-monitor.log"))
    reader = parse_reader_log(run("cat /run/probe-reader.log"))
    mounts = parse_mount_log(run("cat /run/probe-mounts.log"))
    late = (lambda t: True) if since is None else (lambda t: t >= since)
    return {
        "events": [e for e in events if late(e["t"])],
        "reader": [r for r in reader["transitions"] if late(r["t"])],
        "heartbeats": len(reader["heartbeats"]),
        "reader_all": reader["transitions"],
        "mounts": [m for m in mounts if late(m["t"])],
    }


def members() -> dict:
    found = {}
    for serial in POOL_SERIALS:
        status, out = server.execute(f"readlink -e /dev/disk/by-id/virtio-{serial}")
        found[serial] = out.strip().removeprefix("/dev/") if status == 0 else None
    return found


def observe(index: int, timings: dict) -> dict:
    units = {u: with_seconds(show(u, PROPS)) for u in WATCH}
    active_s = {
        u: p.get("ActiveEnter_s") if p["ActiveState"] == "active" else None
        for u, p in units.items()
    }
    unit = units[UNIT]
    if unit["LoadState"] == "not-found":
        unit = {"Result": "absent", "ExecMainStatus": "-"}
    lines = journal()
    seen = logs()
    present = members()
    sysnames = [s for s in present.values() if s]
    link = server.execute("readlink -f /dev/disk/by-label/spikepool")[1].strip()
    race = classify(seen["events"], sysnames, scan_lines(lines, "spikepool"), link, None)
    pool = units[POOL_UNIT]
    start, end = unit.get("ExecMainStart_s"), unit.get("ExecMainExit_s")
    ran = isinstance(start, float) and isinstance(end, float)
    label = "/dev/disk/by-label/spikepool"
    boot = {
        "index": index,
        "timings": timings,
        "manager_finish_s": with_seconds(
            parse_properties(run("systemctl show -p FinishTimestampMonotonic"))
        ).get("Finish_s"),
        "units": units,
        "active_s": active_s,
        "order": activation_order({u: active_s[u] for u in ORDERED}),
        "unit": unit,
        "unit_journal": run(f"journalctl -b -u {UNIT} -o short-monotonic --no-pager"),
        "mounts": {u: units[u] for u in MOUNT_UNITS.values()},
        "smbd": units["samba-smbd.service"],
        "pool_device_inactive_after_active": bool(
            pool.get("ActiveEnter_s")
            and pool.get("InactiveEnter_s")
            and pool["InactiveEnter_s"] > pool["ActiveEnter_s"]
        ),
        "pool_job": {
            "expecting_s": first_time(lines, f"Expecting device {label}"),
            "found_s": first_time(lines, f"Found device {label}"),
            "timed_out_s": first_time(lines, f"Timed out waiting for device {label}"),
        },
        "journal_counts": count_lines(lines, PATTERNS),
        "journal_selected": [f"[{t:.6f}] {s}" for t, s in lines if SELECTED.search(s)][:150],
        "members": present,
        "pool_link": link,
        "uevents": count_uevents(seen["events"]),
        "uevents_in_unit_window": count_uevents(seen["events"], start, end) if ran else None,
        "member_events": [
            {"source": e["source"], "t": e["t"], "action": e["action"], "dev": e["sysname"]}
            | {
                k: e["props"][k]
                for k in ("SEQNUM", "ID_BTRFS_READY", "SYSTEMD_READY")
                if k in e["props"]
            }
            for e in seen["events"]
            if e["sysname"] in sysnames
        ],
        "race": {
            k: race[k]
            for k in (
                "not_ready_member",
                "retrigger_changes",
                "stuck",
                "scan_gap_ms",
                "link_target",
            )
        },
        "reader_transitions": seen["reader_all"],
        "reader_heartbeats": seen["heartbeats"],
        "reader_errors_after_ok": reader_errors_after_ok(seen["reader_all"]),
        "mount_log": seen["mounts"],
        "mount_events": count_mount_events(seen["mounts"]),
        "systemd_analyze": server.execute("systemd-analyze 2>&1")[1].strip(),
    }
    boot["up"] = boot_up(boot | {"smbd": units["samba-smbd.service"]["ActiveState"]})
    return boot


def save_effects() -> None:
    RESULT["effects_summary"] = summarize_effects(RESULT["boots"])
    OUTPUT.write_text(json.dumps(RESULT, indent=2, sort_keys=True))


def report(boot: dict) -> None:
    print(
        f"PROBE boot {boot['index']}: up={boot['up']} unit={boot['unit'].get('Result')}/"
        f"{boot['unit'].get('ExecMainStatus')} pool_job={boot['pool_job']} "
        f"race={boot['race']} reader_errors={len(boot['reader_errors_after_ok'])} "
        f"mount_events={boot['mount_events']} {boot['timings']}"
    )


def hotplug() -> dict:
    """Attach the absent member through the QEMU monitor and watch the pool for 30 s."""
    assert ABSENT and ABSENT_IMAGE, "hotplug needs the absent member and its image"
    t0 = uptime()
    commands = attach_commands(ABSENT, ABSENT_IMAGE)
    answers = [[c, monitor_reply(server.send_monitor_command(c))] for c in commands]
    member_unit = unit_of(f"/dev/disk/by-id/virtio-{ABSENT}", "device")
    status, out = server.execute(until_active_command(member_unit, 30), timeout=60)
    unit_at = server.execute(until_active_command(POOL_UNIT, 30), timeout=60)
    mount_at = server.execute(until_active_command("mnt-pool.mount", 15), timeout=45)
    settle()
    seen = logs(since=t0)
    lines = [(t, s) for t, s in journal() if t >= t0]
    return {
        "started_s": t0,
        "monitor_answers": answers,
        "member_device_active_s": float(out.strip()) if status == 0 else None,
        "pool_unit_active_s": float(unit_at[1].strip()) if unit_at[0] == 0 else None,
        "mnt_pool_active_by_itself_s": float(mount_at[1].strip()) if mount_at[0] == 0 else None,
        "states": {
            u: show(u, "ActiveState,SubState,Result")
            for u in [POOL_UNIT, "mnt-pool.mount", "mnt-base.mount", "mnt-cache.mount"]
        },
        "members": members(),
        "uevents": count_uevents(seen["events"]),
        "journal_counts": count_lines(lines, PATTERNS),
        "journal_selected": [f"[{t:.6f}] {s}" for t, s in lines if SELECTED.search(s)][:80],
        "reader_transitions": seen["reader"],
        "mount_log": seen["mounts"],
    }


def snapshot() -> dict:
    keys = "ActiveEnterTimestampMonotonic,InvocationID,MainPID,NRestarts,ActiveState"
    return {
        "units": {
            u: show(u, keys) for u in [*MOUNT_UNITS.values(), POOL_UNIT, "samba-smbd.service"]
        },
        "mountinfo": run("grep ' /mnt/' /proc/self/mountinfo | cut -d' ' -f1,5,9,10").splitlines(),
    }


def idempotence(rep: int, how: str) -> dict:
    """The unit's commands again while a file is written through /mnt/cache (20 s)."""
    command = {"hand": UNIT_COMMANDS, "restart": f"systemctl restart {UNIT}", "control": "true"}[
        how
    ]
    writer = f"{DATA}/writer-{rep}.bin"
    out = f"/run/probe-writer-{rep}.json"
    before = snapshot()
    tool = "/run/current-system/sw/bin/probe-t051g-observe"
    run(f"systemd-run --unit=probe-writer-{rep} --collect {tool} write {writer} 20 {out}")
    server.sleep(5)  # the writer is running; the commands start in the middle of its 20 s
    t0 = uptime()
    status, output = server.execute(f"set -e; {command} 2>&1", timeout=180)
    t1 = uptime()
    server.wait_for_file(out, timeout=120)
    server.sleep(2)  # late udev and mount events, if any, reach the logs
    after = snapshot()
    seen = logs(since=t0)
    lines = [(t, s) for t, s in journal() if t >= t0]
    result = {
        "rep": rep,
        "how": how,
        "command": command,
        "status": status,
        "output": output,
        "started_s": t0,
        "duration_s": round(t1 - t0, 3),
        "writer": json.loads(run(f"cat {out}")),
        "changed_units": {
            u: {k: (before["units"][u][k], v) for k, v in p.items() if before["units"][u][k] != v}
            for u, p in after["units"].items()
            if p != before["units"][u]
        },
        "mountinfo_changed": before["mountinfo"] != after["mountinfo"],
        "mountinfo": after["mountinfo"],
        "uevents": count_uevents(seen["events"]),
        "journal_counts": count_lines(lines, PATTERNS),
        "journal_selected": [f"[{t:.6f}] {s}" for t, s in lines if SELECTED.search(s)][:80],
        "reader_transitions": seen["reader"],
        "mount_log": seen["mounts"],
        "unit_after": show(UNIT, PROPS),
    }
    run(f"rm -f {writer}")
    return result
