# Append retry probe (task T051h, follow-up). The retry timeline probe found that an append
# retried within about 0.8 s of a failed append through mount.cifs wrote the source at the end of
# file the writer's session still believed in (head + leftover + zeros + source). This probe
# measures whether mergerfs causes it. It measures, it does not judge, and it never writes a
# record. The setup and the writers come from enospc_probe_setup.py (prepended by enospc.nix).
#
# Per repetition: a 4096-byte append target on the pool branch, the S1.9 filler (leave_bytes
# free), a failed append of write_bytes through the writer's mount, its leftover measured on the
# pool branch, the filler removed (as the check does), then the append again, and the result read
# on the pool branch: size, allocated blocks, head, leftover, the bytes between leftover and the
# appended source (zero count) and whether the source ends the file. Right before the retry the
# size the retrying mount shows for the file (`stat` through it) is recorded.
#   Vr  reference: through mergerfs (share `cache`), retry through the writer's own session at once
#   V5  the same through the control share `poolctl` (Samba directly on the pool branch)
#   V6  through mergerfs with the writer's mount at `actimeo=0`, retry through it at once
#   V7  as Vr with the retry DELAYS seconds after the failed append ended (DELAY_REPEATS each)
#   V8  through mergerfs, retry at once through a new mount.cifs session (`nosharesock`, mounted
#       after the failed append, unmounted after the retry)
# PROBE_REPEATS (default 5) repeats Vr, V5, V6 and V8, interleaved. Result:
# probe-append-retry.json in the working directory of the driver and a PROBE-RESULT line;
# retry_timeline.results.md holds it.
from pathlib import Path

from probes.retry_timeline import append_gap, summarize_appends

from testlib.smb import CifsMount, Share

REPEATS = int(os.environ.get("PROBE_REPEATS", "5"))
DELAYS, DELAY_REPEATS = (0.2, 0.5, 1.0, 1.5, 2.0, 3.0), 2
SHARE = lab.share.share
ctl = CifsMount(client, Share("server", "poolctl", lab.user or "", lab.password), "/mnt/ctl")
ctl.mount()
zero = CifsMount(client, SHARE, "/mnt/share0", ("actimeo=0",))
zero.mount()
RESULT["mounts"] = run(client, "grep cifs /proc/mounts").strip().splitlines()
RESULT["appends"] = []


def client_stat(mount, rel: str) -> dict:
    """Size and device of a file as a client mount shows it, or the error."""
    status, out = client.execute(f"stat -c '%s %d' {shlex.quote(mount.path(rel))} 2>&1")
    if status:
        return {"error": out.strip()}
    size, dev = out.split()
    return {"size": int(size), "dev": int(dev)}


def mount_dev(mount) -> int:
    return int(run(client, f"stat -c %d {shlex.quote(mount.mountpoint)}"))


def read_back(row: dict, rel: str) -> None:
    """The retried file on the pool branch (see the file header)."""
    path = f"{POOL_BRANCH}/{rel}"
    size, blocks, unit = map(int, run(server, f"stat -c '%s %b %B' {path}").split())
    kept = row["kept"]
    row |= {"size": size, "allocated_bytes": blocks * unit}
    row["unallocated_bytes"] = max(0, size - blocks * unit)
    row["gap_bytes"] = gap = append_gap(size, HEAD, kept, SIZE)
    head = content_sha256(APPEND_SEED, Entry(rel, POOL, size=HEAD))
    row["parts"] = {
        "head": range_sha(path, 0, HEAD) == head,
        "leftover": range_sha(path, HEAD, kept) == prefix_sha(kept),
        "source_ends_file": size >= SIZE
        and range_sha(path, size - SIZE) == RESULT["source_sha256"],
    }
    if gap > 0:
        window = f"iflag=skip_bytes,count_bytes skip={HEAD + kept} count={gap} bs=1M status=none"
        row["gap_nonzero_bytes"] = int(
            run(server, f"dd if={path} {window} | tr -d '\\000' | wc -c")
        )
    row["read_back"] = gap == 0 and all(row["parts"].values())


def one(variant: str, writer, d: str, delay: float | None = None, fresh: bool = False) -> None:
    """One failed append and its retry (see the file header)."""
    rel = f"{d}/append.bin"
    lab.place(Fixture(APPEND_SEED, (Entry(rel, POOL, size=HEAD),)))
    detail, undo = fill(variant)
    row: dict = {"variant": variant, "path": rel, "precondition": detail, "delay_target_s": delay}
    row["failed"] = cifs_write(writer, "ab")(rel)
    ended = time.monotonic()
    row["kept"] = (size_of(server, f"{POOL_BRANCH}/{rel}") or 0) - HEAD
    undo()
    retry = writer
    if fresh:
        retry = CifsMount(client, SHARE, "/mnt/fresh", ("nosharesock",))
        retry.mount()
    if delay is not None:
        time.sleep(max(0.0, ended + delay - time.monotonic()))
    row["client_before"] = client_stat(retry, rel)
    row["delay_s"] = round(time.monotonic() - ended, 3)
    row["retry"] = cifs_write(retry, "ab")(rel)
    row["devs"] = {"writer": mount_dev(writer), "retry": mount_dev(retry)}
    if fresh:
        run(client, f"umount {retry.mountpoint}")
    row["holders"] = lab.holders(rel)
    read_back(row, rel)
    lab.remove(POOL, d)
    RESULT["appends"].append(row)
    keys = ("delay_s", "client_before", "retry", "size", "gap_bytes", "read_back", "parts")
    print(f"PROBE {variant} {d}: {json.dumps({k: row.get(k) for k in keys}, sort_keys=True)}")
    Path("probe-append-retry.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))


for k in range(REPEATS):
    one("Vr", lab.share, f"t051h-append/Vr-{k}")
    one("V5", ctl, f"t051h-append/V5-{k}")
    one("V6", zero, f"t051h-append/V6-{k}")
    one("V8", lab.share, f"t051h-append/V8-{k}", fresh=True)
for k in range(DELAY_REPEATS):
    for delay in DELAYS:
        one("V7", lab.share, f"t051h-append/V7-{delay}-{k}", delay=delay)

RESULT["append_summary"] = summarize_appends(RESULT["appends"])
RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-append-retry.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
print("PROBE-RESULT " + json.dumps(RESULT["append_summary"], sort_keys=True))
