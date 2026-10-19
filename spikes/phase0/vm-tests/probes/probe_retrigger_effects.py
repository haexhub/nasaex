# Probe of the side effects of the pool retrigger unit of T051f (task T051g). It measures, it
# does not judge, and it never writes a record. retrigger_effects.nix puts the prelude
# (PROBE_MODE, ABSENT, retrigger_prelude.py), boot_probe_setup.py (first boot with blank disks,
# `spike-mkfs all`, the helpers run/show/settle and DEVICE_UNITS/MOUNT_UNITS) and
# retrigger_boot.py (settings, helpers, the per-boot measurement `observe`) before this part.
# One more boot with every pool member writes the reader file /mnt/cache/probe-t051g/reader.bin
# (1 MiB, the content retrigger_observe.py expects), then by PROBE_MODE:
#   normal    PROBE_BOOTS (default 30) reboots, nothing done by hand; with the unit then
#             the repetitions of PROBE_IDEMPOTENCE (default hand,hand,hand,restart,restart) while
#             a file is written through /mnt/cache: `hand` runs its commands by hand, `restart`
#             runs `systemctl restart` of it, `control` runs nothing (the writer alone)
#   degraded  PROBE_BOOTS (default 5) reboots without the member ABSENT; after each one the
#             member is hot-plugged through the QEMU monitor and the pool watched for 30 s
#   sweep     per delay step of PROBE_SWEEP (default 1:2,2:3,3:4,4:5,6:7,31:32: not-ready and
#             ready member delays in s, written to /var/lib/probe-t051g-delay before the boot)
#             PROBE_SWEEP_BOOTS (default 2) reboots
# No new boot starts when the longest boot so far would end past PROBE_DEADLINE_S (default
# 1260 s). Result: probe-retrigger-effects.json (rewritten after every boot), a PROBE-RESULT
# line; retrigger_effects.results.md holds the measured outcome.
# --- one boot with every pool member: the reader file ------------------------------------------
RESULT["prep_timings"] = reboot()
run("systemctl is-active mnt-pool.mount mnt-cache.mount")
run(
    f"mkdir -p {DATA} && python3 -c \"import sys; open(sys.argv[1], 'wb')"
    f'.write(bytes(range(256)) * 4096)" {DATA}/reader.bin && sync'
)
run("test -f /mnt/pool/probe-t051g/reader.bin")  # created on the pool branch (create ff)
if ABSENT:
    os.environ["QEMU_OPTS"] = QEMU_OPTS_BEFORE  # the measured boots run without the member

# --- the measured boots ------------------------------------------------------------------------
if PROBE_MODE == "sweep":
    plan = [step for step in SWEEP for _ in range(SWEEP_BOOTS)]
else:
    plan = [None] * BOOTS
longest = 0.0
for index, step in enumerate(plan):
    if time.monotonic() - STARTED + longest > DEADLINE_S:
        RESULT["stopped"] = f"deadline before boot {index}"
        break
    t0 = time.monotonic()
    if step is not None:
        run(f"echo '{step[0]} {step[1]}' > {DELAY_FILE} && sync")
    boot = observe(index, reboot())
    boot["delay_step"] = step
    if PROBE_MODE == "degraded":
        boot["hotplug"] = hotplug()
        print(f"PROBE hotplug {index}: " + json.dumps(boot["hotplug"]["states"], sort_keys=True))
    RESULT["boots"].append(boot)
    save_effects()
    report(boot)
    longest = max(longest, time.monotonic() - t0)

# --- idempotence (normal mode with the unit) ---------------------------------------------------
if PROBE_MODE == "normal" and RESULT["boots"] and RESULT["boots"][-1]["unit"]["Result"] != "absent":
    for rep, how in enumerate(IDEMPOTENCE):
        entry = idempotence(rep, how)
        RESULT["idempotence"].append(entry)
        save_effects()
        print(
            f"PROBE idempotence {rep} {how}: status={entry['status']} "
            f"changed={entry['changed_units']} "
            f"mountinfo_changed={entry['mountinfo_changed']} uevents={entry['uevents']} "
            f"writer_errors={entry['writer']['errors']} slow={entry['writer']['slow']} "
            f"equal={entry['writer']['read_back_equal']}"
        )

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
save_effects()
print("PROBE-RESULT " + json.dumps(RESULT["effects_summary"], sort_keys=True))
