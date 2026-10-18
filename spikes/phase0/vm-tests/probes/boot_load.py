"""Host side and summary of the boot-under-load probe (task T051d, probe_boot_load.py): the guest
workload that copies the IO S1.8 and S1.11 do before the S1.11 reboot, the host pressure and
memory readings, the host IO load of variant D2 with its memory guard, and the summary over the
boots by outcome. Standard library only; the probe imports it as the script package `probes`.
Scaffolding for the spikes, not product code."""

import shlex
import statistics
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from probes.boot_timing import boot_up, summarize

MIN_AVAILABLE_MIB = 6 * 1024  # D2 stops its host load below this MemAvailable

# Guest writer of the new large file: the seeded content of S1.8 (testlib.fixtures, seed S1).
_WRITER = """import sys
from testlib.fixtures import Entry, content_chunks
path, rel, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(path, "wb") as handle:
    for chunk in content_chunks("S1", Entry(rel, "pool", size=size)):
        handle.write(chunk)"""
# Inverts the byte at one offset in place (S1.8 flip).
_FLIP = """import sys
with open(sys.argv[1], "r+b") as handle:
    handle.seek(int(sys.argv[2]))
    byte = handle.read(1)[0]
    handle.seek(int(sys.argv[2]))
    handle.write(bytes([byte ^ 0xFF]))"""
COPY = "large/copy-probe.bin"
NEW = "large/new-through-share.bin"


def heavy_io_steps(
    cache: str, branch: str, archived: str, new_size: int, flip_offset: int
) -> list[tuple[str, str]]:
    """The guest commands, in order, that redo on the server what S1.8 (baseline case) and the
    S1.11 manifest do through the share: read the archived file twice, copy it within the
    share, read and remove the copy, write and read a new file above 4 GiB, flip a byte of the
    archived file in place and read it through the cache pool and on its branch, remove the new
    file, read every file of the share. Nothing syncs, as in S1.8."""

    def q(path: str) -> str:
        return shlex.quote(path)

    src, copy, new = f"{cache}/{archived}", f"{cache}/{COPY}", f"{cache}/{NEW}"
    return [
        ("read the archived file (S1.8 mount.cifs)", f"sha256sum < {q(src)}"),
        ("read the archived file (S1.8 smbclient)", f"sha256sum < {q(src)}"),
        ("copy the archived file within the share", f"cat -- {q(src)} > {q(copy)}"),
        ("read the copy", f"sha256sum < {q(copy)}"),
        ("remove the copy", f"rm -f -- {q(copy)}"),
        (
            "write the new file",
            f"python3 -c {q(_WRITER)} {q(new)} {q(NEW)} {new_size}",
        ),
        ("read the new file", f"sha256sum < {q(new)}"),
        ("flip a byte above 4 GiB in place", f"python3 -c {q(_FLIP)} {q(src)} {flip_offset}"),
        ("read the flipped file (share)", f"sha256sum < {q(src)}"),
        ("read the flipped file (branch)", f"sha256sum < {q(f'{branch}/{archived}')}"),
        ("remove the new file", f"rm -f -- {q(new)}"),
        (
            "read the whole share (S1.11 manifest)",
            f"find {q(cache)} -type f -exec sha256sum -- {{}} + > /dev/null",
        ),
    ]


# --- host readings -----------------------------------------------------------------------------


def parse_pressure(text: str) -> dict[str, dict[str, float]]:
    """/proc/pressure/<resource>: {"some"|"full": {"avg10", "avg60", "avg300", "total"}};
    totals are microseconds of stall since the host booted."""
    found = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        found[kind] = {k: float(v) for k, _, v in (f.partition("=") for f in fields)}
    return found


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo as {key: value}; values with a unit are KiB."""
    found = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if sep and rest.split():
            found[key] = int(rest.split()[0])
    return found


def read_pressure(resource: str = "io") -> dict:
    return parse_pressure(Path(f"/proc/pressure/{resource}").read_text())


def read_meminfo() -> dict[str, int]:
    return parse_meminfo(Path("/proc/meminfo").read_text())


def stall_share(before: Mapping, after: Mapping, seconds: float, kind: str = "some"):
    """The fraction of `seconds` in which some (or all, `full`) tasks stalled on IO."""
    if seconds <= 0 or kind not in before or kind not in after:
        return None
    return (after[kind]["total"] - before[kind]["total"]) / 1e6 / seconds


def share_between(series: list[tuple[float, float]], t_from: float, t_to: float):
    """Stalled fraction between the first and the last sample inside [t_from, t_to] of a
    series of (seconds, stall total in microseconds); None without two samples."""
    inside = [(t, total) for t, total in series if t_from <= t <= t_to]
    if len(inside) < 2 or inside[-1][0] <= inside[0][0]:
        return None
    (t0, a), (t1, b) = inside[0], inside[-1]
    return (b - a) / 1e6 / (t1 - t0)


def memory_low(meminfo: Mapping[str, int], floor_mib: int = MIN_AVAILABLE_MIB) -> bool:
    """True when MemAvailable is below the floor or cannot be read."""
    available = meminfo.get("MemAvailable")
    return available is None or available < floor_mib * 1024


def zero_failure_bound(boots: int, confidence: float = 0.95) -> float:
    """Upper bound of the per-boot failure rate after `boots` boots without a failure (exact
    binomial, one-sided)."""
    if boots < 1:
        raise ValueError("no boots")
    return 1 - (1 - confidence) ** (1 / boots)


class HostSampler:
    """Samples host IO pressure and memory every `interval_s` from start() to stop(). A row of
    the series: seconds since start, io some avg10, io full avg10, io some total (us), io full
    total (us), Dirty, Writeback and MemAvailable in MiB."""

    def __init__(self, interval_s: float = 0.5):
        self.interval_s = interval_s
        self.series: list[tuple] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> None:
        io, mem = read_pressure("io"), read_meminfo()
        self.series.append(
            (
                round(time.monotonic() - self._t0, 3),
                io["some"]["avg10"],
                io["full"]["avg10"],
                io["some"]["total"],
                io["full"]["total"],
                mem.get("Dirty", 0) // 1024,
                mem.get("Writeback", 0) // 1024,
                mem.get("MemAvailable", 0) // 1024,
            )
        )

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample()

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._sample()
        self._thread.start()

    def stop(self, until_multi_user_s: float | None = None) -> dict:
        self._stop.set()
        self._thread.join()
        self._sample()
        rows = self.series
        some = [(r[0], r[3]) for r in rows]
        full = [(r[0], r[4]) for r in rows]
        early = until_multi_user_s if until_multi_user_s is not None else rows[-1][0]
        return {
            "seconds": rows[-1][0],
            "io_some_share": share_between(some, 0.0, rows[-1][0]),
            "io_full_share": share_between(full, 0.0, rows[-1][0]),
            "io_some_share_to_multi_user": share_between(some, 0.0, early),
            "io_full_share_to_multi_user": share_between(full, 0.0, early),
            "io_some_avg10_max": max(r[1] for r in rows),
            "io_full_avg10_max": max(r[2] for r in rows),
            "dirty_mib_max": max(r[5] for r in rows),
            "writeback_mib_max": max(r[6] for r in rows),
            "available_mib_min": min(r[7] for r in rows),
            "series": rows,
        }


class HostLoad:
    """Runs `command` (D2: a host `dd ... conv=fdatasync` that writes `target`) again and again
    in a thread until stop(). Between polls it reads the host memory: below `floor_mib` of
    MemAvailable it kills the command and stops for good (`aborted` names the reading).
    stop() kills a running command, removes `target` and reports what ran."""

    def __init__(
        self,
        command: list[str],
        target: Path,
        meminfo: Callable[[], Mapping[str, int]] = read_meminfo,
        floor_mib: int = MIN_AVAILABLE_MIB,
        interval_s: float = 0.25,
    ):
        self.command, self.target = command, Path(target)
        self.meminfo, self.floor_mib, self.interval_s = meminfo, floor_mib, interval_s
        self.started = self.completed = 0
        self.aborted: str | None = None
        self.available_mib_min: int | None = None
        self.last_output = ""
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _memory_ok(self) -> bool:
        info = self.meminfo()
        available = info.get("MemAvailable")
        if available is not None:
            mib = available // 1024
            self.available_mib_min = min(mib, self.available_mib_min or mib)
        if memory_low(info, self.floor_mib):
            self.aborted = f"MemAvailable {available} KiB below {self.floor_mib} MiB"
            self._kill()
            return False
        return True

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _loop(self) -> None:
        while not self._stop.is_set() and self._memory_ok():
            self._proc = subprocess.Popen(
                self.command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            self.started += 1
            while self._proc.poll() is None:
                if self._stop.wait(self.interval_s) or not self._memory_ok():
                    self._kill()
                    return
            output = self._proc.stderr.read().strip().splitlines()
            self.last_output = output[-1] if output else ""
            self.completed += self._proc.returncode == 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        self._kill()
        self._thread.join()
        self.target.unlink(missing_ok=True)
        return {
            "command": self.command,
            "started": self.started,
            "completed": self.completed,
            "aborted": self.aborted,
            "available_mib_min": self.available_mib_min,
            "last_output": self.last_output,
        }


# --- summary ---------------------------------------------------------------------------------


def summarize_load(boots: Iterable[Mapping]) -> dict:
    """The summary of the boot-mount probe plus the host IO pressure of the boot window split
    by outcome, and the 95 % upper bound of the failure rate when no boot failed."""
    boots = list(boots)
    summary = summarize(boots)

    def split(key: str) -> dict:
        found = {"up": [], "failed": []}
        for b in boots:
            found["up" if boot_up(b) else "failed"].append((b.get("host_window") or {}).get(key))
        return found

    keys = (
        "io_some_share",
        "io_full_share",
        "io_some_share_to_multi_user",
        "io_some_avg10_max",
        "io_full_avg10_max",
        "dirty_mib_max",
        "available_mib_min",
    )
    summary |= {key: split(key) for key in keys}
    workload = [b["workload_s"] for b in boots if b.get("workload_s") is not None]
    summary["workload_s_median"] = statistics.median(workload) if workload else None
    clean = bool(boots) and summary["failed"] == 0
    summary["zero_failure_bound_95"] = zero_failure_bound(len(boots)) if clean else None
    return summary
