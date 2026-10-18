"""Parsing and summary of the boot-mount probe (task T051a, probe_boot_mounts.py): the systemd
properties and journal lines collected per boot, and the failure rate, the time-to-by-label
distribution and the host load split by outcome over all boots. Times are guest CLOCK_MONOTONIC
seconds (since the kernel started). Standard library only; the probe imports it as the script
package `probes`. Scaffolding for the spikes, not product code."""

import re
import statistics
from collections.abc import Iterable, Mapping

# `journalctl -o short-monotonic`: "[    4.102300] host ident[pid]: message".
_JOURNAL_LINE = re.compile(r"^\[\s*(\d+\.\d+)\]\s(.*)$")


def parse_properties(text: str) -> dict[str, str]:
    """`systemctl show` / `udevadm info --query=property` output as a dict."""
    props = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value
    return props


def monotonic_s(value: str | None) -> float | None:
    """A systemd monotonic timestamp in microseconds as seconds; 0 or empty means never."""
    if not value or value == "0":
        return None
    return int(value) / 1_000_000


def journal_lines(text: str) -> list[tuple[float, str]]:
    """The journal lines that carry a monotonic time, as (seconds, rest of the line)."""
    lines = []
    for line in text.splitlines():
        match = _JOURNAL_LINE.match(line)
        if match:
            lines.append((float(match.group(1)), match.group(2)))
    return lines


def window(
    lines: list[tuple[float, str]],
    center: float | None,
    before: float = 2.0,
    after: float = 1.0,
    limit: int = 60,
) -> list[tuple[float, str]]:
    """The lines from `before` seconds ahead of `center` to `after` seconds past it."""
    if center is None:
        return []
    return [(t, s) for t, s in lines if center - before <= t <= center + after][:limit]


def first_time(lines: list[tuple[float, str]], needle: str, after: float = 0.0) -> float | None:
    """Time of the first line at or after `after` that contains `needle`."""
    return next((t for t, s in lines if t >= after and needle in s), None)


def boot_up(boot: Mapping) -> bool:
    """A boot is up when it has mounts and every one of them is active."""
    mounts = boot.get("mounts") or {}
    return bool(mounts) and all(m.get("ActiveState") == "active" for m in mounts.values())


def _spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def summarize(boots: Iterable[Mapping]) -> dict:
    """Failure rate, per-label time to the active by-label device unit (missing: the unit never
    became active in that boot), and the host 1-minute load of the up and the failed boots."""
    boots = list(boots)
    failed = [b["index"] for b in boots if not boot_up(b)]
    labels = sorted({label for b in boots for label in b.get("devices", {})})
    label_active = {}
    for label in labels:
        times = [b["devices"].get(label, {}).get("active_s") for b in boots]
        found = [t for t in times if t is not None]
        label_active[label] = {**_spread(found), "missing": len(times) - len(found)}
    return {
        "boots": len(boots),
        "failed": len(failed),
        "failed_boots": failed,
        "failure_rate": len(failed) / len(boots) if boots else None,
        "label_active_s": label_active,
        "host_load1": {
            "up": [b["host_load"][0] for b in boots if boot_up(b)],
            "failed": [b["host_load"][0] for b in boots if not boot_up(b)],
        },
    }
