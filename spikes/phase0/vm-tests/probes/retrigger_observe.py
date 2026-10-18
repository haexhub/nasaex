"""Guest-side observers of the retrigger side-effect probe (task T051g), run on the server by
retrigger_effects.nix and probe_retrigger_effects.py. Probe-only, standard library only, times
are CLOCK_MONOTONIC seconds (the clock of the journal and of `udevadm monitor`).

  read <log> <file>...      every 20 ms read each file whole by path, and through one file
                            descriptor per file kept open after its first successful open
                            (key `fd:<file>`); a line `<t> <key> <outcome>` whenever a key's
                            outcome changes (`ok`, `mismatch:<bytes>` or the errno name), and
                            `<t> heartbeat <reads>` every 5 s
  mounts <log>              a line `<t> mount|umount <id> <target> <fstype> <source>` for every
                            change of the mount table under /mnt (poll on /proc/self/mountinfo)
  write <file> <seconds> <json>  write 64 KiB chunks with fsync (10 ms apart) into one open file for
                            <seconds>, then read it back; errors, latencies and the comparison
                            go to <json>
Scaffolding for the spikes, not product code."""

import errno
import json
import os
import select
import sys
import time

CHUNK = 64 * 1024
# The content the probe writes into the reader file (1 MiB).
EXPECTED = bytes(range(256)) * 4096


def _outcome(read) -> str:
    try:
        data = read()
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
    return "ok" if data == EXPECTED else f"mismatch:{len(data)}"


def _read_path(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def read_loop(log_path: str, paths: list[str]) -> None:
    fds: dict[str, int] = {}
    state: dict[str, str] = {}
    reads, last_heartbeat = 0, 0.0
    with open(log_path, "a", buffering=1) as log:
        while True:
            now = time.monotonic()
            for path in paths:
                outcomes = {path: _outcome(lambda p=path: _read_path(p))}
                if path not in fds and outcomes[path] == "ok":
                    try:
                        fds[path] = os.open(path, os.O_RDONLY)
                    except OSError:
                        pass
                if path in fds:
                    fd = fds[path]
                    outcomes[f"fd:{path}"] = _outcome(lambda fd=fd: os.pread(fd, len(EXPECTED), 0))
                for key, outcome in outcomes.items():
                    if state.get(key) != outcome:
                        log.write(f"{now:.6f} {key} {outcome}\n")
                        state[key] = outcome
            reads += 1
            if now - last_heartbeat >= 5:
                log.write(f"{now:.6f} heartbeat {reads}\n")
                last_heartbeat = now
            time.sleep(0.02)


def _mounts() -> dict[int, str]:
    table = {}
    with open("/proc/self/mountinfo") as f:
        for line in f:
            left, _, right = line.partition(" - ")
            fields, tail = left.split(), right.split()
            if fields[4].startswith("/mnt/"):
                table[int(fields[0])] = f"{fields[4]} {tail[0]} {tail[1]}"
    return table


def mount_loop(log_path: str) -> None:
    with open(log_path, "a", buffering=1) as log, open("/proc/self/mountinfo") as watched:
        poller = select.poll()
        poller.register(watched, select.POLLPRI | select.POLLERR)
        before: dict[int, str] = {}
        while True:
            now = time.monotonic()
            after = _mounts()
            for mount_id in sorted(before.keys() - after.keys()):
                log.write(f"{now:.6f} umount {mount_id} {before[mount_id]}\n")
            for mount_id in sorted(after.keys() - before.keys()):
                log.write(f"{now:.6f} mount {mount_id} {after[mount_id]}\n")
            before = after
            poller.poll()
            watched.seek(0)
            watched.read()


def write_file(path: str, seconds: float, out: str) -> None:
    result: dict = {"path": path, "errors": [], "chunks": 0, "max_write_fsync_s": 0.0}
    start = time.monotonic()
    result["start_s"] = start
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    while time.monotonic() - start < seconds:
        chunk = bytes([result["chunks"] % 251]) * CHUNK
        t0 = time.monotonic()
        try:
            os.write(fd, chunk)
            os.fsync(fd)
            result["chunks"] += 1
        except OSError as exc:
            result["errors"].append([round(t0, 6), errno.errorcode.get(exc.errno, str(exc.errno))])
        result["max_write_fsync_s"] = max(result["max_write_fsync_s"], time.monotonic() - t0)
        time.sleep(0.01)  # about 6 MiB/s at most: the pool and the host disk stay small
    try:
        os.close(fd)
    except OSError as exc:
        result["errors"].append(["close", errno.errorcode.get(exc.errno, str(exc.errno))])
    result["end_s"] = time.monotonic()
    with open(path, "rb") as f:
        data = f.read()
    expected = b"".join(bytes([i % 251]) * CHUNK for i in range(result["chunks"]))
    result["bytes_written"] = len(expected)
    result["read_back_equal"] = data == expected
    with open(out, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    if command == "read":
        read_loop(args[0], args[1:])
    elif command == "mounts":
        mount_loop(args[0])
    elif command == "write":
        write_file(args[0], float(args[1]), args[2])
    else:
        raise SystemExit(f"unknown command {command!r}")
