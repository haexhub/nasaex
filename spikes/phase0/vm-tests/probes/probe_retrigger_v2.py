# Probe of the retrigger variants and of the shutdown difference of T051g (task T051i). It
# measures, it does not judge, and it never writes a record. retrigger_v2.nix puts the prelude
# (PROBE_MODE, ABSENT, retrigger_prelude.py), boot_probe_setup.py and retrigger_boot.py (the
# settings and the per-boot measurement `observe` of T051g) before this part. One more boot with
# every pool member writes the reader file of T051g, then by PROBE_MODE (as T051g):
#   normal    PROBE_BOOTS (default 30) cycles
#   degraded  PROBE_BOOTS (default 5) cycles without the member ABSENT, each followed by the
#             hot-plug of T051g
#   sweep     PROBE_SWEEP_BOOTS cycles per delay step of PROBE_SWEEP
# One cycle: when /mnt/cache is a mergerfs mount, two files of FILE_BYTES random bytes are
# written through it (one fsynced, one not) and their SHA-256 kept; the shutdown is timed and
# its console lines kept (systemd-shutdown logs only there); the next boot is measured with
# `observe` plus `systemctl is-system-running` and the failed units; the shutdown is classified
# from its console lines, the previous boot's journal and the capture that
# probe-shutdown-capture.service took after both mergerfs mounts stopped
# (/var/lib/probe-t051i/capture.jsonl, shutdown_observe.py); then, when /mnt/cache is up again,
# the data checks: every file written before a shutdown and not yet checked is read through
# /mnt/cache and on /mnt/pool and compared, `btrfs scrub start -B` and `btrfs device stats
# --check` of the pool, btrfs lines of the kernel log, the mount types. A failed data check
# stops the run with the files left in place. Result: probe-retrigger-v2.json (rewritten after
# every cycle), a PROBE-RESULT line; retrigger_v2.results.md holds the measured outcome.
from probes.retrigger_shutdown import (
    capture_summary,
    classify_shutdown,
    judge_data,
    summarize_shutdowns,
)

OUTPUT = Path("probe-retrigger-v2.json")
TOOL = "/run/current-system/sw/bin/probe-t051i-observe"
FILES = "probe-t051i"
FILE_BYTES = 1 << 20
CAPTURE_LOG = "/var/lib/probe-t051i/capture.jsonl"
SHUTDOWN_LINE = re.compile(
    r"remains running|Waiting for process|Sending SIG|mount\.fuse\.merg|mergerfs|PROBE-T051I|"
    r"Unmount|umount|[Ff]ailed|systemd-shutdown|Stopp(ing|ed) .*(/mnt|SMB|Probe)|BTRFS"
)
PENDING: dict[str, str] = {}  # file name -> SHA-256 written, not yet checked after a boot
RESULT["commands"] += [
    f"{TOOL} put /mnt/cache/{FILES}/c<n>-fsync.bin {FILE_BYTES} 1 (and -nofsync ... 0)",
    f"sha256sum /mnt/cache/{FILES}/<file> /mnt/pool/{FILES}/<file>",
]
RESULT["unit_script"] = server.execute(
    f"systemctl cat {UNIT} | sed -n 's/^ExecStart=-\\?//p' | xargs -r cat 2>&1"
)[1]
RESULT["environment"]["capture_unit"] = server.execute(
    "systemctl cat probe-shutdown-capture.service 2>&1"
)[1]


def boot_id() -> str:
    return run("cat /proc/sys/kernel/random/boot_id").strip()


def fstype(point: str) -> str | None:
    status, out = server.execute(f"findmnt -n -o FSTYPE --mountpoint {point}")
    return out.strip() if status == 0 else None


def write_files(index: int) -> None:
    run(f"mkdir -p /mnt/cache/{FILES}")
    for fsync, kind in ((1, "fsync"), (0, "nofsync")):
        name = f"c{index}-{kind}.bin"
        out = server.succeed(f"{TOOL} put /mnt/cache/{FILES}/{name} {FILE_BYTES} {fsync}")
        PENDING[name] = out.strip()


def cycle_reboot() -> tuple[dict, list[str]]:
    """Shutdown (timed, its console lines kept), start, multi-user.target, empty job queue."""
    t0 = time.monotonic()
    server.shutdown()
    t1 = time.monotonic()
    thread = getattr(server, "serial_thread", None)
    if thread is not None:
        thread.join(timeout=10)  # the reader of the console ends at QEMU's end of output
    console = [line for line in server.full_console_log if SHUTDOWN_LINE.search(line)]
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
    return timings, console


def shutdown_evidence(console: list[str], previous_boot: str, shutdown_s: float) -> dict:
    status, out = server.execute("journalctl -b -1 -o short-monotonic --no-pager", timeout=120)
    lines = [line for line in out.splitlines() if SHUTDOWN_LINE.search(line)] if status == 0 else []
    captures = []
    for line in server.execute(f"cat {CAPTURE_LOG}")[1].splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("boot_id") == previous_boot:
            captures.append(entry)
    run(f"rm -f {CAPTURE_LOG}")
    result = classify_shutdown(console, lines, shutdown_s)
    result["console_lines"] = console[-120:]
    result["journal_lines"] = lines[-200:]
    result["captures"] = captures
    result["capture_summaries"] = [capture_summary(c) for c in captures]
    return result


def check_data() -> dict:
    files = {}
    for name, expected in sorted(PENDING.items()):
        seen: dict[str, str | None] = {"expected": expected}
        for where in ("cache", "pool"):
            status, out = server.execute(f"sha256sum /mnt/{where}/{FILES}/{name}")
            seen[where] = out.split()[0] if status == 0 and out.strip() else None
        files[name] = seen
    scrub = server.execute("btrfs scrub start -B /mnt/pool 2>&1", timeout=600)
    stats = server.execute("btrfs device stats --check /mnt/pool 2>&1")
    dmesg = run("dmesg").splitlines()
    mounts = {p: fstype(p) for p in ("/mnt/pool", "/mnt/base", "/mnt/cache")}
    result = judge_data(files, scrub, stats, dmesg, mounts)
    result |= {
        "checked": files,
        "scrub": list(scrub),
        "stats": list(stats),
        "dmesg_btrfs": [line for line in dmesg if "BTRFS" in line][-40:],
        "mounts": mounts,
        "units": {
            u: server.execute(f"systemctl is-active {u}")[1].strip()
            for u in ("mnt-pool.mount", "mnt-base.mount", "mnt-cache.mount")
        },
    }
    if result["ok"]:
        names = " ".join(f"/mnt/cache/{FILES}/{n}" for n in PENDING)
        run(f"rm -f {names}")
        PENDING.clear()
    return result


def save_all() -> None:
    RESULT["shutdown_summary"] = summarize_shutdowns(
        {"index": b["index"], "shutdown": b["shutdown"], "data": b["data"]} for b in RESULT["boots"]
    )
    save_effects()


# --- one boot with every pool member: the reader file of T051g ---------------------------------
RESULT["prep_timings"], _ = cycle_reboot()
run("systemctl is-active mnt-pool.mount mnt-cache.mount")
run(
    f"mkdir -p {DATA} && python3 -c \"import sys; open(sys.argv[1], 'wb')"
    f'.write(bytes(range(256)) * 4096)" {DATA}/reader.bin && sync'
)
run("test -f /mnt/pool/probe-t051g/reader.bin")  # created on the pool branch (create ff)
if ABSENT:
    os.environ["QEMU_OPTS"] = QEMU_OPTS_BEFORE  # the measured boots run without the member
previous = boot_id()

# --- the measured cycles -------------------------------------------------------------------------
plan = [s for s in SWEEP for _ in range(SWEEP_BOOTS)] if PROBE_MODE == "sweep" else [None] * BOOTS
longest = 0.0
for index, step in enumerate(plan):
    if time.monotonic() - STARTED + longest > DEADLINE_S:
        RESULT["stopped"] = f"deadline before cycle {index}"
        break
    t0 = time.monotonic()
    if step is not None:
        run(f"echo '{step[0]} {step[1]}' > {DELAY_FILE} && sync")
    if fstype("/mnt/cache") == "fuse.mergerfs":
        write_files(index)
    timings, console = cycle_reboot()
    boot = observe(index, timings)
    boot["delay_step"] = step
    boot["system_state"] = server.execute("systemctl is-system-running")[1].strip()
    failed = server.execute("systemctl list-units --state=failed --plain --no-legend")[1]
    boot["failed_units"] = failed.splitlines()
    boot["shutdown"] = shutdown_evidence(console, previous, timings["shutdown_s"])
    previous = boot_id()
    boot["data"] = check_data() if PENDING and fstype("/mnt/cache") == "fuse.mergerfs" else None
    if PROBE_MODE == "degraded":
        boot["hotplug"] = hotplug()
    RESULT["boots"].append(boot)
    save_all()
    report(boot)
    shutdown = boot["shutdown"]
    print(
        f"PROBE cycle {index}: state={boot['system_state']} outlived={shutdown['outlived_units']} "
        f"kill_wait={shutdown['kill_wait']} shutdown_s={timings['shutdown_s']} "
        f"captures={shutdown['capture_summaries']} "
        f"data={boot['data'] and {k: boot['data'][k] for k in ('ok', 'failed', 'files')}}"
    )
    if boot["data"] is not None and not boot["data"]["ok"]:
        RESULT["stopped"] = f"data check failed after cycle {index}; files left in place"
        break
    longest = max(longest, time.monotonic() - t0)

RESULT["pending_unchecked"] = dict(PENDING)
RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
save_all()
print("PROBE-RESULT " + json.dumps(RESULT["shutdown_summary"], sort_keys=True))
print("PROBE-EFFECTS " + json.dumps(RESULT["effects_summary"], sort_keys=True))
