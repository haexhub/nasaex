# Probe of a client delete during the M1 copy (task T037f, follow-up of T037c). It measures, it
# does not judge, and it never writes a record. The mover primitive copies a pool file through the
# base pool and is held at a mid-step hook of M1 while the client unlinks the path through the SMB
# share; then the mover is released and mover_copy_item runs to its end. Two holds, one item each:
# `mid:M1:tmp-data-synced` (the temporary archive copy is complete and synced, not yet renamed to
# the final name) and `mid:M1:tmp-partial` (half of the bytes written). Measured at each hold:
# the branches that hold the path and the directory listing of every branch (which shows the
# temporary name) before the delete, after the delete and after the mover ended, the visibility
# through the share and on the server, the effective `func.unlink`, the exit status of the copy
# and whether the final archive copy is byte-identical to the original item. The helpers and the
# setup come from delete_probe_setup.py (prepended by concurrent_delete.nix). Result:
# probe-concurrent-delete-mid-copy.json in the working directory of the driver and a
# PROBE-RESULT line in the log. Measured outcome: concurrent_delete_mid_copy.results.md.
import json
from pathlib import Path

from testlib.faults import arm, release, wait_reached

HOLDS = {"mid:M1:tmp-data-synced": "probe/synced.bin", "mid:M1:tmp-partial": "probe/partial.bin"}


def listings() -> dict:
    """`ls -A` of the probe directory, read directly on each branch (shows temporary names)."""
    return {name: run(server, f"ls -A {root}/probe").split() for name, root in BRANCHES.items()}


def observe(item: str) -> dict:
    return {
        "branches": holders(item),
        "branch_listings": listings(),
        "visible": visible(item),
        "share_listing": run(client, f"ls -A {SHARE}/probe").split(),
        "share_actimeo0_listing": run(client, f"ls -A {SHARE_UNCACHED}/probe").split(),
    }


def held_delete(hook: str, item: str) -> dict:
    run(server, f"head -c 1048576 /dev/urandom > {POOL}/{item} && chown spike {POOL}/{item}")
    item_sha = run(server, f"sha256sum {POOL}/{item}").split()[0]
    tag = hook.rsplit(":", 1)[1]
    arm(server, hook)
    # The driver runs commands under `set -e`, which a failing copy would carry into the
    # subshell; `|| rc=$?` keeps the exit status of the copy.
    run(
        server,
        f"(rc=0; mover-effects mover_copy_item {POOL}/{item} /mnt/base/{item} 1 || rc=$?; "
        f"echo $rc > /run/{tag}.rc) > /run/{tag}.log 2>&1 &",
    )
    wait_reached(server, hook, timeout=120)
    before = observe(item)

    delete_ms = timed_client(f"rm {SHARE}/{item}")
    after_delete = observe(item) | {"effective_func_unlink": effective_unlink()}

    release(server, hook)
    server.wait_for_file(f"/run/{tag}.rc", timeout=120)
    copy_rc = int(run(server, f"cat /run/{tag}.rc").strip())
    after_mover = observe(item)
    # Later looks, 3 s and 35 s after the mover ended: the cifs attribute cache (default 1 s), the
    # cifs directory cache (default 30 s) and the mergerfs entry cache could hide a path that
    # appeared on a branch after the client deleted it.
    after_mover["later"] = {}
    for seconds, pause in ((3, 3), (35, 32)):
        run(server, f"sleep {pause}")
        after_mover["later"][f"{seconds}s"] = {
            "visible": visible(item),
            "share_listing": run(client, f"ls -A {SHARE}/probe").split(),
            "share_actimeo0_listing": run(client, f"ls -A {SHARE_UNCACHED}/probe").split(),
        }

    archive = [b for b in after_mover["branches"] if b != "pool"]
    final_sha = {b: run(server, f"sha256sum {BRANCHES[b]}/{item}").split()[0] for b in archive}
    tmp_prefix = f".{Path(item).name}.tmp-"
    pool_present = "pool" in after_mover["branches"]
    return {
        "hook": hook,
        "item": item,
        "item_sha256": item_sha,
        "before_delete": before,
        "client_delete_ms": delete_ms,
        "after_delete": after_delete,
        "copy_exit_status": copy_rc,
        "copy_log": run(server, f"cat /run/{tag}.log").strip(),
        "after_mover": after_mover,
        "final_archive_copy_branches": archive,
        "final_archive_copy_identical": {b: h == item_sha for b, h in final_sha.items()},
        "tmp_names_after_mover": {
            b: [n for n in names if n.startswith(tmp_prefix)]
            for b, names in after_mover["branch_listings"].items()
        },
        # The state the mover's M2/M5 would find by the branch listing (the probe runs no M2).
        "state_for_m2_m5": (
            "pool file present"
            if pool_present
            else "pool file missing, archive copy present (trash path)"
            if archive
            else "pool file missing, archive copy missing (vanished-archive-gone)"
        ),
    }


for hook, item in HOLDS.items():
    RESULT[hook] = held_delete(hook, item)
    print(f"PROBE {hook}: {RESULT[hook]}")

RESULT["summary"] = {
    "configured_cache_pool_options": RESULT["configured_cache_pool_options"],
    "effective_func_unlink": RESULT["effective_func_unlink"],
    "probe_seconds": round(time.monotonic() - STARTED, 1),
} | {
    hook: {
        "branches_before_delete": r["before_delete"]["branches"],
        "branches_after_delete": r["after_delete"]["branches"],
        "branches_after_mover": r["after_mover"]["branches"],
        "listings_after_delete": r["after_delete"]["branch_listings"],
        "listings_after_mover": r["after_mover"]["branch_listings"],
        "visible_after_delete": r["after_delete"]["visible"],
        "visible_after_mover": r["after_mover"]["visible"],
        "share_listings_after_mover": {
            "share": r["after_mover"]["share_listing"],
            "share_actimeo0": r["after_mover"]["share_actimeo0_listing"],
        },
        "later_after_mover": r["after_mover"]["later"],
        "copy_exit_status": r["copy_exit_status"],
        "final_archive_copy_identical": r["final_archive_copy_identical"],
        "tmp_names_after_mover": r["tmp_names_after_mover"],
        "state_for_m2_m5": r["state_for_m2_m5"],
    }
    for hook, r in ((h, RESULT[h]) for h in HOLDS)
}
Path("probe-concurrent-delete-mid-copy.json").write_text(
    json.dumps(RESULT, indent=2, sort_keys=True)
)
print("PROBE-RESULT " + json.dumps(RESULT["summary"], sort_keys=True))
