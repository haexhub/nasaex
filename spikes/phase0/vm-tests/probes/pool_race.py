"""The pool race probe (task T051f, probe_pool_race.py): parsing of the udev monitor log and the
btrfs scan lines of a boot, the per-boot classification of the pool member that the kernel
reported not ready, and the summary over the boots. Times are guest CLOCK_MONOTONIC seconds.

Background (64-btrfs.rules of systemd 260.5): the first pool member whose `btrfs ready` ioctl
runs sees an incomplete volume (ID_BTRFS_READY=0, SYSTEMD_READY=0); the member that completes
the volume runs `udevadm trigger -s block -p ID_BTRFS_READY=0`, which re-sends a change event to
every block device whose udev database says ID_BTRFS_READY=0 at that moment. A member that
received no such change event and never became ready is `stuck`. Standard library only; the
probe imports it as the script package `probes`. Scaffolding for the spikes, not product code."""

import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from probes.boot_load import zero_failure_bound

# `udevadm monitor`: "UDEV  [5.290000] add      /devices/.../block/vdb (block)".
_HEADER = re.compile(r"^(UDEV|KERNEL)\s*\[(\d+\.\d+)\]\s+(\S+)\s+(\S+)\s+\((\S+)\)$")
# Kernel: "BTRFS: device label spikepool devid 1 transid 9 /dev/vdb (253:16) scanned by
# (udev-worker) (363)" (fs/btrfs/volumes.c, device_list_add, on first registration).
_SCAN = re.compile(
    r"BTRFS: device label (\S+) devid (\d+) transid \d+ /dev/(\S+) \(\d+:\d+\) "
    r"scanned by .* \((\d+)\)$"
)
STATE_KEYS = ("ID_BTRFS_READY", "SYSTEMD_READY")


def parse_monitor(text: str) -> list[dict]:
    """The events of `udevadm monitor --property` in log order: source (UDEV or KERNEL), time,
    action, devpath, sysname (last devpath component) and properties."""
    events: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            source, t, action, devpath, _ = header.groups()
            current = {
                "source": source,
                "t": float(t),
                "action": action,
                "devpath": devpath,
                "sysname": devpath.rsplit("/", 1)[-1],
                "props": {},
            }
            events.append(current)
        elif not line.strip():
            current = None
        elif current is not None:
            key, sep, value = line.partition("=")
            if sep:
                current["props"][key] = value
    return events


def scan_lines(lines: Iterable[tuple[float, str]], label: str) -> dict[str, dict]:
    """Per device name of `label`: time of its kernel registration, devid and udev worker PID."""
    scans = {}
    for t, text in lines:
        match = _SCAN.search(text)
        if match and match.group(1) == label:
            scans[match.group(3)] = {
                "t": t,
                "devid": int(match.group(2)),
                "worker_pid": int(match.group(4)),
            }
    return scans


def member_events(events: Iterable[Mapping], sysname: str) -> list[Mapping]:
    return [e for e in events if e["sysname"] == sysname]


def _state(event: Mapping) -> dict:
    return {key: event["props"].get(key) for key in STATE_KEYS}


def state_at(events: Iterable[Mapping], sysname: str, t: float) -> dict | None:
    """ID_BTRFS_READY and SYSTEMD_READY of the member's last processed (UDEV) event at or before
    `t`; None when udev had processed no event of it by then."""
    last = None
    for event in member_events(events, sysname):
        if event["source"] == "UDEV" and event["t"] <= t:
            last = event
    return _state(last) if last else None


def classify(
    events: Sequence[Mapping],
    members: list[str],
    scans: Mapping[str, Mapping],
    link_target: str | None,
    link_time: float | None,
) -> dict:
    """The race data of one boot. `members` are the pool device names (vdb, vdc), `link_target`
    the device name the by-label link resolves to and `link_time` its last change time."""
    not_ready = None
    for event in events:
        if (
            event["source"] == "UDEV"
            and event["action"] == "add"
            and event["sysname"] in members
            and event["props"].get("ID_BTRFS_READY") == "0"
        ):
            not_ready = event
            break
    name = not_ready["sysname"] if not_ready else None
    later = [e for e in member_events(events, name) if e["t"] > not_ready["t"]] if name else []
    changes = [e for e in later if e["source"] == "KERNEL" and e["action"] == "change"]
    ready = [e for e in later if e["source"] == "UDEV" and e["props"].get("ID_BTRFS_READY") == "1"]
    scanned = sorted((s["t"], dev) for dev, s in scans.items() if dev in members)
    gap = None
    if len(scanned) == 2:
        gap = round((scanned[1][0] - scanned[0][0]) * 1000, 3)
    return {
        "not_ready_member": name,
        "not_ready_processed_s": not_ready["t"] if not_ready else None,
        "retrigger_changes": len(changes) if name else None,
        "retrigger_change_s": [e["t"] for e in changes],
        "not_ready_member_ready_s": ready[0]["t"] if ready else None,
        "scan_gap_ms": gap,
        "scan_order": [dev for _, dev in scanned],
        "scan_workers": {dev: scans[dev]["worker_pid"] for _, dev in scanned},
        "link_target": link_target,
        "link_time_s": link_time,
        "state_at_link": (
            {m: state_at(events, m, link_time) for m in members} if link_time is not None else None
        ),
        "stuck": (not ready) if name else None,
    }


def _spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def summarize_race(boots: Iterable[Mapping]) -> dict:
    """Failed and stuck boots, how many retrigger change events reached the not-ready member
    (as a histogram), the scan gaps, and the 95 % failure-rate bound when nothing failed."""
    boots = list(boots)
    races = [(b["index"], b.get("race") or {}) for b in boots]
    failed = [b["index"] for b in boots if not b.get("up")]
    histogram = Counter(
        str(r["retrigger_changes"]) for _, r in races if r.get("retrigger_changes") is not None
    )
    gaps = [r["scan_gap_ms"] for _, r in races if r.get("scan_gap_ms") is not None]
    return {
        "boots": len(boots),
        "failed_boots": failed,
        "stuck_boots": [i for i, r in races if r.get("stuck")],
        "no_not_ready_member": [i for i, r in races if r.get("not_ready_member") is None],
        "retrigger_changes": dict(sorted(histogram.items())),
        "scan_gap_ms": _spread(gaps),
        "zero_failure_bound": zero_failure_bound(len(boots)) if boots and not failed else None,
    }
