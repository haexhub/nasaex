"""Boot diagnostics of S1.11 (task T051e): what the guest and the host looked like when the
reboot of S1.11 came up incomplete.

In the S1 shakedown one S1.11 reboot came up without any mount (every by-label device job hit
x-systemd.device-timeout=10s), and the probes T051a and T051d could not reproduce it outside the
S1 sequence. When the reboot comes up with a unit the check judges down, a by-label mount of
/etc/fstab missing, or the boot never settling, the check captures the boot state here before
anything else happens on the server. The capture only reads: it sends no command that changes
the guest, it never raises, and every command failure is recorded in the evidence, so it never
changes what the check judges. Selection and filtering are pure functions; `server` is a driver
machine (duck-typed). Scaffolding for the spikes, not product code."""

import re
import shlex
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

BY_LABEL = "/dev/disk/by-label/"
FULL_JOURNAL_S = 60.0  # the journal is kept whole up to this many seconds after kernel start
# After that only these identifiers (kernel, PID 1, udev daemon and its workers) are kept.
JOURNAL_IDENTS = ("kernel:", "systemd[1]:", "systemd-udevd[", "(udev-worker)[")
HOST_FILES = ("pressure/io", "loadavg")
COMMAND_TIMEOUT_S = 60
_STAMP = re.compile(r"^\[\s*(\d+\.\d+)\]")
_SAFE = re.compile(r"[A-Za-z0-9:_.]")
SHOW = (
    "Id,LoadState,ActiveState,SubState,Result,InactiveExitTimestampMonotonic,"
    "ActiveEnterTimestampMonotonic,InactiveEnterTimestampMonotonic,JobRunningTimeoutUSec"
)


def label_mounts(fstab: str) -> dict[str, str]:
    """Mount point -> label of every /etc/fstab line whose source is a by-label link; the root
    filesystem is not one of the spike's mounts."""
    mounts = {}
    for line in fstab.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith(BY_LABEL) and fields[1] != "/":
            mounts[fields[1]] = fields[0].removeprefix(BY_LABEL)
    return mounts


def missing_mounts(expected: Iterable[str], findmnt_targets: str) -> list[str]:
    """The expected mount points that `findmnt -rn -o TARGET` does not list."""
    mounted = set(findmnt_targets.split())
    return sorted(path for path in expected if path not in mounted)


def unit_name(path: str, suffix: str) -> str:
    """`systemd-escape --path --suffix=<suffix> <path>` for a path other than the root."""
    escaped = []
    for i, ch in enumerate(path.strip("/")):
        if ch == "/":
            escaped.append("-")
        elif _SAFE.fullmatch(ch) and not (i == 0 and ch == "."):
            escaped.append(ch)
        else:
            escaped.append(f"\\x{ord(ch):02x}")
    return "".join(escaped) + "." + suffix


def failed_units(listing: str) -> list[str]:
    """The unit column of `systemctl list-units --failed --no-legend`, bullet or not."""
    units = []
    for line in listing.splitlines():
        fields = [f for f in line.split() if f != "●"]
        if fields:
            units.append(fields[0])
    return units


def units_to_inspect(mounts: Mapping[str, str], failed: Iterable[str]) -> list[str]:
    """Every by-label device and mount unit of the fstab mounts, and every failed unit."""
    devices = {unit_name(BY_LABEL + label, "device") for label in mounts.values()}
    mount_units = {unit_name(where, "mount") for where in mounts}
    return sorted(devices | mount_units | set(failed))


def journal_window(journal: str, full_s: float = FULL_JOURNAL_S) -> str:
    """The lines of `journalctl -o short-monotonic` up to `full_s` seconds after the kernel
    started, after that only the lines of JOURNAL_IDENTS; lines without a time are kept."""
    kept = []
    for line in journal.splitlines():
        stamp = _STAMP.match(line)
        if stamp is None or float(stamp.group(1)) <= full_s:
            kept.append(line)
            continue
        fields = line[stamp.end() :].split(maxsplit=2)  # host, identifier, message
        if len(fields) >= 2 and fields[1].startswith(JOURNAL_IDENTS):
            kept.append(line)
    return "\n".join(kept)


def btrfs_lines(dmesg: str) -> list[str]:
    return [line for line in dmesg.splitlines() if "btrfs" in line.lower()]


def _read_proc(name: str) -> str:
    return Path("/proc", name).read_text()


def host_state(read: Callable[[str], str] = _read_proc) -> dict:
    """The host's IO pressure and load (the test driver runs on the host), and the time."""
    state: dict = {"time": time.time()}
    for name in HOST_FILES:
        try:
            state[name] = read(name)
        except Exception as error:  # evidence, never a reason to stop
            state[name] = f"unreadable: {error!r}"
    return state


class _Guest:
    """Runs read-only commands on the server and keeps each result; never raises."""

    def __init__(self, server, errors: list[str]):
        self.server = server
        self.errors = errors
        self.results: dict[str, dict] = {}

    def run(self, name: str, command: str) -> tuple[int | None, str]:
        command = f"{command} 2>&1"
        try:
            status, output = self.server.execute(command, timeout=COMMAND_TIMEOUT_S)
        except Exception as error:  # recorded, the check decides alone
            self.errors.append(f"{name}: {error!r}")
            self.results[name] = {"command": command, "error": repr(error)}
            return None, ""
        self.results[name] = {"command": command, "status": status, "output": output}
        return status, output


def capture_if_incomplete(
    server,
    units_active: Mapping[str, bool],
    *,
    settled: bool = True,
    host_at_start: Mapping | None = None,
    read_host: Callable[[str], str] = _read_proc,
) -> dict | None:
    """The diagnostics of an incomplete boot, or None when the boot is complete: settled, every
    judged unit active and every by-label mount of /etc/fstab mounted. Never raises."""
    data: dict = {
        "settled": settled,
        "units_active": dict(units_active),
        "host_at_start": host_at_start,
        "errors": [],
    }
    guest = _Guest(server, data["errors"])
    try:
        mounts = label_mounts(guest.run("fstab", "cat /etc/fstab")[1])
        missing = missing_mounts(mounts, guest.run("findmnt", "findmnt -rn -o TARGET")[1])
        if settled and all(units_active.values()) and not missing and not data["errors"]:
            return None
        data["host_at_capture"] = host_state(read_host)
        data["label_mounts"] = mounts
        data["missing_mounts"] = missing
        _capture(guest, mounts, data)
    except Exception as error:  # recorded, the check decides alone
        data["errors"].append(f"capture: {error!r}")
    data["commands"] = guest.results
    return data


def _capture(guest: _Guest, mounts: Mapping[str, str], data: dict) -> None:
    # Time-sensitive state first: which links exist now, against the guest's clocks.
    guest.run("clock", "date +%s.%N; cat /proc/uptime")
    guest.run("by_label", "ls -l --time-style=full-iso /dev/disk/by-label/")
    status, listing = guest.run("by_label_names", "ls -1 /dev/disk/by-label/")
    links = [name for name in listing.split() if name] if status == 0 else []
    if links:
        names = " ".join(shlex.quote(BY_LABEL + name) for name in links)
        guest.run("by_label_times", f"stat -c '%n %.9Y %.9Z' {names}")
    guest.run("failed_units", "systemctl list-units --failed --plain --no-legend --full --no-pager")
    guest.run("jobs", "systemctl list-jobs --no-legend --no-pager")
    guest.run("udev_queue_settled", "udevadm settle --timeout=0")
    for name in links:
        guest.run(f"udevadm_info {name}", f"udevadm info --query=all --name={BY_LABEL}{name}")
    status, disks = guest.run("disks", "lsblk -dnp -o NAME")
    for disk in disks.split() if status == 0 else []:
        guest.run(f"udevadm_info {disk}", f"udevadm info --query=all --name={disk}")
    failed = failed_units(guest.results["failed_units"].get("output", ""))
    units = " ".join(shlex.quote(unit) for unit in units_to_inspect(mounts, failed))
    guest.run("unit_status", f"systemctl status --no-pager --full --lines=50 {units}")
    guest.run("unit_show", f"systemctl show -p {SHOW} {units}")
    status, journal = guest.run("journal", "journalctl -b -o short-monotonic --no-pager")
    data["journal_first_60s_then_kernel_systemd_udev"] = journal_window(journal)
    guest.results["journal"]["output"] = f"(kept as journal_first_60s_..., status {status})"
    guest.run("previous_boot_tail", "journalctl -b -1 -n 200 -o short-monotonic --no-pager")
    guest.run("lsblk", "lsblk -f")
    status, dmesg = guest.run("dmesg", "dmesg")
    data["dmesg_btrfs"] = btrfs_lines(dmesg)
    guest.results["dmesg"]["output"] = f"(kept as dmesg_btrfs, status {status})"
    guest.run("pressure", "grep -H . /proc/pressure/io /proc/pressure/cpu /proc/pressure/memory")
    guest.run("uptime", "uptime")
