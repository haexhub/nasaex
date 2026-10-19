"""Guest-side tool of the retrigger variant probe (task T051i), run on the server by
retrigger_v2.nix and probe_retrigger_v2.py. Probe-only, standard library only, times are
CLOCK_MONOTONIC seconds.

  capture <jsonl>   run by the ExecStop of probe-shutdown-capture.service, which is ordered before
                    mnt-base.mount and mnt-cache.mount and so stops after both at shutdown: every
                    mergerfs FUSE server still running (pid, cgroup, cmdline, /dev/fuse fds, per
                    thread state, wait channel, kernel stack, signal masks and system call), the
                    FUSE connections still open (/sys/fs/fuse/connections/<n>/waiting), the mount
                    namespaces that still hold a fuse.mergerfs mount, then a poll of up to POLL_S
                    for the servers to exit (the time each one went away) and, for any still
                    running, a second snapshot; only when SIGTERM_FLAG exists (a diagnostic run,
                    never a counted one, as it changes what systemd-shutdown later meets): SIGTERM
                    to each server still running, a poll of up to POLL_S and a third snapshot;
                    one JSON line with the boot id is appended to <jsonl> (fsynced) and a
                    one-line summary goes to stdout
  put <file> <bytes> <fsync 0|1>
                    write <bytes> random bytes to <file> (one write, then fsync or not, close) and
                    print their SHA-256
Scaffolding for the spikes, not product code."""

import hashlib
import json
import os
import sys
import time

# comm of the mergerfs server: mount(8) runs mount.fuse.mergerfs (a symlink to mergerfs), which
# mounts, forks and keeps serving in the child (libfuse/lib/helper.c fuse_daemonize).
COMMS = {"mount.fuse.merg", "mount.mergerfs", "mergerfs"}
POLL_S = 2.0
SIGTERM_FLAG = "/var/lib/probe-t051i/sigterm"


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _pids() -> list[int]:
    return sorted(int(p) for p in os.listdir("/proc") if p.isdigit())


def _thread(pid: int, tid: str) -> dict:
    base = f"/proc/{pid}/task/{tid}"
    stat = _read(f"{base}/stat")
    status = dict(
        line.split(":\t", 1) for line in _read(f"{base}/status").splitlines() if ":\t" in line
    )
    return {
        "tid": int(tid),
        "comm": _read(f"{base}/comm").strip(),
        "state": stat.rsplit(")", 1)[-1].split()[0] if stat else "?",
        "wchan": _read(f"{base}/wchan").strip() or "0",
        "stack": _read(f"{base}/stack").splitlines()[:8],
        # which signals the thread blocks, ignores and catches, and its current system call
        "signals": {
            k: status.get(k, "").strip() for k in ("SigPnd", "ShdPnd", "SigBlk", "SigIgn", "SigCgt")
        },
        "syscall": _read(f"{base}/syscall").split()[:3],
    }


def _fuse_fds(pid: int) -> int:
    count = 0
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                count += os.readlink(f"/proc/{pid}/fd/{fd}") == "/dev/fuse"
            except OSError:
                pass
    except OSError:
        pass
    return count


def servers() -> list[dict]:
    found = []
    for pid in _pids():
        if _read(f"/proc/{pid}/comm").strip() not in COMMS:
            continue
        try:
            tids = sorted(os.listdir(f"/proc/{pid}/task"), key=int)
        except OSError:
            continue
        found.append(
            {
                "pid": pid,
                "comm": _read(f"/proc/{pid}/comm").strip(),
                "cgroup": _read(f"/proc/{pid}/cgroup").strip(),
                "cmdline": _read(f"/proc/{pid}/cmdline").replace("\0", " ").strip(),
                "fuse_fds": _fuse_fds(pid),
                "threads": [_thread(pid, tid) for tid in tids],
            }
        )
    return found


def connections() -> dict:
    root = "/sys/fs/fuse/connections"
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {}
    return {n: {"waiting": int(_read(f"{root}/{n}/waiting").strip() or 0)} for n in names}


def holders() -> list[dict]:
    """One entry per mount namespace that still has a fuse.mergerfs mount."""
    seen: dict[str, dict] = {}
    for pid in _pids():
        try:
            namespace = os.readlink(f"/proc/{pid}/ns/mnt")
        except OSError:
            continue
        if namespace in seen:
            continue
        mounts = [
            line.split()[4]
            for line in _read(f"/proc/{pid}/mountinfo").splitlines()
            if " - fuse.mergerfs " in line
        ]
        if mounts:
            seen[namespace] = {
                "pid": pid,
                "comm": _read(f"/proc/{pid}/comm").strip(),
                "namespace": namespace,
                "mounts": mounts,
            }
    return list(seen.values())


def _poll(gone: dict[str, float | None]) -> dict[str, float | None]:
    """Up to POLL_S: the time after which each pid no longer runs a mergerfs server."""
    start = time.monotonic()
    while any(t is None for t in gone.values()) and time.monotonic() - start < POLL_S:
        for pid, t in gone.items():
            if t is None and _read(f"/proc/{pid}/comm").strip() not in COMMS:
                gone[pid] = round(time.monotonic() - start, 3)
        time.sleep(0.02)
    return gone


def capture(log_path: str) -> None:
    start = time.monotonic()
    found = servers()
    record = {
        "boot_id": _read("/proc/sys/kernel/random/boot_id").strip(),
        "t": round(start, 6),
        "servers": found,
        "connections": connections(),
        "holders": holders(),
    }
    gone = _poll({str(s["pid"]): None for s in found})
    record["gone_after_s"] = gone
    alive = [pid for pid, t in gone.items() if t is None]
    if alive:
        record["after"] = {"servers": servers(), "connections": connections()}
    if alive and os.path.exists(SIGTERM_FLAG):
        for pid in alive:
            os.kill(int(pid), 15)
        record["sigterm_gone_after_s"] = _poll(dict.fromkeys(alive))
        record["after_sigterm"] = {"servers": servers()}
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(
        f"PROBE-T051I-CAPTURE servers={[(s['pid'], s['cgroup']) for s in found]} "
        f"gone_after_s={gone} sigterm={record.get('sigterm_gone_after_s')} "
        f"connections={record['connections']} "
        f"holders={[(h['comm'], h['mounts']) for h in record['holders']]}",
        flush=True,
    )


def put(path: str, size: int, fsync: bool) -> None:
    data = os.urandom(size)
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    print(hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    if command == "capture":
        capture(args[0])
    elif command == "put":
        put(args[0], int(args[1]), args[2] == "1")
    else:
        raise SystemExit(f"unknown command {command!r}")
