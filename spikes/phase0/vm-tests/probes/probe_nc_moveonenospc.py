# NC versus moveonenospc probe (task T051c, research F10). It measures, it does not judge, and it
# never writes a record. The setup and `attempt` come from enospc_probe_setup.py (prepended by
# enospc.nix). The cache pool runs as in S1 (pool branch first, archive branches NC, create ff,
# minfreespace 64M) with moveonenospc=false (the S1 baseline unit) and moveonenospc=true (the
# S1 mount-variant mechanism, s1.context.Lab.variant); the effective values are read back.
#
# Documented expectation (mergerfs 2.41.1, mkdocs/docs/config/moveonenospc.md and
# src/fuse_write.cpp, src/fs_movefile_and_open.cpp, src/policy_pfrd.cpp, src/branch.cpp): when a
# write fails with ENOSPC or EDQUOT and moveonenospc is enabled (`true` selects the create policy
# pfrd), mergerfs runs that create policy over all branches, copies the file to the branch it
# picks, unlinks the source and retries the write; create policies skip RO and NC branches, a
# read-only filesystem and a branch with less than minfreespace available; with no branch left
# the original error is returned. The source branch itself is not excluded.
#
# Per mode and round, each from the S1.9 precondition (leave_bytes free on the pool branch):
# mount.cifs create and append and smbclient put through the share, and the probe writer's
# create and append on the server through the cache pool. Two more appends use a btrfs qgroup
# limit on the pool subvolume instead of the filler (the write fails with EDQUOT while statvfs
# still shows the pool free above minfreespace), the case in which the create policy can pick the
# pool itself. PROBE_ROUNDS (default 2). Result: probe-nc-moveonenospc.json in the working
# directory of the driver and a PROBE-RESULT line; nc_moveonenospc.results.md holds it.
from pathlib import Path

MODES = ("false", "true")
CACHE = "/mnt/cache"


def quota(name: str):
    """Precondition: a qgroup limit of the pool subvolume leave_bytes above what it references."""
    run(server, f"btrfs quota enable {POOL_BRANCH}")
    run(server, f"btrfs quota rescan -w {POOL_BRANCH}")
    subvolume = run(server, f"btrfs inspect-internal rootid {POOL_BRANCH}").strip()
    table = run(server, f"btrfs qgroup show --raw {POOL_BRANCH}")
    line = next(r for r in table.splitlines() if r.split()[:1] == [f"0/{subvolume}"])
    referenced = int(line.split()[1])
    run(server, f"btrfs qgroup limit {referenced + LEAVE} {POOL_BRANCH}")
    detail = (
        f"qgroup 0/{subvolume} references {referenced} bytes, limit {referenced + LEAVE}; "
        f"statvfs shows {free_bytes()} bytes free"
    )

    def undo() -> None:
        """Remove the pool qgroup limit and disable quotas after the measured attempt."""
        run(server, f"btrfs qgroup limit none {POOL_BRANCH}")
        run(server, f"btrfs quota disable {POOL_BRANCH}")

    return detail, undo


def cache_views() -> dict:
    """Return a file-size reader for the cache pool on the server."""
    return {"cache pool on the server": lambda rel: size_of(server, f"{CACHE}/{rel}")}


APPENDS = [
    f"{mode}-{case}"
    for mode in MODES
    for case in ("cifs-append", "local-append", "quota-local-append", "quota-cifs-append")
]
append_targets(APPENDS)
RESULT["modes"] = {}

for mode in MODES:
    with lab.variant({} if mode == "false" else {"moveonenospc": mode}):
        RESULT["modes"][mode] = {
            o: lab.effective(o)
            for o in ("moveonenospc", "minfreespace", "category.create", "branches")
        }
        print(f"PROBE mode {mode}: {RESULT['modes'][mode]}")
        for r in range(ROUNDS):
            d = f"enospc/r{r}"
            views = share_views(lab.share, lab.second)
            tag = f"moveonenospc={mode}"
            attempt(
                f"{tag} cifs create",
                f"{d}/{mode}-cifs-create.bin",
                cifs_write(lab.share, "wb"),
                views,
            )
            attempt(f"{tag} smbclient put", f"{d}/{mode}-put.bin", smbclient_put(lab.smb), views)
            attempt(
                f"{tag} cifs append",
                f"{d}/{mode}-cifs-append.bin",
                cifs_write(lab.share, "ab"),
                views,
                append=True,
            )
            attempt(
                f"{tag} local create",
                f"{d}/{mode}-local-create.bin",
                local_write(CACHE, "wb"),
                cache_views(),
            )
            attempt(
                f"{tag} local append",
                f"{d}/{mode}-local-append.bin",
                local_write(CACHE, "ab"),
                cache_views(),
                append=True,
            )
            attempt(
                f"{tag} qgroup local append",
                f"{d}/{mode}-quota-local-append.bin",
                local_write(CACHE, "ab"),
                cache_views(),
                append=True,
                precondition=quota,
            )
            attempt(
                f"{tag} qgroup cifs append",
                f"{d}/{mode}-quota-cifs-append.bin",
                cifs_write(lab.share, "ab"),
                views,
                append=True,
                precondition=quota,
            )

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-nc-moveonenospc.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
summary = [
    {k: a.get(k) for k in ("case", "client", "holders", "leftover_on_pool")}
    | {"archive_changed": a["archive_changed"], "archive_events": len(a["archive_events"])}
    for a in RESULT["attempts"]
]
print("PROBE-RESULT " + json.dumps(summary, sort_keys=True))
