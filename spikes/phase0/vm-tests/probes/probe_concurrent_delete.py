# Probe of the concurrent-delete fault kind (task T037c, contracts/fault-points.md). It measures,
# it does not judge, and it never writes a record. Item 1 exists on the pool branch and, after
# the M1 copy of the mover primitive, as an archive copy; the mover is held at the named hook
# `post:M1` (the point of `inject:delete-pool-after:M1`) while the client unlinks the path
# through the SMB share. Measured: the branches that still hold the path (listed directly on
# each branch), the configured and the effective `func.unlink` of the cache pool (runtime
# xattr of the mergerfs control file) and whether the path is visible through the share and
# the cache pool. Then S1 scenarios 4 and 5 (in-place delete, delete-then-recreate) are
# exercised with the same settings. Result: probe-concurrent-delete.json in the working
# directory of the driver and a PROBE-RESULT line in the log; the commands run for the
# measurements are part of the result. Measured outcome: concurrent_delete.results.md.
# The helpers and the setup come from delete_probe_setup.py (prepended by concurrent_delete.nix).
import json
from pathlib import Path

from testlib.faults import arm, release, wait_reached

ITEM = "probe/item1.bin"
HOOK = "post:M1"


# --- concurrent delete with the mover held at post:M1 ----------------------------------------
run(server, f"head -c 1048576 /dev/urandom > {POOL}/{ITEM} && chown spike {POOL}/{ITEM}")
item_sha = run(server, f"sha256sum {POOL}/{ITEM}").split()[0]
arm(server, HOOK)
run(
    server,
    f"(mover-effects mover_copy_item {POOL}/{ITEM} /mnt/base/{ITEM} 1 && spike_hook {HOOK}; "
    "echo $? > /run/probe-mover.rc) > /run/probe-mover.log 2>&1 &",
)
wait_reached(server, HOOK, timeout=120)
before = holders(ITEM)
archive_copies = {b: run(server, f"sha256sum {BRANCHES[b]}/{ITEM}").split()[0] for b in before}

delete_ms = timed_client(f"rm {SHARE}/{ITEM}")
after = holders(ITEM)
seen = visible(ITEM)
effective_after = effective_unlink()
release(server, HOOK)
server.wait_for_file("/run/probe-mover.rc", timeout=120)
mover_rc = int(server.succeed("cat /run/probe-mover.rc").strip())

archive_survived = [b for b in after if b != "pool"]
RESULT["concurrent_delete"] = {
    "hook": HOOK,
    "item_sha256": item_sha,
    "branches_before_delete": before,
    "copies_before_delete_match_item": all(h == item_sha for h in archive_copies.values()),
    "client_delete_ms": delete_ms,
    "branches_after_delete": after,
    "effective_func_unlink_after_delete": effective_after,
    "visible_before_trash": seen,
    "archive_copy_survived": bool(archive_survived),
    "mover_exit_after_release": mover_rc,
}
print(f"PROBE concurrent delete: {RESULT['concurrent_delete']}")

# --- S1 scenario 4: an archive-only file deleted through the share -----------------------------
arch0, arch1, arch2 = (a["branch"] for a in TOPOLOGY["archiveLayout"])
run(server, f"echo archived > {arch0}/probe/s14.txt")
s14_ms = timed_client(f"rm {SHARE}/probe/s14.txt")
s14: dict = {
    "client_delete_ms": s14_ms,
    "branches_after_delete": holders("probe/s14.txt"),
    "visible_after_delete": visible("probe/s14.txt"),
    "pool_probe_dir": run(server, f"ls -A {POOL}/probe").split(),
}
s14["as_required"] = (
    s14["branches_after_delete"] == []
    and not any(s14["visible_after_delete"].values())
    and not any(name.startswith(".") or "s14" in name for name in s14["pool_probe_dir"])
)

# --- S1 scenario 5: delete then recreate, a file and a directory -------------------------------
run(server, f"echo old-archived > {arch1}/probe/s15.txt")
run(server, f"mkdir {arch2}/probe/s15dir && echo a > {arch2}/probe/s15dir/a.txt")
run(server, f"echo b > {arch2}/probe/s15dir/b.txt && chown -R spike {arch2}/probe/s15dir")
run(client, f"rm {SHARE}/probe/s15.txt && echo new-content > {SHARE}/probe/s15.txt")
run(client, f"rm -r {SHARE}/probe/s15dir && mkdir {SHARE}/probe/s15dir")
s15: dict = {
    "file_content_through_share": run(client, f"cat {SHARE}/probe/s15.txt").strip(),
    "file_content_through_share_actimeo0": run(
        client, f"cat {SHARE_UNCACHED}/probe/s15.txt"
    ).strip(),
    "file_branches": holders("probe/s15.txt"),
    "dir_listing_through_share": run(client, f"ls -A {SHARE}/probe/s15dir").split(),
    "dir_listing_through_share_actimeo0": run(
        client, f"ls -A {SHARE_UNCACHED}/probe/s15dir"
    ).split(),
    "dir_branches": holders("probe/s15dir"),
    "old_dir_entries_on_branches": holders("probe/s15dir/a.txt") + holders("probe/s15dir/b.txt"),
}
s15["as_required"] = (
    s15["file_content_through_share"] == "new-content"
    and s15["file_content_through_share_actimeo0"] == "new-content"
    and s15["dir_listing_through_share"] == []
    and s15["dir_listing_through_share_actimeo0"] == []
    and s15["old_dir_entries_on_branches"] == []
)
RESULT["s1_scenario_4"] = s14
RESULT["s1_scenario_5"] = s15

RESULT["summary"] = {
    "configured_func_unlink": next(
        (
            o
            for o in RESULT["configured_cache_pool_options"].split(",")
            if o.startswith("func.unlink")
        ),
        "none (mergerfs default)",
    ),
    "effective_func_unlink": RESULT["effective_func_unlink"],
    "branches_before_delete": before,
    "branches_after_delete": after,
    "archive_copy_survived_delete": bool(archive_survived),
    "visible_before_trash": seen["share"],
    # The trash outcome needs a vanished item whose archive copy is still present (mover
    # contract, known limit 4); measured here for the delete at post:M1 only.
    "trash_outcome_reachable_at_post_M1": bool(archive_survived),
    "s1_scenario_4_as_required": s14["as_required"],
    "s1_scenario_5_as_required": s15["as_required"],
    "probe_seconds": round(time.monotonic() - STARTED, 1),
}
Path("probe-concurrent-delete.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
print("PROBE-RESULT " + json.dumps(RESULT["summary"], sort_keys=True))
