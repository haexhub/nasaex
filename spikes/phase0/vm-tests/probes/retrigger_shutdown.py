"""The shutdown and data-safety part of the retrigger variant probe (task T051i,
probe_retrigger_v2.py): Fisher's exact test, the leftover-process and kill-wait lines of one
shutdown (PID 1 logs `<unit>: Unit process <pid> (<comm>) remains running after unit stopped.`,
systemd-shutdown logs `Waiting for process: <pid> (<comm>), ...` and `Sending SIGKILL to PID <pid>
(<comm>).`), the summary of the guest capture of the mergerfs servers (shutdown_observe.py), the
data checks after the next boot and the summary over the cycles. Standard library only; the
probe imports it as the script package `probes`. Scaffolding for the spikes, not product code."""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping

# The mergerfs FUSE server is the mergerfs binary started as mount.fuse.mergerfs (a symlink to
# mergerfs) by mount(8); comm is truncated to 15 characters.
MERGERFS_COMMS = frozenset({"mount.fuse.merg", "mount.mergerfs", "mergerfs"})
LONG_SHUTDOWN_S = 60.0  # far above a normal shutdown (about 5 s), below the 90 s kill wait

_LEFTOVER = re.compile(r"(\S+): Unit process (\d+) \(([^)]*)\) remains running after unit stopped")
_WAITING = re.compile(r"Waiting for process: (.*)$")
_PROCESS = re.compile(r"(\d+) \(([^)]*)\)")
_SIGKILL = re.compile(r"Sending SIGKILL to PID (\d+) \(([^)]*)\)")
_STAT = re.compile(r"^(\[\S+\]\.\w+)\s+(-?\d+)$")
# Kernel btrfs messages above info level, and the checksum and corruption reports.
_BTRFS_ERROR = re.compile(
    r"BTRFS (error|warning|critical|crit|alert|emerg)|csum failed|corrupt", re.IGNORECASE
)


def _table_p(a: int, b: int, c: int, d: int) -> float:
    """Hypergeometric probability of the 2x2 table [[a, b], [c, d]] with its margins."""
    n = a + b + c + d
    return math.comb(a + b, a) * math.comb(c + d, c) / math.comb(n, a + c)


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the table [[a, b], [c, d]]: the sum of the probabilities of
    every table with the same margins that is no more probable than the observed one."""
    if min(a, b, c, d) < 0:
        raise ValueError(f"counts must not be negative: {(a, b, c, d)}")
    row1, col1, n = a + b, a + c, a + b + c + d
    observed = _table_p(a, b, c, d)
    total = 0.0
    for x in range(max(0, col1 - (n - row1)), min(row1, col1) + 1):
        p = _table_p(x, row1 - x, col1 - x, n - row1 - col1 + x)
        if p <= observed * (1 + 1e-7):  # relative tolerance for equal probabilities
            total += p
    return min(1.0, total)


def compare_counts(x_yes: int, x_n: int, y_yes: int, y_n: int) -> dict:
    """Two rates `k of n` and the two-sided Fisher p of their difference."""
    return {
        "x": f"{x_yes} of {x_n}",
        "y": f"{y_yes} of {y_n}",
        "p": fisher_two_sided(x_yes, x_n - x_yes, y_yes, y_n - y_yes),
    }


def _processes(text: str) -> list[dict]:
    return [{"pid": int(p), "comm": c} for p, c in _PROCESS.findall(text)]


def classify_shutdown(console: Iterable[str], journal: Iterable[str], shutdown_s: float) -> dict:
    """One shutdown from its console lines (systemd-shutdown logs only there) and the journal of
    that boot: every leftover process with the source that showed it, whether a mergerfs server
    outlived its unit, which servers systemd-shutdown waited for or killed, and whether the
    shutdown took longer than LONG_SHUTDOWN_S."""
    leftovers: dict[tuple[str, int], dict] = {}
    for source, lines in (("console", list(console)), ("journal", list(journal))):
        for line in lines:
            match = _LEFTOVER.search(line)
            if match:
                key = (match.group(1), int(match.group(2)))
                entry = leftovers.setdefault(
                    key,
                    {
                        "unit": key[0],
                        "pid": key[1],
                        "comm": match.group(3),
                        "console": False,
                        "journal": False,
                    },
                )
                entry[source] = True
    waiting, sigkill = [], []
    for line in console:
        if match := _WAITING.search(line):
            waiting.extend(_processes(match.group(1)))
        if match := _SIGKILL.search(line):
            sigkill.append({"pid": int(match.group(1)), "comm": match.group(2)})
    servers = [e for e in leftovers.values() if e["comm"] in MERGERFS_COMMS]
    return {
        "leftovers": sorted(leftovers.values(), key=lambda e: (e["unit"], e["pid"])),
        "outlived": bool(servers),
        "outlived_units": sorted({e["unit"] for e in servers}),
        "waiting": waiting,
        "sigkill": sigkill,
        "kill_wait": any(p["comm"] in MERGERFS_COMMS for p in waiting + sigkill),
        "shutdown_s": shutdown_s,
        "long_shutdown": shutdown_s >= LONG_SHUTDOWN_S,
    }


def capture_summary(capture: Mapping) -> dict:
    """The guest capture taken after both mergerfs mount units stopped: how many servers were
    still running and in which unit's cgroup, which of them were still alive after the capture's
    poll, the FUSE connections still open (/sys/fs/fuse/connections) with their waiting requests,
    the processes whose mount namespace still had a mergerfs mount, and the kernel wait channels
    of the servers' threads."""
    servers = capture.get("servers", [])
    gone = capture.get("gone_after_s", {})
    connections = capture.get("connections", {})
    gone_times = [t for t in gone.values() if t is not None]
    return {
        "servers": len(servers),
        "units": sorted({s["cgroup"].rsplit("/", 1)[-1] for s in servers}),
        "still_alive": sorted(int(pid) for pid, t in gone.items() if t is None),
        "gone_after_s_max": max(gone_times)
        if gone_times and len(gone_times) == len(gone)
        else None,
        "connections": len(connections),
        "waiting_requests": sum(int(c.get("waiting", 0)) for c in connections.values()),
        "holders": [f"{h['comm']}:{m}" for h in capture.get("holders", []) for m in h["mounts"]],
        "wchans": dict(
            sorted(Counter(t["wchan"] for s in servers for t in s.get("threads", [])).items())
        ),
    }


def parse_device_stats(text: str) -> dict[str, int]:
    """`btrfs device stats` lines `[<device>].<counter> <value>`."""
    return {m.group(1): int(m.group(2)) for line in text.splitlines() if (m := _STAT.match(line))}


def judge_data(
    files: Mapping[str, Mapping],
    scrub: tuple[int, str],
    stats: tuple[int, str],
    dmesg: Iterable[str],
    mounts: Mapping[str, str | None],
) -> dict:
    """The data checks after a boot: every file read through /mnt/cache and on /mnt/pool has the
    SHA-256 recorded when it was written, `btrfs scrub start -B` exited 0 with no errors,
    `btrfs device stats --check` exited 0 with every counter 0, the kernel log has no btrfs
    error, and every mount point has its expected type (/mnt/pool btrfs, the pools mergerfs)."""
    failed = []
    for name, seen in sorted(files.items()):
        if not (seen["expected"] == seen["cache"] == seen["pool"]):
            failed.append(f"file {name}: {dict(seen)}")
    status, out = scrub
    if status != 0 or "no errors found" not in out:
        failed.append(f"scrub exit {status}")
    status, out = stats
    counters = parse_device_stats(out)
    if status != 0 or not counters or any(counters.values()):
        failed.append(f"stats exit {status}: {sorted(k for k, v in counters.items() if v)}")
    errors = [line for line in dmesg if _BTRFS_ERROR.search(line)]
    if errors:
        failed.append(f"dmesg {errors[:5]}")
    expected = {"/mnt/pool": "btrfs", "/mnt/base": "fuse.mergerfs", "/mnt/cache": "fuse.mergerfs"}
    for point, fstype in expected.items():
        if mounts.get(point) != fstype:
            failed.append(f"mount {point}: {mounts.get(point)}")
    return {"ok": not failed, "failed": failed, "files": len(files)}


def summarize_shutdowns(cycles: Iterable[Mapping]) -> dict:
    """Over the cycles: how many shutdowns had a mergerfs server outlive its unit (and in which
    units), a kill wait for one, a long shutdown, and the data checks after the next boot
    (`data` None: not checked, e.g. the pool was not mounted)."""
    cycles = list(cycles)
    shutdowns = [(c["index"], c["shutdown"]) for c in cycles]
    units = Counter(u for _, s in shutdowns for u in s["outlived_units"])
    checked = [c for c in cycles if c.get("data") is not None]
    return {
        "cycles": len(cycles),
        "outlived": sum(1 for _, s in shutdowns if s["outlived"]),
        "outlived_cycles": [i for i, s in shutdowns if s["outlived"]],
        "outlived_units": dict(sorted(units.items())),
        "kill_wait": sum(1 for _, s in shutdowns if s["kill_wait"]),
        "kill_wait_cycles": [i for i, s in shutdowns if s["kill_wait"]],
        "long_shutdown": sum(1 for _, s in shutdowns if s["long_shutdown"]),
        "data_checked": len(checked),
        "data_failed_cycles": [c["index"] for c in checked if not c["data"]["ok"]],
        "data_unchecked_cycles": [c["index"] for c in cycles if c.get("data") is None],
    }
