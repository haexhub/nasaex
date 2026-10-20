# Phases snap and headroom of the SnapRAID probe (task T057); snapraid.nix places this file
# between snapraid_probe_setup.py and probe_snapraid.py, whose main flow calls them. It measures,
# it never writes a record. Scaffolding for the Phase 0 spikes, not product code.
#
# snap: snapraid-btrfs (pinned in nix/snapraid-btrfs.nix) with one NixOS snapper config per
#   archive data subvolume. What it needs at run time (config discovery through `snapper
#   get-config` in the shell, in a UTF-8 locale and in a systemd service environment); the alloc
#   history with snapshot-supported syncs; the evidence of a completed sync (userdata, the config
#   generated for the synced snapshots, `diff` against it before and after live changes, the
#   files in the synced snapshots); the S2.2 changes; arch2 fails and is replaced (first without,
#   then with `.snapshots`); `snapraid-btrfs fix -d arch2`; resync and cleanup; a snapshot-supported
#   sync killed at a position, arch2 fails again (snapperd keeps running), fix, resume, resume
#   with a fresh snapperd, a sync; `-u sync diff` and the userdata.
# headroom: arch2 alone (512 MiB) under its own config: half the free space written, synced with
#   a snapshot, all but two of those files deleted, then new data written until ENOSPC with the
#   snapshot retained; a snapper snapshot and a snapshot-supported sync at ENOSPC; the sync again
#   after removing the partial file; cleanup.
from probes.enospc_writes import SOURCE_SCRIPT, WRITER

SNAP_BW = os.environ.get("PROBE_SNAP_BW_LIMIT", "10M")


def lists() -> dict:
    return {s: snapper_list(s) for s in ARCH}


def synced_number(serial: str) -> int | None:
    numbers = [n for n, v in snapper_list(serial) if v == "synced"]
    return numbers[-1] if numbers else None


def runtime(ph: str) -> str:
    """How snapraid-btrfs finds its snapper configs in three environments; returns the prefix the
    phase uses. snapraid-btrfs reads `snapper get-config` with the ASCII separator '|'
    (snapraid-btrfs:689-690); snapper picks a box-drawing table style in a UTF-8 locale (snapper
    client/utils/Table.cc:409-411); NixOS services get LANG and LOCALE_ARCHIVE
    (nixos/modules/config/i18n.nix:201-218)."""
    note(ph, "snapper_config_arch0", run("cat /etc/snapper/configs/arch0").splitlines())
    note(ph, "snapper_sysconfig", run("cat /etc/sysconfig/snapper").strip())
    utf8 = (
        "env LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8"
        " LOCALE_ARCHIVE=/run/current-system/sw/lib/locale/locale-archive"
    )
    service = "systemd-run --wait --pipe --quiet --collect"
    variants = {
        "driver_shell": "",
        "utf8_locale": f"{utf8} ",
        "systemd_service": f"{service} ",
        "lc_all_c": "env LC_ALL=C ",
    }
    note(ph, "service_environment", tail(attempt(f"{service} env | grep -E 'LANG|LC_|LOCALE'")[1]))
    found = {}
    for name, prefix in variants.items():
        get = attempt(f"{prefix}$(command -v snapper) -c arch0 get-config | grep SUBVOLUME")
        ls = attempt(f"{prefix}$(command -v snapraid-btrfs) -c {CONF} ls")
        found[name] = {
            "get_config_subvolume_line": get[1].strip(),
            "ls_rc": ls[0],
            "ls_tail": tail(ls[1], 3),
        }
    note(ph, "config_discovery", found)
    prefix = "" if found["driver_shell"]["ls_rc"] == 0 else "env LC_ALL=C "
    note(ph, "prefix_used", prefix)
    return prefix


def phase_snap() -> None:
    ph = "snap"
    fresh_array(snapshots=True)
    write_conf()
    bt = f"{runtime(ph)}snapraid-btrfs -c {CONF} --noninteractive"
    sizes = {f"{d}:{r}": s for d, r, _, s in ALLOC_INITIAL + ALLOC_CHANGED}
    phys = fill(ALLOC_INITIAL)
    sync1 = snapraid("snap-sync1", "sync", tool=bt)
    note(ph, "sync1", {**brief(sync1, 24), "snapper": lists()})
    hyp1 = hypothesis({}, list(phys), sizes, physical_order(phys))
    remove("arch0", "branch/a/z0.bin")
    remove("arch1", "branch/d1.bin")
    phys2 = fill(ALLOC_CHANGED)
    sync2 = snapraid("snap-sync2", "sync", tool=bt)
    note(ph, "sync2", {**brief(sync2, 6), "snapper": lists()})
    kept = {k: v for k, v in hyp1.items() if k in EXPECTED and k not in phys2}
    hyp2 = hypothesis(kept, list(phys2), sizes, physical_order(phys2))
    note(ph, "locate2_vs_first_fit", against_locate(hyp2, locate()))
    as_of_sync2 = dict(EXPECTED)

    # Evidence of a completed snapshot-supported sync.
    rc, conf_text = attempt(f"{bt} -u sync config > /root/synced.conf; cat /root/synced.conf")
    note(
        ph,
        "synced_snapshot_config",
        {
            "rc": rc,
            "data_lines": [line for line in conf_text.splitlines() if line.startswith("data ")],
        },
    )
    note(
        ph,
        "diff_against_synced_snapshots",
        brief(snapraid("snap-cov1", "diff", conf="/root/synced.conf")),
    )
    s22 = s22_history(hyp2, sizes)
    synced_arch2 = s22.pop("synced_arch2")
    note(ph, "s22_history", s22)
    note(
        ph,
        "diff_against_synced_snapshots_after_live_changes",
        brief(snapraid("snap-cov2", "diff", conf="/root/synced.conf")),
    )
    note(ph, "diff_live_after_changes", brief(snapraid("snap-live-diff", "diff")))
    note(ph, "files_in_synced_snapshots_vs_hashes_as_of_sync2", in_snapshots(as_of_sync2))
    note(ph, "userdata_after_config_and_diff", lists())

    # arch2 fails and is replaced: first without `.snapshots`, then with it.
    note(ph, "fail_arch2", fail_disk("arch2"))
    new_disk("arch2", snapshots=False)
    note(ph, "replacement_snapper_list_before_snapshots_subvolume", snapper_list("arch2"))
    rc, out = attempt(f"{bt} fix -d arch2 -l {LOGS}/snap-fix-nosnapshots.log")
    note(ph, "fix_without_snapshots_subvolume", {"rc": rc, "out_tail": tail(out, 4)})
    add_snapshots_subvolume("arch2")
    note(ph, "replacement_snapper_list_with_snapshots_subvolume", snapper_list("arch2"))
    run("systemctl stop snapperd.service")
    note(ph, "replacement_snapper_list_after_snapperd_stop", snapper_list("arch2"))
    fix = snapraid("snap-fix", "fix -d arch2", tool=f"{bt} -v")
    report = fix_report(fix["log"])
    note(ph, "s29_fix", {**brief(fix, 30), "status": report["status"], "snapper": lists()})
    note(ph, "s29_restored_vs_hashes_as_of_sync2", compare_disk("arch2", synced_arch2))
    EXPECTED.update({k: v for k, v in as_of_sync2.items() if k.startswith("arch2:")})
    EXPECTED.pop("arch2:branch/w2.bin", None)
    resync = snapraid("snap-sync3", "sync", tool=bt)
    note(ph, "resync_after_fix", {**brief(resync, 8), "snapper": lists()})
    rc, out = attempt(f"{bt} cleanup")
    note(ph, "cleanup_after_resync", {"rc": rc, "out_tail": tail(out, 6), "snapper": lists()})
    snap_kill(ph, bt)


def in_snapshots(expected: dict) -> dict:
    """Each expected file read from the synced snapshot of its disk."""
    out = {}
    for disk in ARCH:
        number = synced_number(disk)
        ok, bad = [], []
        for key, sha in sorted(expected.items()):
            if key.startswith(f"{disk}:"):
                rel = key.split(":", 1)[1]
                path = f"{DATA[disk]}/.snapshots/{number}/snapshot/{rel}"
                rc, text = attempt(f"sha256sum {path}")
                (ok if rc == 0 and text.split()[0] == sha else bad).append(rel)
        out[disk] = {"synced_snapshot": number, "bit_exact": ok, "missing_or_differs": bad}
    return out


def snap_kill(ph: str, bt: str) -> None:
    """A snapshot-supported sync killed at a position (only the snapraid process), changes on a
    surviving disk after the kill, arch2 fails, fix with the snapshots, resume."""
    layout = locate()
    old_arch2 = {k: v for k, v in layout.items() if k.startswith("arch2:")}
    top = 1 + max(v[1] for v in layout.values())
    new = [
        ("arch0", "branch/f2.bin", "sf2", 240 * BLOCK),
        ("arch1", "branch/g2.bin", "sg2", 80 * BLOCK),
        ("arch2", "branch/n3.bin", "sn3", 80 * BLOCK),
    ]
    fill(new)
    at_kill = disk_expected("arch2")
    log = f"{LOGS}/snapkill.log"
    in_background("snapkill", f"{bt} sync --gui --bw-limit={SNAP_BW} -l {log}")
    note(ph, "snapkill", kill_at_position("snapkill", top + 120))
    server.wait_until_succeeds(f"test -s {LOGS}/snapkill.rc", timeout=120)
    note(
        ph,
        "snapkill_wrapper",
        {
            "rc": int(run(f"cat {LOGS}/snapkill.rc")),
            "out_tail": tail(run(f"cat {LOGS}/snapkill.out"), 4),
            "snapper": lists(),
        },
    )
    note(ph, "snapkill_status", brief(snapraid("snapkill-status", "status"), 4))
    note(ph, "snapkill_layouts", {"before": layout, "after_kill": locate()})
    # An in-place change after the kill on arch0, at positions of an old arch2 file.
    victim = next(
        (
            k
            for k, v in sorted(layout.items())
            if k.startswith("arch0:")
            and any(v[0] <= w[1] and w[0] <= v[1] for w in old_arch2.values())
        ),
        None,
    )
    if victim:
        disk, rel = victim.split(":", 1)
        fill([(disk, rel, "changed-after-kill", int(run(f"stat -c %s {DATA[disk]}/{rel}")))])
    note(ph, "snapkill_changed_after_kill", {"arch0": victim, "old_arch2_layout": old_arch2})
    fail_disk("arch2")
    new_disk("arch2", snapshots=True)
    fix = snapraid("snapkill-fix", "fix -d arch2", tool=f"{bt} -v")
    report = fix_report(fix["log"])
    note(
        ph,
        "snapkill_fix",
        {
            **brief(fix, 30),
            "status": report["status"],
            "unrecoverable_positions": {k: ranges(v) for k, v in report["unrecoverable"].items()},
            "restored_vs_hashes_at_kill": compare_disk("arch2", at_kill),
        },
    )
    resume = snapraid("snapkill-resume", "resume --gui", tool=bt)
    note(ph, "snapkill_resume", {**brief(resume, 6), **run_pos_report(resume), "snapper": lists()})
    if resume["rc"] != 0:
        # snapperd keeps the snapshot list of a loaded config until it is unused for 30 s
        # (snapper server/snapperd.cc:54-55, 164-168): resume again with a fresh daemon. The
        # replaced disk has no `syncing` or `synced` snapshot for `resume` to select
        # (snapraid-btrfs:740-741, 1885-1889); then a new snapshot-supported sync instead.
        run("systemctl stop snapperd.service")
        fresh = snapraid("snapkill-resume-fresh", "resume --gui", tool=bt)
        note(ph, "snapkill_resume_after_snapperd_stop", {**brief(fresh, 6), "snapper": lists()})
        again = snapraid("snapkill-sync", "sync --gui", tool=bt)
        note(
            ph,
            "snapkill_sync_instead",
            {**brief(again, 6), **run_pos_report(again), "snapper": lists()},
        )
    note(ph, "snapkill_diff_end", brief(snapraid("snapkill-diff-end", "diff")))
    before = lists()
    rc, out = attempt(f"{bt} -u sync diff")
    note(
        ph,
        "use_snapshot_sync_diff",
        {"rc": rc, "out_tail": tail(out, 3), "before": before, "after": lists()},
    )


def df(path: str) -> int:
    run(f"sync -f {path}")
    return int(run(f"df --output=avail -B1 {path} | tail -n1"))


def write_files(prefix: str, count: int, size: int, stop_on_error: bool) -> list:
    rows = []
    for i in range(count):
        path = f"{DATA['arch2']}/branch/{prefix}-{i:03d}.bin"
        row = json.loads(run(f"python3 -c {shlex.quote(WRITER)} {path} wb /root/fill.src {size}"))
        rows.append({"path": path, **row})
        if row["errno"] and stop_on_error:
            break
    return rows


def phase_headroom() -> None:
    ph = "headroom"
    conf = "/root/headroom.conf"
    fail_disk("arch2")
    new_disk("arch2", snapshots=True)
    run("systemctl stop snapperd.service")  # no snapshot list of the old arch2 in its cache
    run("rm -f /mnt/parity/headroom.* /var/lib/snapraid/headroom.*")
    note(ph, "conf", write_conf(conf, ("arch2",), "headroom"))
    prefix = RESULT["phases"].get("snap", {}).get("prefix_used", "env LC_ALL=C ")
    bt = f"{prefix}snapraid-btrfs -c {conf} --snapper-configs arch2 --noninteractive"
    chunk = 32 * MIB
    run(f"python3 -c {shlex.quote(SOURCE_SCRIPT)} /root/fill.src {chunk}")
    avail0 = df(DATA["arch2"])
    count = int(avail0 * 0.5) // chunk
    first = write_files("d1", count, chunk, stop_on_error=True)
    note(
        ph,
        "start",
        {"avail_bytes": avail0, "d1_files": count, "d1_errors": [r for r in first if r["errno"]]},
    )
    sync = snapraid("headroom-sync1", "sync", conf=conf, tool=bt)
    note(
        ph,
        "sync_with_snapshot",
        {**brief(sync, 4), "avail_bytes": df(DATA["arch2"]), "snapper": snapper_list("arch2")},
    )
    # Delete all but two D1 files: deleting every file would trip SnapRAID's refusal to sync a
    # disk whose files all disappeared (cmdline/scan.c:1846-1891), not the space question.
    run(f"rm $(ls {DATA['arch2']}/branch/d1-*.bin | tail -n +3)")
    note(ph, "avail_after_deleting_d1_but_two", df(DATA["arch2"]))
    rows = write_files("d2", 64, chunk, stop_on_error=True)
    failed = rows[-1] if rows[-1]["errno"] else None
    note(
        ph,
        "d2_until_enospc",
        {
            "files_complete": sum(1 for r in rows if not r["errno"]),
            "bytes_written_total": sum(r["written"] for r in rows),
            "failed_write": failed,
            "failed_file_size": int(run(f"stat -c %s {failed['path']}")) if failed else None,
            "avail_bytes": df(DATA["arch2"]),
            "fi_usage": run(f"btrfs filesystem usage -b {DATA['arch2']} | head -12").splitlines(),
        },
    )
    rc, out = attempt("snapper -c arch2 create -p -d probe-at-enospc")
    note(ph, "snapper_create_at_enospc", {"rc": rc, "out_tail": tail(out, 3)})
    full = snapraid("headroom-sync-at-enospc", "sync", conf=conf, tool=bt)
    note(ph, "sync_at_enospc", {**brief(full, 10), "snapper": snapper_list("arch2")})
    note(ph, "status_at_enospc", brief(snapraid("headroom-status", "status", conf=conf), 4))
    note(ph, "diff_at_enospc", brief(snapraid("headroom-diff", "diff", conf=conf), 4))
    if failed:
        run(f"rm {failed['path']}")
    again = snapraid("headroom-sync2", "sync", conf=conf, tool=bt)
    note(
        ph,
        "sync_after_removing_partial_file",
        {**brief(again, 6), "snapper": snapper_list("arch2")},
    )
    rc, out = attempt(f"{bt} cleanup")
    note(
        ph,
        "cleanup",
        {
            "rc": rc,
            "out_tail": tail(out, 12),
            "snapper": snapper_list("arch2"),
            "avail_bytes": df(DATA["arch2"]),
        },
    )
