# Shared part of the ENOSPC probes (tasks T051b and T051c): the setup of the S1 server and client
# and one measured write. enospc.nix prepends this file to the test script of each probe, so the
# names defined here are globals of that script. It measures, it never writes a record.
#
# Setup as in the S1 test script (vm-tests/s1/test_script.py): `spike-mkfs all`, the pool and the
# archive data subvolumes mounted, the base and the cache pool from their units, the S1 share
# `cache` with the S1 user, the S1 data set without its file above 4 GiB (s1.fixture), and the
# two S1 client sessions of the share (s1.context.Lab). Sizes come from the S1 criteria
# (tables.out_of_space). The source of every write is the same seeded file on the client and
# the server (probes.enospc_writes), so that a leftover's prefix can be compared with it.
#
# One write (`attempt`): the precondition (by default the S1.9 filler, testlib.faults.enospc,
# leaving leave_bytes free on the pool branch), an inotify watch on the three archive data
# subvolumes (branch and trash), the archive tree hashes and the pool manifest before; the
# write; then the branches that hold the path, size and SHA-256 of each copy, the sizes the
# given views report, the leftover classification, the free bytes, the archive tree hashes
# and the rest of the pool after, and the inotify events. The path is removed from every
# branch that holds it and the precondition is undone before the next write.
import json
import os
import shlex
import time

from probes.enospc_writes import SOURCE_SCRIPT, WRITER, leftover
from s1.context import Lab
from s1.fixture import ARCHIVE, FIXTURE, POOL, POOL_BRANCH, roots, trash

from testlib import faults
from testlib.fixtures import Entry, Fixture, content_sha256
from testlib.manifest import Item, compare, describe, tree_hash

STARTED = time.monotonic()
ROUNDS = int(os.environ.get("PROBE_ROUNDS", "2"))
TABLE = CRITERIA["tables"]["out_of_space"]
LEAVE, SIZE = TABLE["leave_bytes"], TABLE["write_bytes"]
SOURCE = "/root/enospc-source.bin"
HEAD = 4096  # size of every append target before its append (S1.9: 4096)
APPEND_SEED = "enospc-append"
ARCHIVE_KEYS = (*ARCHIVE, *(trash(s) for s in ARCHIVE))
ARCHIVE_DATA = " ".join(a["data"] for a in TOPOLOGY["archiveLayout"])
RESULT: dict = {"environment": {}, "commands": [], "vm_resources": TOPOLOGY["vmResources"]}
RESULT["sizes"] = {"leave_bytes": LEAVE, "write_bytes": SIZE, "append_head_bytes": HEAD}
RESULT["attempts"] = []


def run(machine, command: str, timeout: int = 300) -> str:
    """Record a distinct guest command and return its output, requiring success."""
    entry = f"{machine.name}$ {command}"
    if entry not in RESULT["commands"]:
        RESULT["commands"].append(entry)
    return machine.succeed(command, timeout=timeout)


def outcome(machine, command: str, timeout: int = 300) -> list:
    """A command whose failure is an outcome: [exit status, combined output]."""
    entry = f"{machine.name}$ {command}"
    if entry not in RESULT["commands"]:
        RESULT["commands"].append(entry)
    status, out = machine.execute(f"{command} 2>&1", timeout=timeout)
    return [status, out.strip()]


def size_of(machine, path: str) -> int | None:
    """Return the guest file size in bytes, or None if stat fails."""
    status, out = machine.execute(f"stat -c %s {shlex.quote(path)}")
    return int(out) if status == 0 else None


def free_bytes() -> int:
    """Return the available bytes on the server pool branch."""
    return int(run(server, f"df --output=avail -B1 {POOL_BRANCH} | tail -n1").strip())


_prefix: dict[int, str] = {}


def prefix_sha(n: int) -> str:
    """Return the cached SHA-256 digest of the first n source bytes."""
    if n not in _prefix:
        _prefix[n] = run(server, f"head -c {n} {SOURCE} | sha256sum").split()[0]
    return _prefix[n]


def range_sha(path: str, start: int, length: int | None = None) -> str:
    """SHA-256 of `length` bytes (None: to the end) of a server file from byte `start`."""
    count = "" if length is None else f" count={length}"
    return run(
        server,
        f"dd if={shlex.quote(path)} iflag=skip_bytes,count_bytes skip={start}{count} bs=1M"
        " status=none | sha256sum",
    ).split()[0]


def copy_on(key: str, rel: str, skip: int) -> dict:
    """Size and SHA-256 of a branch copy; for an append, of the bytes after the head, plus
    whether the head is still the head that was placed."""
    path = shlex.quote(lab.path(key, rel))
    found: dict = {"size": int(run(server, f"stat -c %s {path}")) - skip}
    found["sha256"] = run(server, f"tail -c +{skip + 1} {path} | sha256sum").split()[0]
    if skip:
        head = run(server, f"head -c {skip} {path} | sha256sum").split()[0]
        found["head_intact"] = head == content_sha256(APPEND_SEED, Entry(rel, POOL, size=skip))
    return found


def archive_hashes() -> dict:
    """Return tree hashes for every archive branch and trash tree."""
    return {key: tree_hash(lab.manifest(key)) for key in ARCHIVE_KEYS}


def pool_rest(rel: str):
    """The pool branch's manifest without `rel` and without the filler, which is never read
    (hashing its gigabytes twice per write would dominate the probe): the top-level names
    plus the manifest below every top-level directory."""
    names = run(server, f"ls -A {POOL_BRANCH}").split()
    rest = {name: Item("top-level", None, 0, None, (), None) for name in names}
    rest.pop(faults.DEFAULT_FILLER, None)
    for name in list(rest):
        if run(server, f"stat -c %F {POOL_BRANCH}/{name}").strip() == "directory":
            rest |= {f"{name}/{p}": i for p, i in lab.manifest(POOL, name).items()}
    return {p: i for p, i in rest.items() if p != rel}


def fill(name: str):
    """Fill the pool to LEAVE free bytes and return details plus a cleanup callback."""
    event = faults.enospc(server, POOL_BRANCH, LEAVE, point=name)
    return event.detail, lambda: run(server, f"rm -f {POOL_BRANCH}/{faults.DEFAULT_FILLER}")


def watch_start() -> None:
    """Start recursive archive change monitoring and wait for watch readiness."""
    run(server, "rm -f /run/inotify.log /run/inotify.err")
    run(
        server,
        "(inotifywait -m -r -e modify,attrib,close_write,create,delete,moved_to,moved_from "
        f"--format '%e %w%f' {ARCHIVE_DATA} > /run/inotify.log 2> /run/inotify.err &)",
    )
    server.wait_until_succeeds("grep -q 'Watches established' /run/inotify.err", timeout=60)


def watch_stop() -> list[str]:
    """Stop archive change monitoring and return the recorded event lines."""
    run(server, "pkill -x inotifywait; sleep 0.5; true")
    return run(server, "cat /run/inotify.log").splitlines()


def attempt(name: str, rel: str, write, views: dict, append: bool = False, precondition=fill):
    """One measured write of SIZE source bytes to `rel` (see the file header)."""
    detail, undo = precondition(name)
    row: dict = {"case": name, "path": rel, "precondition": detail, "free_before": free_bytes()}
    try:
        archive_before, pool_before = archive_hashes(), pool_rest(rel)
        watch_start()
        started = time.monotonic()
        row["client"] = write(rel)
        row["write_s"] = round(time.monotonic() - started, 2)
        row["views"] = {view: size(rel) for view, size in views.items()}
        row["holders"] = lab.holders(rel)
        row["copies"] = {k: copy_on(k, rel, HEAD if append else 0) for k in row["holders"]}
        row["free_after"] = free_bytes()
        row["archive_events"] = watch_stop()
        row["archive_changed"] = [k for k, h in archive_hashes().items() if h != archive_before[k]]
        row["pool_rest_differences"] = describe(compare(pool_before, pool_rest(rel)), limit=None)
        pool_copy = row["copies"].get(POOL)
        prefix = prefix_sha(pool_copy["size"]) if pool_copy else None
        row["leftover_on_pool"] = leftover(pool_copy, prefix, SIZE)
    finally:
        server.execute("pkill -x inotifywait")
        for key in lab.holders(rel):
            lab.remove(key, rel)
        undo()
    RESULT["attempts"].append(row)
    print(f"PROBE {name}: {json.dumps(row, sort_keys=True)}")
    return row


# --- writes through the client and on the server ---------------------------------------------
def cifs_write(mount, mode: str):
    """Return a writer for a client CIFS mount that reports the guest write outcome."""
    return lambda rel: json.loads(
        lab.python(client, WRITER, mount.path(rel), mode, SOURCE, str(SIZE))
    )


def local_write(root: str, mode: str):
    """Return a writer beneath a server root that reports the guest write outcome."""
    return lambda rel: json.loads(
        lab.python(server, WRITER, f"{root}/{rel}", mode, SOURCE, str(SIZE))
    )


def smbclient_put(smb):
    """Return a writer that uploads SOURCE and reports the smbclient outcome."""
    return lambda rel: list(smb.put(SOURCE, rel))


def share_views(first, second) -> dict:
    """Return file-size readers for both CIFS sessions and the server cache pool."""
    return {
        "writer session": lambda rel: size_of(client, first.path(rel)),
        "second session (actimeo=0)": lambda rel: size_of(client, second.path(rel)),
        "cache pool on the server": lambda rel: size_of(server, f"/mnt/cache/{rel}"),
    }


def append_targets(names) -> None:
    """A HEAD-byte pool file per append case and round, placed like the S1.9 append target."""
    entries = tuple(
        Entry(f"enospc/r{r}/{n}.bin", POOL, size=HEAD) for r in range(ROUNDS) for n in names
    )
    lab.place(Fixture(APPEND_SEED, entries))


# --- setup ------------------------------------------------------------------------------------
start_all()
server.wait_for_unit("multi-user.target")
server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
for key, (machine, command) in {
    "kernel": (server, "uname -r"),
    "mergerfs": (server, "mergerfs -V 2>&1 | head -1"),
    "samba": (server, "smbd --version"),
    "btrfs_progs": (server, "btrfs --version"),
    "client_kernel": (client, "uname -r"),
    "mount_cifs": (client, "mount.cifs -V"),
    "smbclient": (client, "smbclient --version"),
}.items():
    RESULT["environment"][key] = machine.succeed(command).strip()

run(server, "spike-mkfs all")
for mount in [POOL_BRANCH, *(a["data"] for a in TOPOLOGY["archiveLayout"])]:
    run(server, f"mount {mount}")
run(server, "systemctl start mnt-base.mount mnt-cache.mount")
server.wait_for_unit("samba.target")
lab = Lab(server, client, roots(TOPOLOGY), None, tables=CRITERIA["tables"])
run(server, f"printf '%s\\n%s\\n' {lab.password} {lab.password} | smbpasswd -s -a {lab.user}")
lab.place(
    Fixture(FIXTURE.seed, tuple(e for e in FIXTURE.entries if e.path != "large/archived.bin"))
)
client.wait_for_unit("multi-user.target")
lab.mount_clients()
RESULT["pool_data_profile"] = run(server, f"btrfs filesystem df {POOL_BRANCH}").strip()
for machine in (server, client):
    run(machine, f"python3 -c {shlex.quote(SOURCE_SCRIPT)} {SOURCE} {SIZE}")
SOURCE_SHA = {m.name: run(m, f"sha256sum {SOURCE}").split()[0] for m in (server, client)}
assert len(set(SOURCE_SHA.values())) == 1, f"the source differs between the VMs: {SOURCE_SHA}"
RESULT["source_sha256"] = SOURCE_SHA["server"]
RESULT["effective_baseline"] = {
    o: lab.effective(o) for o in ("category.create", "moveonenospc", "minfreespace", "cache.files")
}
