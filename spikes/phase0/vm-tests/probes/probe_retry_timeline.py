# S1.9 retry probe (task T051h). The amended S1.9 check (T047a) retries its three writes right
# after the server removed the last filler and the client deleted the leftovers of the create and
# the upload; in a shakedown the create retry failed with EROFS at open and the upload retry with
# NT_STATUS_MEDIA_WRITE_PROTECTED. This probe measures, it does not judge, and it never writes a
# record. The setup and the writers come from enospc_probe_setup.py (prepended by enospc.nix).
#
# Per repetition: the S1.9 sequence of the check (an append target of 4096 bytes; mount.cifs
# create, smbclient put, mount.cifs append, each from its own filler that leaves leave_bytes free,
# the filler removed on the server after each write; then the client deletes the leftovers of the
# create and the upload through the share; t0 is the end of that delete), then a variant:
#   V0  the three retries of the check at once (create, put, append of write_bytes)
#   V1  `sync -f` on the pool branch, then the retries
#   V2  `btrfs filesystem sync` on the pool branch, then the retries
#   V3  the timeline first (a 1 MiB write every 0.5 s from t0), then the retries
#   V4  V0 through the control share `poolctl` (Samba directly on the pool branch, no mergerfs)
# and a timeline of 90 s with a sample every 0.5 s (V0 to V2 and V4 after the retries): statvfs
# free of the pool branch and of the cache pool, the data space counters and the commit count of
# the pool's btrfs (sysfs), `btrfs filesystem df` and the space lines of `btrfs filesystem usage`,
# and a 1 MiB write through mount.cifs to a new name with its errno. Afterwards every retry that
# reported no error is read back on the pool branch, and an appended file longer than head,
# leftover and source is measured between leftover and source (`gap`). PROBE_ROUNDS (default 2)
# repeats all variants, interleaved; PROBE_VARIANTS (default all) selects them. Result:
# probe-retry-timeline.json in the working directory of the driver and a PROBE-RESULT line;
# retry_timeline.results.md holds it.
from pathlib import Path

from probes.retry_timeline import SAMPLER, parse_commit_stats, summarize, usage_lines

from testlib.smb import CifsMount, Share, SmbClient

TICK, DURATION, PERIOD = 1 << 20, 90.0, 0.5
EPSILON = 16 << 20  # slack below the freed bytes for the `need` mark of the summary
VARIANTS = os.environ.get("PROBE_VARIANTS", "V0,V1,V2,V3,V4").split(",")
PREP = {"V1": f"sync -f {POOL_BRANCH}", "V2": f"btrfs filesystem sync {POOL_BRANCH}"}
MINFREE = int(lab.effective("minfreespace"))
CONTROL = Share("server", "poolctl", lab.user or "", lab.password)
ctl = CifsMount(client, CONTROL, "/mnt/ctl")
ctl.mount()
ctl_smb = SmbClient(client, CONTROL)
UUID = run(server, f"findmnt -no UUID {POOL_BRANCH}").strip()
RESULT["pool_mount"] = run(server, f"grep ' {POOL_BRANCH} ' /proc/mounts").strip()
RESULT["minfreespace"] = MINFREE
RESULT["sampler"] = f"server$ python3 -c '<SAMPLER>' {UUID} {POOL_BRANCH} /mnt/cache"
RESULT["tick"] = f"client$ python3 -c '<WRITER>' <mount>/<dir>/tick-NNN.bin wb {SOURCE} {TICK}"
RESULT["repetitions"] = []


def sample() -> dict:
    """The server's space state now (see retry_timeline.SAMPLER)."""
    raw = json.loads(lab.python(server, SAMPLER, UUID, POOL_BRANCH, "/mnt/cache"))
    return {
        "pool_avail": raw["statvfs"][POOL_BRANCH]["avail"],
        "pool_free": raw["statvfs"][POOL_BRANCH]["free"],
        "cache_avail": raw["statvfs"]["/mnt/cache"]["avail"],
        "commits": parse_commit_stats(raw["commit_stats"]),
        "data": raw["data"],
        "fi_df": raw["fi_df"].strip().splitlines(),
        "fi_usage": usage_lines(raw["fi_usage"]),
    }


def brief(s: dict) -> dict:
    """The counters of a sample without the command outputs."""
    keep = ("pool_avail", "cache_avail", "commits")
    return {k: s[k] for k in keep} | {k: s["data"][k] for k in ("disk_used", "bytes_pinned")}


def since(t0: float) -> float:
    return round(time.monotonic() - t0, 3)


def timeline(mount, d: str, t0: float) -> list[dict]:
    """For DURATION seconds from now, a sample every PERIOD (at once when one took longer),
    each followed by a 1 MiB write to a new name."""
    start, rows, k = time.monotonic(), [], 0
    while time.monotonic() < start + DURATION:
        time.sleep(max(0.0, start + k * PERIOD - time.monotonic()))
        row = {"t": since(t0)} | sample()
        row["write_t"] = since(t0)
        rel = mount.path(f"{d}/tick-{k:03d}.bin")
        row["write"] = json.loads(lab.python(client, WRITER, rel, "wb", SOURCE, str(TICK)))
        rows.append(row)
        k += 1
    return rows


def sequence(name: str, mount, smb, d: str) -> tuple[dict, float]:
    """The failing writes of S1.9 and the deletes; the repetition record and t0."""
    lab.place(Fixture(APPEND_SEED, (Entry(f"{d}/append.bin", POOL, size=HEAD),)))
    run(server, f"sync -f {POOL_BRANCH}")
    rep: dict = {"free_synced_before": free_bytes(), "writes": {}}
    writes = (("create", cifs_write(mount, "wb")), ("put", smbclient_put(smb)))
    for step, write in (*writes, ("append", cifs_write(mount, "ab"))):
        rel = f"{d}/{step}.bin"
        detail, undo = fill(f"{name} {step}")
        filler = size_of(server, f"{POOL_BRANCH}/{faults.DEFAULT_FILLER}")
        row: dict = {"precondition": detail, "filler_bytes": filler}
        row["client"] = write(rel)
        row["ended"] = time.monotonic()
        row["leftover_bytes"] = (size_of(server, f"{POOL_BRANCH}/{rel}") or 0) - (
            HEAD if step == "append" else 0
        )
        row["free_before_filler_removal"] = free_bytes()
        undo()
        rep["writes"][step] = row
    removed = time.monotonic()
    rep["deletes"] = {
        step: lab.client_run(f"rm -f -- {shlex.quote(mount.path(f'{d}/{step}.bin'))}")
        for step, _ in writes
    }
    t0 = time.monotonic()
    rep["last_filler_removed_before_t0_s"] = round(t0 - removed, 3)
    for row in rep["writes"].values():
        row["ended_before_t0_s"] = round(t0 - row.pop("ended"), 3)
    w = rep["writes"]
    rep["freed_bytes"] = sum(
        (w["append"]["filler_bytes"], w["create"]["leftover_bytes"], w["put"]["leftover_bytes"])
    )
    rep["need"] = w["append"]["free_before_filler_removal"] + rep["freed_bytes"] - EPSILON
    return rep, t0


def retries(mount, smb, d: str, t0: float) -> dict:
    """The three writes of the check again, each with the space state before and after."""
    out = {}
    writes = (("create", cifs_write(mount, "wb")), ("put", smbclient_put(smb)))
    for step, write in (*writes, ("append", cifs_write(mount, "ab"))):
        rel = f"{d}/{step}.bin"
        row: dict = {"before": brief(sample()), "start_s": since(t0)}
        row["client"] = write(rel)
        row["end_s"] = since(t0)
        row["size_on_pool"] = size_of(server, f"{POOL_BRANCH}/{rel}")
        row["after"] = brief(sample())
        row["holders"] = lab.holders(rel)
        out[step] = row
    return out


def read_back(rep: dict, d: str) -> None:
    """Each retry without a reported error, read on the pool branch: the source, or for the
    append the placed head, the leftover of the failed append and then the source."""
    for step, row in rep["retries"].items():
        client_ok = row["client"][0] == 0 if step == "put" else row["client"]["errno"] is None
        if not client_ok or POOL not in row["holders"]:
            continue
        path = f"{POOL_BRANCH}/{d}/{step}.bin"
        if step != "append":
            row["reads_back"] = range_sha(path, 0) == RESULT["source_sha256"]
            continue
        kept = rep["writes"]["append"]["leftover_bytes"]
        head = content_sha256(APPEND_SEED, Entry(f"{d}/append.bin", POOL, size=HEAD))
        row["parts_read_back"] = {
            "head": range_sha(path, 0, HEAD) == head,
            "leftover": range_sha(path, HEAD, kept) == prefix_sha(kept),
            "source": range_sha(path, HEAD + kept) == RESULT["source_sha256"],
        }
        row["reads_back"] = all(row["parts_read_back"].values())
        size = row["size"] = int(run(server, f"stat -c %s {path}"))
        if size > HEAD + kept + SIZE:
            row["gap"] = gap(path, HEAD + kept, size - SIZE, kept)


def gap(path: str, start: int, end: int, kept: int) -> dict:
    """Bytes between the leftover of the failed append and the source the retry appended:
    zeros, the rest of the failed append's source (kept onwards), or other; and whether the
    retry's source follows it."""
    q, n = shlex.quote(path), end - start
    window = f"iflag=skip_bytes,count_bytes skip={start} count={n} bs=1M status=none"
    found = {
        "start": start,
        "bytes": n,
        "tail_is_source": range_sha(path, end) == RESULT["source_sha256"],
    }
    found["nonzero_bytes"] = int(run(server, f"dd if={q} {window} | tr -d '\\000' | wc -c"))
    rest = f"iflag=skip_bytes,count_bytes skip={kept} count={n} bs=1M status=none"
    found["is_source_continuation"] = (
        range_sha(path, start, n) == run(server, f"dd if={SOURCE} {rest} | sha256sum").split()[0]
    )
    return found


for r in range(ROUNDS):
    for variant in VARIANTS:
        d, name = f"t051h/{variant}-r{r}", f"{variant} r{r}"
        mount, smb = (ctl, ctl_smb) if variant == "V4" else (lab.share, lab.smb)
        rep, t0 = sequence(name, mount, smb, d)
        rep |= {"variant": variant, "round": r}
        if variant in PREP:
            prep: dict = {"before": brief(sample()), "start_s": since(t0)}
            prep["outcome"] = outcome(server, PREP[variant])
            prep["end_s"] = since(t0)
            rep["prep"] = prep
        if variant == "V3":
            rep["timeline"] = timeline(mount, d, t0)
        rep["retries"] = retries(mount, smb, d, t0)
        if variant != "V3":
            rep["timeline"] = timeline(mount, d, t0)
        rep["summary"] = summarize(rep["timeline"], rep["need"], MINFREE)
        read_back(rep, d)
        rep["holders_of_dir"] = lab.holders(d)
        rep["branches_after"] = lab.effective("branches")
        lab.remove(POOL, d)
        RESULT["repetitions"].append(rep)
        line = {k: rep[k] for k in ("variant", "round", "summary", "need", "freed_bytes")}
        line["retries"] = {s: (x["client"], x["holders"]) for s, x in rep["retries"].items()}
        print(f"PROBE {name}: {json.dumps(line, sort_keys=True)}")
        # Written after every repetition, so that a later failure keeps what was measured.
        Path("probe-retry-timeline.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))

RESULT["probe_seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-retry-timeline.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
summary = [
    {k: rep[k] for k in ("variant", "round", "summary")}
    | {s: x["client"] for s, x in rep["retries"].items()}
    for rep in RESULT["repetitions"]
]
print("PROBE-RESULT " + json.dumps(summary, sort_keys=True))
