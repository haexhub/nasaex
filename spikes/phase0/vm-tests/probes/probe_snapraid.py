# SnapRAID and snapshot-tool probe (task T057, research section 6 items 4, 15, 16, 17 and 19).
# It measures, it does not judge, and it never writes a record. snapraid.nix prepends
# snapraid_probe_setup.py (helpers) and probe_snapraid_snapshots.py (phases snap, headroom
# and headroom-sys); this file holds the phases alloc and kill and the main flow. PROBE_PHASES
# (default alloc,kill,snap,headroom,headroom-sys) selects phases; each starts from a fresh
# array. Scaffolding for the Phase 0 spikes, not product code.
#
# alloc: seeded files written in an order that differs from the alphabetical one; sync; the
#   positions `snapraid locate` reports against the first-fit hypothesis in physical order (and in
#   alphabetical order, as a control); deletes, an in-place rewrite and additions; sync; the
#   same comparison; the parity file against the XOR of the data blocks at every position of the
#   hypothesis; then the S2.2 history (an in-place change on arch0 and a delete on arch1 at
#   positions of arch2 files, files added on arch0 and arch2, an arch2 file rewritten), arch2
#   fails and `snapraid -d arch2 fix` runs; restored files against the hashes as of the sync;
#   fix again with `-i` and a timestamp-preserving copy of the deleted arch1 file.
# kill: a sync without --gui (no run:pos lines expected), a forced full sync with --gui
#   (unthrottled run:pos frequency), then deletes on arch1 and arch2 and additions on all disks,
#   a sync with --gui and --bw-limit killed with SIGKILL once run:pos >= KILL_AT; the state it
#   leaves (status, diff, locate, the parity file against the old and the new layout per
#   position); arch2 fails; fix, fix again with `-i` and a copy of the deleted arch1 file, and
#   with the hidden `--test-import-content` (to confirm what blocks the deleted blocks); a sync.
# Result: probe-snapraid.json in the working directory of the driver and PROBE lines.
from pathlib import Path

from probes.snapraid_observe import (
    allocate,
    blocks,
    extent,
    fix_report,
    parity_states,
    ranges,
    run_positions,
    step_stats,
    tag_fields,
)

KILL_AT = int(os.environ.get("PROBE_KILL_AT", "400"))
BW_LIMIT = os.environ.get("PROBE_BW_LIMIT", "20M")
# The alloc fixture, per disk in write order (which differs from the alphabetical order), and the
# changes before the second sync (after deleting arch0 z0 and arch1 d1): an in-place rewrite of
# arch0 c0 and two additions. The snap phase repeats it with snapshot-supported syncs.
ALLOC_INITIAL = [
    ("arch0", "branch/a/z0.bin", "z0", 4 * BLOCK),
    ("arch0", "branch/a/m0.bin", "m0", 2 * BLOCK + BLOCK // 2),
    ("arch0", "branch/a/c0.bin", "c0", BLOCK),
    ("arch0", "branch/b/q0.bin", "q0", 3 * BLOCK),
    ("arch1", "branch/y1.bin", "y1", 6 * BLOCK),
    ("arch1", "branch/d1.bin", "d1", 2 * BLOCK),
    ("arch1", "branch/k1.bin", "k1", 5 * BLOCK),
    ("arch2", "branch/x2.bin", "x2", 3 * BLOCK),
    ("arch2", "branch/e2.bin", "e2", 4 * BLOCK),
    ("arch2", "branch/n2.bin", "n2", 2 * BLOCK),
    ("arch2", "branch/t2.bin", "t2", BLOCK + BLOCK // 4),
]
ALLOC_CHANGED = [
    ("arch0", "branch/a/c0.bin", "c0-v2", BLOCK),
    ("arch0", "branch/a/f0.bin", "f0", 6 * BLOCK),
    ("arch1", "branch/g1.bin", "g1", BLOCK),
]


def hypothesis(kept: dict, added: list[str], sizes: dict, order: dict) -> dict:
    """First-fit per disk: kept files keep their positions, added files (sorted by `order`, a
    stable sort) take the lowest free positions (probes.snapraid_observe.allocate)."""
    out = {}
    for disk in ARCH:
        mine = {k: v for k, v in kept.items() if k.startswith(f"{disk}:")}
        new = sorted((k for k in added if k.startswith(f"{disk}:")), key=lambda k: order[k])
        out.update(allocate(mine, [(k, blocks(sizes[k])) for k in new]))
    return out


def against_locate(hyp: dict, layout: dict) -> dict:
    """Files whose hypothesised (first, last, fragments) differ from what locate reports."""
    keys = sorted(set(hyp) | set(layout))
    wrong = {k: [extent(hyp.get(k, [])), layout.get(k)] for k in keys}
    return {k: v for k, v in wrong.items() if v[0] != (tuple(v[1]) if v[1] else None)}


def physical_order(phys: dict) -> dict:
    return {k: (v is None, v or 0) for k, v in phys.items()}


def xor_summary(result: dict) -> dict:
    return {k: ranges(v) for k, v in parity_states(result).items() if v}


def phase_alloc() -> None:
    ph = "alloc"
    fresh_array(snapshots=False)
    note(ph, "conf", write_conf())
    sizes = {f"{d}:{r}": s for d, r, _, s in ALLOC_INITIAL}
    phys = fill(ALLOC_INITIAL)
    note(ph, "write_order_and_physical_start", phys)
    sync1 = snapraid("alloc-sync1", "sync")
    note(ph, "sync1", brief(sync1))
    note(ph, "sync1_run_pos_lines_without_gui", len(run_positions(sync1["log"])))
    layout1 = locate()
    note(ph, "locate1", layout1)
    hyp1 = hypothesis({}, list(phys), sizes, physical_order(phys))
    alpha = hypothesis({}, list(phys), sizes, {k: k for k in phys})
    note(ph, "locate1_vs_first_fit_physical_order", against_locate(hyp1, layout1))
    note(ph, "locate1_vs_first_fit_alphabetical_order", against_locate(alpha, layout1))

    # Holes and reuse: a delete and an in-place rewrite on arch0, a delete on arch1, additions.
    remove("arch0", "branch/a/z0.bin")
    remove("arch1", "branch/d1.bin")
    sizes.update({f"{d}:{r}": s for d, r, _, s in ALLOC_CHANGED})
    phys2 = fill(ALLOC_CHANGED)
    note(ph, "changes2_physical_start", phys2)
    diff2 = snapraid("alloc-diff2", "diff")
    note(ph, "diff_before_sync2", {**brief(diff2), "scan_tags": tail(grep_scan(diff2["log"]), 20)})
    sync2 = snapraid("alloc-sync2", "sync")
    note(ph, "sync2", brief(sync2))
    layout2 = locate()
    note(ph, "locate2", layout2)
    kept = {k: v for k, v in hyp1.items() if k in EXPECTED and k not in phys2}
    hyp2 = hypothesis(kept, list(phys2), sizes, physical_order(phys2))
    note(ph, "hypothesis2", {k: ranges(v) for k, v in sorted(hyp2.items())})
    note(ph, "locate2_vs_first_fit_physical_order", against_locate(hyp2, layout2))
    note(ph, "diff_after_sync2", brief(snapraid("alloc-diff3", "diff")))
    status = snapraid("alloc-status", "status")
    note(ph, "status_after_sync2", {"rc": status["rc"], "out": tail(status["out"], 14)})
    top = 1 + max(p for v in hyp2.values() for p in v)
    xor = xor_check({"new": position_map(hyp2, live_paths(hyp2), top + 2)}, PARITY)
    note(ph, "parity_vs_xor_of_hypothesis2", xor_summary(xor))

    # S2.2 history after the sync, then arch2 fails (plain SnapRAID, no snapshots).
    s22 = s22_history(hyp2, sizes)
    note(ph, "s22_history", s22)
    synced = s22.pop("synced_arch2")
    note(ph, "s22_fail_arch2", fail_disk("arch2"))
    new_disk("arch2", snapshots=False)
    fix = snapraid("alloc-fix", "-d arch2 fix")
    report = fix_report(fix["log"])
    note(ph, "s22_fix", {**brief(fix), "status": report["status"]})
    note(ph, "s22_fix_positions_vs_hypothesis2", fixed_vs(report, hyp2))
    note(ph, "s22_restored_vs_synced_hashes", compare_disk("arch2", synced))
    # The manual's retry for files removed after the last sync: fix again with -i pointing at a
    # copy of the deleted arch1 file (its timestamps kept).
    again = snapraid("alloc-fix-import", "-d arch2 -i /root/import-alloc fix")
    report = fix_report(again["log"])
    note(
        ph,
        "s22_fix_with_import",
        {
            **brief(again),
            "status": report["status"],
            "fixed_positions": {k: ranges(v) for k, v in report["fixed"].items()},
            "restored_vs_synced_hashes": compare_disk("arch2", synced),
        },
    )


def grep_scan(log: str) -> str:
    return "\n".join(line for line in log.splitlines() if line.startswith("scan:"))


def fixed_vs(report: dict, hyp: dict) -> dict:
    """Parity positions in fix's `fixed:` tags against the hypothesised positions per file."""
    return {
        k: {"fixed": ranges(v), "hypothesis": ranges(hyp.get(k, []))}
        for k, v in report["fixed"].items()
    }


def s22_history(hyp: dict, sizes: dict) -> dict:
    """The S2.2 changes after a completed sync (the probe writes directly to the branches; the
    S2 history of T065 adds files through the mover path and changes them through the share)."""

    def overlapping(disk: str, target: str) -> str:
        return next(
            k
            for k, v in sorted(hyp.items())
            if k.startswith(f"{disk}:") and set(v) & set(hyp[target])
        )

    modified = overlapping("arch0", "arch2:branch/e2.bin")
    deleted = overlapping("arch1", "arch2:branch/x2.bin")
    synced = disk_expected("arch2")
    affected = set(hyp[modified]) | set(hyp[deleted])
    disk, rel = modified.split(":", 1)
    fill([(disk, rel, "modified-after-sync", sizes[modified])])
    remove(*deleted.split(":", 1), keep_copy=f"/root/import-alloc/{deleted.rsplit('/', 1)[1]}")
    fill(
        [
            ("arch0", "branch/added-after-sync.bin", "a0-new", 2 * BLOCK),
            ("arch2", "branch/w2.bin", "w2", BLOCK),
            ("arch2", "branch/t2.bin", "t2-v2", BLOCK + BLOCK // 4),
        ]
    )
    rule = {
        k: ("affected" if set(v) & affected else "unaffected")
        for k, v in sorted(hyp.items())
        if k.startswith("arch2:")
    }
    return {
        "modified_in_place": modified,
        "deleted": deleted,
        "changed_positions": ranges(sorted(affected)),
        "block_rule_per_arch2_file": rule,
        "added_after_sync": ["arch0:branch/added-after-sync.bin", "arch2:branch/w2.bin"],
        "rewritten_on_arch2_after_sync": "arch2:branch/t2.bin",
        "synced_arch2": synced,
    }


def phase_kill() -> None:
    ph = "kill"
    fresh_array(snapshots=False)
    write_conf()
    initial = [
        ("arch0", "branch/p0.bin", "p0", 160 * BLOCK),
        ("arch1", "branch/q0.bin", "q0", 480 * BLOCK),
        ("arch1", "branch/q1.bin", "q1", 160 * BLOCK),
        ("arch2", "branch/a.bin", "ka", 160 * BLOCK),
        ("arch2", "branch/h.bin", "kh", 160 * BLOCK),
        ("arch2", "branch/c.bin", "kc", 160 * BLOCK),
        ("arch2", "branch/d.bin", "kd", 160 * BLOCK),
    ]
    sizes = {f"{d}:{r}": s for d, r, _, s in initial}
    phys0 = fill(initial)
    sync0 = snapraid("kill-sync0", "sync")
    note(
        ph, "sync0_without_gui", {**brief(sync0), "run_pos_lines": len(run_positions(sync0["log"]))}
    )
    full = snapraid("kill-sync-full", "--gui -F sync")
    note(ph, "full_sync_with_gui_unthrottled", run_pos_report(full))
    hyp0 = hypothesis({}, list(phys0), sizes, physical_order(phys0))
    note(ph, "locate0_vs_first_fit", against_locate(hyp0, locate()))

    remove("arch2", "branch/h.bin", keep_copy="/root/copies/h.bin")
    remove("arch1", "branch/q1.bin", keep_copy="/root/copies/q1.bin")
    new = [
        ("arch2", "branch/n1.bin", "kn1", 120 * BLOCK),
        ("arch2", "branch/n2.bin", "kn2", 160 * BLOCK),
        ("arch0", "branch/f.bin", "kf", 480 * BLOCK),
    ]
    sizes.update({f"{d}:{r}": s for d, r, _, s in new})
    phys1 = fill(new)
    kept = {k: v for k, v in hyp0.items() if k in EXPECTED}
    hyp1 = hypothesis(kept, list(phys1), sizes, physical_order(phys1))
    note(ph, "hypothesis_new_layout", {k: ranges(v) for k, v in sorted(hyp1.items())})
    at_kill = disk_expected("arch2")

    log = f"{LOGS}/kill-sync.log"
    in_background("kill-sync", f"snapraid -c {CONF} -l {log} --gui --bw-limit={BW_LIMIT} sync")
    note(ph, "kill", kill_at_position("kill-sync", KILL_AT))
    server.wait_until_succeeds(f"test -s {LOGS}/kill-sync.rc", timeout=60)
    killed = server.succeed(f"cat {log}")
    note(
        ph,
        "killed_sync",
        {
            "rc": int(run(f"cat {LOGS}/kill-sync.rc")),
            **run_pos_report({"log": killed, "rc": None, "seconds": None}),
            "log_tail": tail(killed, 4),
            "processes_after": attempt(f"pgrep -af '{SNAPRAID_PROCESS}'")[1].strip(),
            "files_after": run("ls -l --time-style=+%s /var/lib/snapraid /mnt/parity").splitlines(),
        },
    )
    status = snapraid("kill-status", "status")
    note(
        ph,
        "status_after_kill",
        {
            **brief(status, 14),
            "unsynced_tags": {
                k: v for k, v in status["summary"].items() if "unsynced" in k or k == "exit"
            },
        },
    )
    note(ph, "diff_after_kill", brief(snapraid("kill-diff", "diff")))
    note(ph, "locate_after_kill_vs_hypothesis", against_locate(hyp1, locate()))
    old_paths = {
        **live_paths(hyp0),
        "arch2:branch/h.bin": "/root/copies/h.bin",
        "arch1:branch/q1.bin": "/root/copies/q1.bin",
    }
    top = 1 + max(p for v in hyp1.values() for p in v)
    xor = xor_check(
        {
            "new": position_map(hyp1, live_paths(hyp1), top),
            "old": position_map(hyp0, old_paths, top),
        },
        PARITY,
    )
    note(ph, "parity_after_kill_per_position", xor_summary(xor))

    note(ph, "fail_arch2", fail_disk("arch2"))
    new_disk("arch2", snapshots=False)
    for name, args in (
        ("kill-fix", "-d arch2 fix"),
        ("kill-fix-import", "-d arch2 -i /root/import fix"),
        # Hidden test option of 14.4 that fills the hash import set strategy 2 reads
        # (snapraid.c:2012-2013, check.c:497-499); a confirmation of the cause, not a procedure.
        ("kill-fix-import-content", "-d arch2 --test-import-content /root/import fix"),
    ):
        if name == "kill-fix-import":
            run("mkdir -p /root/import && cp /root/copies/q1.bin /root/import/")
        fix = snapraid(name, args)
        report = fix_report(fix["log"])
        note(
            ph,
            name,
            {
                **brief(fix),
                "status": report["status"],
                "unrecoverable_positions": {
                    k: ranges(v) for k, v in report["unrecoverable"].items()
                },
                "fixed_positions": {k: ranges(v) for k, v in report["fixed"].items()},
                "restored_vs_hashes_at_kill": compare_disk("arch2", at_kill),
            },
        )
    after = snapraid("kill-sync-after-fix", "--gui sync")
    note(ph, "sync_after_fix", {**brief(after), **run_pos_report(after)})
    note(ph, "diff_after_sync", brief(snapraid("kill-diff-end", "diff")))


def run_pos_report(result: dict) -> dict:
    """run:begin and the run:pos lines of a --gui log: block position steps and processed-count
    steps between consecutive lines (the flush frequency, cmdline/state.c:5102-5131, 5231-5257)."""
    pos = run_positions(result["log"])
    begin = tag_fields(result["log"], "run", "begin")
    return {
        "run_begin_blockstart_blockmax_countmax": begin[0] if begin else None,
        "run_pos_lines": len(pos),
        "first_last": [pos[0], pos[-1]] if pos else None,
        "blockpos_steps": step_stats([p for p, _ in pos]),
        "countpos_steps": step_stats([c for _, c in pos]),
        "elapsed_field_last": tag_fields(result["log"], "run", "pos")[-1][7] if pos else None,
        "seconds": result.get("seconds"),
    }


# --- main --------------------------------------------------------------------------------
PARITY = "/mnt/parity/snapraid.parity"
start_all()
server.wait_for_unit("multi-user.target")
settle_jobs()
versions = {
    "kernel": "uname -r",
    "snapraid": "snapraid --version | head -1",
    "snapper": "snapper --version | head -1",
    "snapraid_btrfs": "snapraid-btrfs --version | head -1",
    "btrfs_progs": "btrfs --version",
    "bash": "bash --version | head -1",
    "shell_locale": "echo LANG=${LANG-} LC_ALL=${LC_ALL-} LOCALE_ARCHIVE=${LOCALE_ARCHIVE-}",
}
for key, command in versions.items():
    RESULT["environment"][key] = run(command).strip()
RESULT["environment"]["vm_resources"] = TOPOLOGY["vmResources"]
run(f"spike-mkfs parity && mount /mnt/parity && mkdir -p /var/lib/snapraid {LOGS} /root/copies")
for phase in PHASES:
    began = time.monotonic()
    {
        "alloc": phase_alloc,
        "kill": phase_kill,
        "snap": phase_snap,
        "headroom": phase_headroom,
        "headroom-sys": phase_headroom_sys,
    }[phase]()
    note(phase, "phase_seconds", round(time.monotonic() - began, 1))
    server.copy_from_machine(LOGS, phase)
    RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
    Path("probe-snapraid.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
print(f"PROBE-RESULT written probe-snapraid.json after {RESULT['seconds']} s")
