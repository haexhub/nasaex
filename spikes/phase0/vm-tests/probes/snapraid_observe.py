"""What the SnapRAID probe (probe_snapraid.py, task T057) reads, and its two guest scripts.

SnapRAID 14.4 writes machine-readable tag lines with `-l FILE`: colon-separated fields, with ':'
escaped as '\\d' and '\\' as '\\\\' inside a field (cmdline/support.c:632-670). `tag_fields`,
`summary`, `run_positions` and `fix_report` read them; `parse_locate` reads the report of
`snapraid locate` (cmdline/locate.c:47-55: byte offset, byte span and fragment count per file);
`first_physical` reads `filefrag -e`. `first_fit` and `allocate` are the probe's hypothesis of the
allocation (cmdline/scan.c:207-293 and 1755-1805: after the removals, the new files of one scan,
sorted by physical offset, each take the lowest positions without a live block of that disk),
checked against `locate` and the parity file, not a model of record (that is task T059).

FILL writes seeded files (testlib.fixtures content) in the given order with the given mtimes, so
that expected hashes come from the seed and never from the guest. XOR compares each parity block
with the XOR of the data blocks a map assigns to its position (the first parity level is the XOR
of the data blocks, raid/int.c:8-34; a short last block is zero padded, cmdline/handle.c:278-279).
Standard library and testlib only; the probe imports it as the script package `probes`.
Scaffolding for the Phase 0 spikes, not product code."""

import json
import re
import statistics
from itertools import pairwise

BLOCK = 256 * 1024  # SnapRAID's default block size (manual "blocksize")

FILL = """import json, os, sys
from testlib.fixtures import Entry, content_chunks
seed = sys.argv[1]
for path, key, size, mtime_ns in json.load(open(sys.argv[2])):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        for chunk in content_chunks(seed, Entry(key, "archive", size=size)):
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(path, ns=(mtime_ns, mtime_ns))"""

XOR = """import json, sys
spec = json.load(open(sys.argv[1]))
size = spec["block_size"]
out = {}
with open(spec["parity"], "rb") as parity:
    for name, positions in spec["maps"].items():
        out[name] = {}
        for pos, members in positions.items():
            acc = 0
            for path, index in members:
                with open(path, "rb") as handle:
                    handle.seek(index * size)
                    acc ^= int.from_bytes(handle.read(size).ljust(size, b"\\0"), "little")
            parity.seek(int(pos) * size)
            stored = int.from_bytes(parity.read(size).ljust(size, b"\\0"), "little")
            out[name][pos] = stored == acc
print(json.dumps(out))"""

_LOCATE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s(.+)$")
_FILEFRAG = re.compile(r"^\s*0:\s*\d+\.\.\s*\d+:\s*(\d+)\.\.")


def _unescape(field: str) -> str:
    out, chars = [], iter(field)
    for char in chars:
        if char == "\\":
            nxt = next(chars, "")
            out.append({"d": ":", "n": "\n", "r": "\r"}.get(nxt, nxt))
        else:
            out.append(char)
    return "".join(out)


def tag_fields(log: str, *prefix: str) -> list[list[str]]:
    """The fields after `prefix` of every tag line that starts with it."""
    rows = []
    for line in log.splitlines():
        fields = line.split(":")
        if tuple(fields[: len(prefix)]) == prefix:
            rows.append([_unescape(f) for f in fields[len(prefix) :]])
    return rows


def summary(log: str) -> dict[str, str]:
    """`summary:` and `content_info:` tags, keyed by the fields between tag and value."""
    out = {}
    for tag in ("summary", "content_info"):
        for fields in tag_fields(log, tag):
            if len(fields) >= 2:
                out[":".join(fields[:-1])] = fields[-1]
    return out


def run_positions(log: str) -> list[tuple[int, int]]:
    """(block position, processed count) of every `run:pos` line (cmdline/state.c:5256)."""
    return [(int(f[0]), int(f[1])) for f in tag_fields(log, "run", "pos")]


def step_stats(values: list[int]) -> dict:
    """Number of values and the min, median and max difference between consecutive values."""
    steps = [b - a for a, b in pairwise(values)]
    if not steps:
        return {"lines": len(values), "min": None, "median": None, "max": None}
    return {
        "lines": len(values),
        "min": min(steps),
        "median": statistics.median(steps),
        "max": max(steps),
    }


def parse_locate(text: str, block_size: int = BLOCK) -> dict[str, tuple[int, int, int]]:
    """Path -> (first position, last position, fragments) from `snapraid locate`."""
    out = {}
    for line in text.splitlines():
        match = _LOCATE.match(line)
        if match:
            offset, span, frags, path = match.groups()
            first = int(offset) // block_size
            out[path] = (first, first + int(span) // block_size - 1, int(frags))
    return out


def blocks(size: int, block_size: int = BLOCK) -> int:
    return -(-size // block_size)


def extent(positions: list[int]) -> tuple[int, int, int] | None:
    """(first, last, fragments) of a file's positions, as `locate` reports them."""
    if not positions:
        return None
    frags = 1 + sum(1 for a, b in pairwise(positions) if b != a + 1)
    return positions[0], positions[-1], frags


def first_fit(occupied: set[int], count: int) -> list[int]:
    out, pos = [], 0
    while len(out) < count:
        if pos not in occupied:
            out.append(pos)
        pos += 1
    return out


def allocate(kept: dict[str, list[int]], added: list[tuple[str, int]]) -> dict[str, list[int]]:
    """The hypothesis: kept files keep their positions, added files (in the given order, which
    for SnapRAID is the physical order) each take the lowest free positions."""
    out = {path: list(pos) for path, pos in kept.items()}
    occupied = {p for pos in out.values() for p in pos}
    for path, count in added:
        out[path] = first_fit(occupied, count)
        occupied.update(out[path])
    return out


def first_physical(text: str) -> int | None:
    """Physical start (filesystem blocks) of the first extent in `filefrag -e` output."""
    for line in text.splitlines():
        match = _FILEFRAG.match(line)
        if match:
            return int(match.group(1))
    return None


def fix_report(log: str) -> dict:
    """Per `disk:path`: the final status tag and the parity positions of the fixed and the
    unrecoverable blocks (cmdline/check.c:674-762, 1319, 1337, 1408)."""
    report: dict = {"status": {}, "fixed": {}, "unrecoverable": {}}
    for fields in tag_fields(log, "status"):
        if len(fields) >= 3:
            report["status"][f"{fields[1]}:{fields[2]}"] = fields[0]
    for tag in ("fixed", "unrecoverable"):
        for fields in tag_fields(log, tag):
            if len(fields) >= 3 and fields[0].isdigit():
                report[tag].setdefault(f"{fields[1]}:{fields[2]}", []).append(int(fields[0]))
    report["exit"] = summary(log).get("exit")
    return report


def snapper_userdata(text: str, key: str) -> list[tuple[int, str | None]]:
    """(snapshot number, value of `key` or None) from `snapper --jsonout -c <config> list`."""
    data = json.loads(text)
    snapshots = next(iter(data.values())) if data else []
    return [(s["number"], (s.get("userdata") or {}).get(key)) for s in snapshots]


def ranges(positions: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for pos in positions:
        if out and pos == out[-1][1] + 1:
            out[-1][1] = pos
        else:
            out.append([pos, pos])
    return out


def parity_states(result: dict[str, dict[str, bool]]) -> dict[str, list[int]]:
    """Positions grouped by which XOR map ("new", "old") the stored parity matched."""
    out: dict[str, list[int]] = {"both": [], "new": [], "old": [], "neither": []}
    for pos, new in result["new"].items():
        old = result.get("old", {}).get(pos, False)
        label = "both" if new and old else "new" if new else "old" if old else "neither"
        out[label].append(int(pos))
    return {k: sorted(v) for k, v in out.items()}
