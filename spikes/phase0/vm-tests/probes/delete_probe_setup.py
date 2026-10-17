# Shared part of the delete probes (tasks T037c and T037f): the measurement helpers and the setup
# of the server (tiered-cache mounts, SMB share of the cache pool) and the client (two mounts of the
# share). concurrent_delete.nix prepends this file to the test script of each delete probe, so the
# names defined here are globals of that script. It measures, it never writes a record.
import time

STARTED = time.monotonic()
POOL = "/mnt/pool"
CACHE = "/mnt/cache"
SHARE = "/mnt/share"  # client mount of the share, default cifs options
SHARE_UNCACHED = "/mnt/share-actimeo0"  # second client mount without attribute caching
BRANCHES = {"pool": POOL} | {a["serial"]: a["branch"] for a in TOPOLOGY["archiveLayout"]}
RESULT: dict = {"environment": {}, "commands": [], "vm_resources": TOPOLOGY["vmResources"]}


def run(machine, command: str, timeout: int = 60) -> str:
    RESULT["commands"].append(f"{machine.name}$ {command}")
    return machine.succeed(command, timeout=timeout)


def status(machine, command: str, timeout: int = 60) -> int:
    RESULT["commands"].append(f"{machine.name}$ {command}")
    return machine.execute(command, timeout=timeout)[0]


def holders(path: str) -> list[str]:
    """The branches that hold `path`, listed directly on each branch, never through a pool."""
    return [
        name for name, root in BRANCHES.items() if status(server, f"test -e {root}/{path}") == 0
    ]


def visible(path: str) -> dict:
    return {
        "share": status(client, f"stat {SHARE}/{path}") == 0,
        "share_actimeo0": status(client, f"stat {SHARE_UNCACHED}/{path}") == 0,
        "cache_pool_on_server": status(server, f"stat {CACHE}/{path}") == 0,
    }


def effective_unlink() -> str:
    return run(
        server,
        "python3 -c \"import os; print(os.getxattr('/mnt/cache/.mergerfs', "
        "'user.mergerfs.func.unlink').decode())\"",
    ).strip()


def timed_client(command: str) -> int:
    """Run `command` on the client and return its duration in milliseconds (guest clock)."""
    out = run(client, f"s=$(date +%s%N); {command}; e=$(date +%s%N); echo $(((e - s) / 1000000))")
    return int(out.strip().splitlines()[-1])


# --- setup: topology, tiered-cache mounts, share, client mounts -------------------------------
start_all()
server.wait_for_unit("multi-user.target")
server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
for key, command in {
    "kernel": "uname -r",
    "mergerfs": "mergerfs -V 2>&1 | head -1",
    "samba": "smbd --version",
}.items():
    RESULT["environment"][key] = server.succeed(command).strip()

run(server, "spike-mkfs all")
for mount in [POOL, *(a["data"] for a in TOPOLOGY["archiveLayout"])]:
    run(server, f"mount {mount}")
run(server, "systemctl start mnt-base.mount mnt-cache.mount")
RESULT["data_subvolumes"] = {
    a["serial"]: run(server, f"ls -A {a['data']}").split() for a in TOPOLOGY["archiveLayout"]
}
# The options of the unit file; `systemctl show -p Options` reports the kernel mount options.
RESULT["configured_cache_pool_options"] = run(
    server, "systemctl cat mnt-cache.mount | sed -n 's/^Options=//p'"
).strip()
RESULT["effective_func_unlink"] = effective_unlink()

for root in BRANCHES.values():
    run(server, f"mkdir -m 0777 {root}/probe")
server.wait_for_unit("samba.target")
run(server, "printf 'probe\\nprobe\\n' | smbpasswd -s -a spike")
client.wait_for_unit("multi-user.target")
run(client, f"mkdir -p {SHARE} {SHARE_UNCACHED}")
run(client, f"mount -t cifs //server/cache {SHARE} -o username=spike,password=probe")
run(
    client,
    f"mount -t cifs //server/cache {SHARE_UNCACHED} -o username=spike,password=probe,actimeo=0",
)
