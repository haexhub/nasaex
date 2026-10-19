"""Guest sampler and summary of the S1.9 retry timeline probe (task T051h,
probe_retry_timeline.py). SAMPLER reads, in one guest call on the server, the statvfs free
bytes of the given paths (the pool branch first), the data space counters of the pool's btrfs
in sysfs (/sys/fs/btrfs/<uuid>/allocation/data), its commit counter (commit_stats) and the
output of `btrfs filesystem df` and `btrfs filesystem usage` for the first path. The host
keeps the space lines of the usage output (`usage_lines`) and the commit count
(`parse_commit_stats`); `summarize` reduces one timeline to the times, in seconds since its
start, at which a write first succeeds, the pool branch first shows the freed space and btrfs
first commits. `append_gap` and `summarize_appends` serve the append retry probe
(probe_append_retry.py). Standard library only; the probe imports it as the script package `probes`.
Scaffolding for the spikes, not product code."""

SAMPLER = """import json, os, subprocess, sys
uuid, paths = sys.argv[1], sys.argv[2:]
base = f"/sys/fs/btrfs/{uuid}"
out = {"statvfs": {}}
for path in paths:
    st = os.statvfs(path)
    out["statvfs"][path] = {"avail": st.f_bavail * st.f_frsize, "free": st.f_bfree * st.f_frsize}
fields = ("disk_used", "disk_total", "bytes_used", "bytes_pinned", "bytes_may_use",
          "bytes_reserved")
out["data"] = {f: int(open(f"{base}/allocation/data/{f}").read()) for f in fields}
out["commit_stats"] = open(f"{base}/commit_stats").read()
for name, sub in (("fi_df", "df"), ("fi_usage", "usage")):
    done = subprocess.run(["btrfs", "filesystem", sub, "-b", paths[0]],
                          capture_output=True, text=True)
    out[name] = done.stdout + done.stderr
print(json.dumps(out))"""

_USAGE_KEYS = (
    "Device allocated:",
    "Device unallocated:",
    "Used:",
    "Free (estimated):",
    "Free (statfs, df):",
    "Data,",
)


def parse_commit_stats(text: str) -> int:
    """The number of transaction commits from /sys/fs/btrfs/<uuid>/commit_stats."""
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key == "commits":
            return int(value)
    raise ValueError(f"no commit count in {text!r}")


def usage_lines(text: str) -> list[str]:
    """The allocation and free-space lines of `btrfs filesystem usage -b`, and the data
    profile line; the per-device lines are dropped."""
    return [s for s in (line.strip() for line in text.splitlines()) if s.startswith(_USAGE_KEYS)]


def _first(timeline, test):
    return next((s["t"] for s in timeline if test(s)), None)


def summarize(timeline: list[dict], need: int, minfree: int) -> dict:
    """One timeline: samples with `t` (seconds since its start), `pool_avail` (statvfs avail
    of the pool branch), `commits` and the `write` report of the 1 MiB write at that instant.
    `need` is the free space expected once the deleted files are released, `minfree` the
    mergerfs minfreespace."""
    ok = [i for i, s in enumerate(timeline) if s["write"]["errno"] is None]
    first_ok = ok[0] if ok else len(timeline)
    errors = {f"{s['write']['errno']} at {s['write']['step']}" for s in timeline[:first_ok]}
    commits = timeline[0]["commits"] if timeline else None
    return {
        "samples": len(timeline),
        "first_write_ok_s": timeline[first_ok]["t"] if ok else None,
        "errors_before_ok": sorted(errors),
        "failed_after_ok": sum(1 for s in timeline[first_ok:] if s["write"]["errno"]),
        "first_free_need_s": _first(timeline, lambda s: s["pool_avail"] >= need),
        "first_free_minfree_s": _first(timeline, lambda s: s["pool_avail"] >= minfree),
        "first_commit_s": _first(timeline, lambda s: s["commits"] != commits),
        "pool_avail_at_first_ok": timeline[first_ok]["pool_avail"] if ok else None,
        "pool_avail_first": timeline[0]["pool_avail"] if timeline else None,
        "pool_avail_last": timeline[-1]["pool_avail"] if timeline else None,
    }


def append_gap(size: int, head: int, kept: int, written: int) -> int:
    """Bytes of an appended file between the leftover of the failed append and the retry's
    source: the file is head, leftover, then this many bytes, then the source."""
    return size - head - kept - written


def summarize_appends(rows: list[dict]) -> dict:
    """Per variant of the append retry probe: how many retries, how many left a gap before the
    source, the gap sizes, the sizes the client saw before the retry and how many read back as
    head, leftover and source."""
    out: dict = {}
    for row in rows:
        seen = out.setdefault(
            row["variant"],
            {"n": 0, "with_gap": 0, "gap_bytes": set(), "client_size_before": set()}
            | {"read_back_ok": 0, "unallocated_bytes": set()},
        )
        client = row["client_before"]
        seen["n"] += 1
        seen["with_gap"] += row["gap_bytes"] > 0
        seen["gap_bytes"].add(row["gap_bytes"])
        seen["client_size_before"].add(client.get("size", client.get("error")))
        seen["read_back_ok"] += bool(row["read_back"])
        seen["unallocated_bytes"].add(row["unallocated_bytes"])
    for seen in out.values():
        for key in ("gap_bytes", "client_size_before", "unallocated_bytes"):
            seen[key] = sorted(
                seen[key], key=lambda v: (isinstance(v, str), str(v) if isinstance(v, str) else v)
            )
    return out
