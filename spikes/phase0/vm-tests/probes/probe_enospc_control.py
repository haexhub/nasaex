# ENOSPC control probe (task T051b): is the partial file S1.9 saw after a write into a full pool
# normal filesystem behaviour, or does mergerfs add it? It measures, it does not judge, and it
# never writes a record. The setup and `attempt` come from enospc_probe_setup.py (prepended by
# enospc.nix). Every write starts from the S1.9 precondition (leave_bytes free on the pool
# branch) and writes write_bytes of the seeded source, against four targets:
#   cache-share  the S1 share `cache` (Samba on the mergerfs cache pool): mount.cifs create and
#                append in the S1 client session, smbclient put (the S1.9 sequence)
#   pool-share   the control share `poolctl` (Samba directly on /mnt/pool, the pool branch: the
#                same btrfs RAID1 filesystem, mount options and free space, no mergerfs), the
#                same three writes with the same clients and session options
#   local-cache  on the server, through the cache pool without Samba: the probe writer, dd, cp
#   local-pool   on the server, directly on the pool branch: the probe writer, dd, cp
# PROBE_ROUNDS (default 2) repeats the whole sequence. Result: probe-enospc-control.json in the
# working directory of the driver and a PROBE-RESULT line; enospc_control.results.md holds it.
from pathlib import Path

from testlib.smb import CifsMount, Share, SmbClient

CONTROL = Share("server", "poolctl", lab.user or "", lab.password)
ctl = CifsMount(client, CONTROL, "/mnt/ctl")
ctl2 = CifsMount(client, CONTROL, "/mnt/ctl2", ("nosharesock", "actimeo=0"))
ctl.mount()
ctl2.mount()
ctl_smb = SmbClient(client, CONTROL)


def control_views() -> dict:
    """Return file-size readers for both control-share sessions and the pool branch."""
    return {
        "writer session": lambda rel: size_of(client, ctl.path(rel)),
        "second session (actimeo=0)": lambda rel: size_of(client, ctl2.path(rel)),
        "pool branch on the server": lambda rel: size_of(server, f"{POOL_BRANCH}/{rel}"),
    }


def local_views(root: str) -> dict:
    """Return a file-size reader beneath the given server root."""
    return {f"{root} on the server": lambda rel: size_of(server, f"{root}/{rel}")}


def shell(command: str):
    """Return a writer that substitutes a quoted relative path and records the outcome."""
    return lambda rel: outcome(server, command.format(path=shlex.quote(rel)))


APPENDS = ("cache-share-append", "pool-share-append", "local-cache-append", "local-pool-append")
append_targets(APPENDS)

for r in range(ROUNDS):
    d = f"enospc/r{r}"
    for target, root in (("local-cache", "/mnt/cache"), ("local-pool", POOL_BRANCH)):
        views = local_views(root)
        attempt(
            f"{target} writer create", f"{d}/{target}-create.bin", local_write(root, "wb"), views
        )
        attempt(
            f"{target} writer append",
            f"{d}/{target}-append.bin",
            local_write(root, "ab"),
            views,
            append=True,
        )
        attempt(
            f"{target} dd",
            f"{d}/{target}-dd.bin",
            shell(f"dd if={SOURCE} of={root}/{{path}} bs=1M conv=fsync"),
            views,
        )
        attempt(
            f"{target} cp", f"{d}/{target}-cp.bin", shell(f"cp {SOURCE} {root}/{{path}}"), views
        )
    for target, first, smb, views in (
        ("cache-share", lab.share, lab.smb, share_views(lab.share, lab.second)),
        ("pool-share", ctl, ctl_smb, control_views()),
    ):
        attempt(f"{target} cifs create", f"{d}/{target}-create.bin", cifs_write(first, "wb"), views)
        attempt(f"{target} smbclient put", f"{d}/{target}-put.bin", smbclient_put(smb), views)
        attempt(
            f"{target} cifs append",
            f"{d}/{target}-append.bin",
            cifs_write(first, "ab"),
            views,
            append=True,
        )

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-enospc-control.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
summary = [
    {k: a.get(k) for k in ("case", "client", "views", "holders", "leftover_on_pool")}
    | {"archive_changed": a["archive_changed"], "archive_events": len(a["archive_events"])}
    for a in RESULT["attempts"]
]
print("PROBE-RESULT " + json.dumps(summary, sort_keys=True))
