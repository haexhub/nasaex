# Probe of the boot race of S1.11 (task T051a). It measures, it does not judge, and it never
# writes a record. boot_probe_setup.py (prepended by boot_mounts.nix) boots the server with
# blank disks, formats them and defines the per-boot measurement; this part shuts the server
# down and starts it PROBE_BOOTS times (default 20), doing nothing by hand. Per boot: the host
# load and pressure right before the start plus everything `collect` measures. Result:
# probe-boot-mounts.json in the working directory of the driver (rewritten after every boot)
# and a PROBE-RESULT line in the log; boot_mounts.results.md holds the measured outcome.
from probes.boot_timing import boot_up

BOOTS = int(os.environ.get("PROBE_BOOTS", "20"))
OUTPUT = Path("probe-boot-mounts.json")

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
