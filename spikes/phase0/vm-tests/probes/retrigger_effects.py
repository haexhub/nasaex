"""The side-effect probe of the pool retrigger unit (task T051g, probe_retrigger_effects.py):
parsing of the guest reader log and mount log (retrigger_observe.py), the uevent and journal
counts of a boot, the summary over the boots, the delay steps of the sweep, and the QEMU options
that attach a pool member which the VM definition leaves out. Times are guest CLOCK_MONOTONIC
seconds. Standard library only; the probe imports it as the script package `probes`.
Scaffolding for the spikes, not product code."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from probes.boot_timing import _spread

_READER = re.compile(r"^(\d+\.\d+) (\S+) (\S+)$")
_MOUNT = re.compile(r"^(\d+\.\d+) (mount|umount) (\d+) (\S+) (\S+) (.*)$")


def parse_reader_log(text: str) -> dict:
    """Reader log lines `<t> <key> <outcome>` (written on every change of a key's outcome) and
    `<t> heartbeat <reads>`; other lines are skipped."""
    transitions, heartbeats = [], []
    for line in text.splitlines():
        match = _READER.match(line.strip())
        if not match:
            continue
        t, key, value = float(match.group(1)), match.group(2), match.group(3)
        if key == "heartbeat":
            heartbeats.append((t, int(value)))
        else:
            transitions.append({"t": t, "key": key, "outcome": value})
    return {"transitions": transitions, "heartbeats": heartbeats}


def reader_errors_after_ok(transitions: Iterable[Mapping]) -> list[dict]:
    """Every outcome other than `ok` of a key after that key's first `ok`: before its first
    successful read a reader only sees the empty mount point."""
    seen_ok: set[str] = set()
    errors = []
    for entry in transitions:
        if entry["outcome"] == "ok":
            seen_ok.add(entry["key"])
        elif entry["key"] in seen_ok:
            errors.append(dict(entry))
    return errors


def parse_mount_log(text: str) -> list[dict]:
    """Mount log lines `<t> mount|umount <mount id> <target> <fstype> <source>`."""
    events = []
    for line in text.splitlines():
        match = _MOUNT.match(line.strip())
        if match:
            t, action, mount_id, target, fstype, source = match.groups()
            events.append(
                {
                    "t": float(t),
                    "action": action,
                    "id": int(mount_id),
                    "target": target,
                    "fstype": fstype,
                    "source": source,
                }
            )
    return events


def count_mount_events(events: Iterable[Mapping], since: float | None = None) -> dict:
    """Per mount target: how many mount and umount events (at or after `since`)."""
    counts: dict[str, Counter] = {}
    for event in events:
        if since is None or event["t"] >= since:
            counts.setdefault(event["target"], Counter())[event["action"]] += 1
    return {target: dict(c) for target, c in counts.items()}


def count_uevents(
    events: Iterable[Mapping], since: float | None = None, until: float | None = None
) -> dict:
    """Per device name: how many `<source> <action>` events of the udev monitor log lie in the
    window [since, until] (open ends when None)."""
    counts: dict[str, Counter] = {}
    for event in events:
        if (since is None or event["t"] >= since) and (until is None or event["t"] <= until):
            key = f"{event['source']} {event['action']}"
            counts.setdefault(event["sysname"], Counter())[key] += 1
    return {name: dict(c) for name, c in counts.items()}


def count_lines(
    lines: Sequence[tuple[float, str]], patterns: Mapping[str, str], since: float | None = None
) -> dict[str, int]:
    """Per named regular expression: how many journal lines (at or after `since`) match it."""
    compiled = {name: re.compile(p) for name, p in patterns.items()}
    return {
        name: sum(1 for t, s in lines if (since is None or t >= since) and rx.search(s))
        for name, rx in compiled.items()
    }


def activation_order(times: Mapping[str, float | None]) -> list[str]:
    """The units that became active, in the order of their activation times."""
    return [name for name, t in sorted(times.items(), key=lambda kv: kv[1] or 0) if t is not None]


def _histogram(values: Iterable) -> dict[str, int]:
    return dict(sorted(Counter(str(v) for v in values).items()))


def summarize_effects(boots: Iterable[Mapping]) -> dict:
    """Over the boots: activation time spreads, unit results, histograms of the per-boot counts
    (journal lines, uevents, mount events, smbd restarts) and the boots with a harm indicator
    (a reader error after its first good read, the pool device unit dead after it was active)."""
    boots = list(boots)
    units = sorted({u for b in boots for u in b.get("active_s", {})})
    active = {}
    for unit in units:
        times = [b["active_s"].get(unit) for b in boots]
        found = [t for t in times if t is not None]
        active[unit] = {**_spread(found), "missing": len(times) - len(found)}

    def nested(field: str) -> dict:
        keys = sorted({(k, j) for b in boots for k, v in b.get(field, {}).items() for j in v})
        out: dict = {}
        for outer, inner in keys:
            values = [b.get(field, {}).get(outer, {}).get(inner, 0) for b in boots]
            out.setdefault(outer, {})[inner] = _histogram(values)
        return out

    names = sorted({n for b in boots for n in b.get("journal_counts", {})})
    unit_results = [f"{b['unit'].get('Result')}/{b['unit'].get('ExecMainStatus')}" for b in boots]
    return {
        "boots": len(boots),
        "failed_boots": [b["index"] for b in boots if not b.get("up")],
        "active_s": active,
        "manager_finish_s": _spread(
            [b["manager_finish_s"] for b in boots if b["manager_finish_s"]]
        ),
        "host_start_to_multi_user_s": _spread(
            [b["timings"]["start_to_multi_user_s"] for b in boots]
        ),
        "host_start_to_no_jobs_s": _spread([b["timings"]["start_to_no_jobs_s"] for b in boots]),
        "unit_results": _histogram(unit_results),
        "journal_counts": {
            n: _histogram(b.get("journal_counts", {}).get(n, 0) for b in boots) for n in names
        },
        "uevents": nested("uevents"),
        "reader_errors_after_ok_boots": [b["index"] for b in boots if b["reader_errors_after_ok"]],
        "mount_events": nested("mount_events"),
        "orders": _histogram(" < ".join(b["order"]) for b in boots),
        "smbd_restarts": _histogram(b["smbd"].get("NRestarts") for b in boots),
        "pool_device_inactive_after_active_boots": [
            b["index"] for b in boots if b.get("pool_device_inactive_after_active")
        ],
    }


def parse_sweep(spec: str) -> list[tuple[int, int]]:
    """Delay steps `<not-ready s>:<ready s>,...` of the sweep variants, whole seconds >= 0."""
    steps = []
    for part in spec.split(","):
        fields = part.strip().split(":")
        if len(fields) != 2 or not all(f.isdigit() for f in fields):
            raise ValueError(f"malformed delay step {part!r} in {spec!r}: want <s>:<s>")
        steps.append((int(fields[0]), int(fields[1])))
    return steps


def _drive_and_device(serial: str, image: str) -> tuple[str, str]:
    """QEMU drive and device options of a pool member, with the device id and serial the nodes
    give it (nix/nodes.nix), so it resolves at /dev/disk/by-id/virtio-<serial>."""
    if not image.startswith("/") or any(c in image for c in " ,\t\n"):
        raise ValueError(f"image path must be absolute without blanks or commas: {image!r}")
    drive = f"werror=report,format=qcow2,id=probe-{serial},if=none,file={image}"
    device = f"virtio-blk-pci,drive=probe-{serial},serial={serial},id=disk-{serial}"
    return drive, device


def attach_qemu_opts(serial: str, image: str) -> str:
    """QEMU_OPTS that attach the member from the start of the machine."""
    drive, device = _drive_and_device(serial, image)
    return f"-drive {drive} -device {device}"


def attach_commands(serial: str, image: str) -> list[str]:
    """QEMU monitor commands that hot-plug the member into the running machine."""
    drive, device = _drive_and_device(serial, image)
    return [f"drive_add 0 {drive}", f"device_add {device}"]


def monitor_reply(answer: str) -> str:
    """QEMU's reply to a human monitor command, without the echoed command line (terminal
    escapes up to the last erase-line sequence) and the prompt."""
    return answer.rsplit("\x1b[K", 1)[-1].replace("(qemu)", "").strip()
