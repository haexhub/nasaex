# Shared part of the SnapRAID probe (task T057). snapraid.nix prepends this file to
# probe_snapraid.py, so the names defined here are globals of that script. It measures, it never
# writes a record. Scaffolding for the Phase 0 spikes, not product code.
#
# The array: the three archive disks of the `small` profile as SnapRAID data disks (the whole
# `data` subvolume of each, research D3), single parity in a file on the ext4 parity disk, content
# files on the system disk, the parity disk and the `content` subvolume of every archive disk.
# A disk "fails" by being unmounted and wiped (`wipefs -a`) in the running VM and is "replaced" by
# a new btrfs filesystem with the same label on the same blank device (`data` and `content`
# subvolumes, `branch` and `trash` as spike-mkfs makes them, optionally the `.snapshots`
# subvolume snapper needs). Files are seeded (testlib.fixtures via probes.snapraid_observe.FILL),
# so every expected hash is computed on the host from the seed.
import base64
import json
import os
import shlex
import time

from probes.snapraid_observe import (
    BLOCK,
    FILL,
    XOR,
    first_physical,
    parse_locate,
    snapper_userdata,
    summary,
)

from testlib import faults
from testlib.fixtures import Entry, content_sha256

STARTED = time.monotonic()
PHASES = os.environ.get("PROBE_PHASES", "alloc,kill,snap,headroom,headroom-sys").split(",")
SEED = "t057-snapraid-probe"
MIB = 1 << 20
ARCH = ["arch0", "arch1", "arch2"]
DATA = {s: f"/mnt/archive/{s}" for s in ARCH}
CONTENT = {s: f"/mnt/archive-content/{s}" for s in ARCH}
CONF, LOGS = "/root/snapraid.conf", "/root/logs"
RESULT: dict = {"environment": {}, "commands": [], "phases": {}, "disk_space_after_mkfs": {}}
EXPECTED: dict[str, str] = {}  # disk:relpath -> SHA-256 of the current version
_mtime = [1_700_000_000_000_000_000]


def run(command: str, timeout: int = 900) -> str:
    """A guest command that must succeed; distinct commands are recorded."""
    if command not in RESULT["commands"]:
        RESULT["commands"].append(command)
    return server.succeed(command, timeout=timeout)


def attempt(command: str, timeout: int = 900) -> tuple[int, str]:
    """A guest command whose exit status is part of the measurement (stderr merged)."""
    if command not in RESULT["commands"]:
        RESULT["commands"].append(command)
    return server.execute(f"{command} 2>&1", timeout=timeout)


def put(path: str, text: str) -> None:
    """Write a guest file in pieces: one argument may not exceed 128 KiB (MAX_ARG_STRLEN)."""
    raw = text.encode()
    server.succeed(f": > {path}")
    for start in range(0, len(raw), 48 * 1024):
        data = base64.b64encode(raw[start : start + 48 * 1024]).decode()
        server.succeed(f"echo {data} | base64 -d >> {path}")


def tail(text: str, lines: int = 12) -> list[str]:
    return text.strip().splitlines()[-lines:]


def note(phase: str, key: str, value) -> None:
    RESULT["phases"].setdefault(phase, {})[key] = value
    print(f"PROBE {phase}.{key}: {json.dumps(value)[:600]}")


def by_id(serial: str) -> str:
    return f"/dev/disk/by-id/virtio-{serial}"


def ensure_mounted(path: str) -> None:
    run(f"findmnt -n {path} >/dev/null || mount {path}")


# --- disks ---------------------------------------------------------------------------------


def fail_disk(serial: str) -> dict:
    """Unmount both mounts of an archive disk and wipe its signatures. snapperd keeps the
    subvolume of a loaded config open for 30 s (snapper server/snapperd.cc:54-55, 164-168), so a
    busy unmount is recorded and retried after stopping it."""
    out = {}
    for mount in (DATA[serial], CONTENT[serial]):
        rc, text = attempt(f"findmnt -n {mount} >/dev/null && umount {mount}")
        if rc != 0 and "busy" in text:
            out[f"umount {mount}"] = tail(text, 3)
            run("systemctl stop snapperd.service")
            run(f"umount {mount}")
    run(f"wipefs -a -q {by_id(serial)} && udevadm settle")
    return out


def new_disk(serial: str, snapshots: bool) -> None:
    """A new btrfs filesystem on the (blank) device of an archive disk, mounted like nodes.nix."""
    run(f"mkfs.btrfs -q -f -L {serial} {by_id(serial)} && udevadm settle")
    tmp = f"/mnt/new-{serial}"
    run(f"mkdir -p {tmp} && mount {by_id(serial)} {tmp}")
    run(f"btrfs subvolume create {tmp}/data && btrfs subvolume create {tmp}/content")
    run(f"umount {tmp}")
    ensure_mounted(DATA[serial])
    ensure_mounted(CONTENT[serial])
    run(f"mkdir -p {DATA[serial]}/branch {DATA[serial]}/trash")
    if snapshots:
        add_snapshots_subvolume(serial)
    if serial not in RESULT["disk_space_after_mkfs"]:
        run(f"sync -f {DATA[serial]}")
        RESULT["disk_space_after_mkfs"][serial] = {
            "df_avail_bytes": int(run(f"df --output=avail -B1 {DATA[serial]} | tail -n1")),
            "fi_df": run(f"btrfs filesystem df -b {DATA[serial]}").strip().splitlines(),
        }


def add_snapshots_subvolume(serial: str) -> None:
    """snapper needs `<SUBVOLUME>/.snapshots` as a btrfs subvolume owned by root and not
    world-writable (snapper/Btrfs.cc:265-298); the NixOS module does not create it
    (nixos/modules/services/misc/snapper.nix:47-55, 246-275)."""
    run(f"btrfs subvolume create {DATA[serial]}/.snapshots && chmod 750 {DATA[serial]}/.snapshots")


def fresh_array(snapshots: bool) -> None:
    for serial in ARCH:
        fail_disk(serial)
        new_disk(serial, snapshots)
    run("rm -f /mnt/parity/snapraid.* /var/lib/snapraid/snapraid.*")
    EXPECTED.clear()


def write_conf(
    path: str = CONF,
    disks: tuple[str, ...] = tuple(ARCH),
    name: str = "snapraid",
    archive_content: bool = True,
):
    """A SnapRAID config; `archive_content` False keeps the content copies on the system and the
    parity disk only (manual "content": at least parity levels + 1 copies,
    doc/snapraid.d:1107-1120)."""
    lines = [f"parity /mnt/parity/{name}.parity", f"content /var/lib/snapraid/{name}.content"]
    lines += [f"content /mnt/parity/{name}.content"]
    if archive_content:
        lines += [f"content {CONTENT[s]}/{name}.content" for s in disks]
    lines += [f"data {s} {DATA[s]}/" for s in disks]
    lines += ["exclude /.snapshots/"]
    put(path, "\n".join(lines) + "\n")
    return lines


# --- files -----------------------------------------------------------------------------------


def fill(files: list[tuple[str, str, str, int]]) -> dict[str, int | None]:
    """Write seeded files in the given order: (disk, relpath, key, size). Returns the physical
    start of each (filefrag, the FIEMAP value SnapRAID sorts by, cmdline/unix.c:1020-1040)."""
    spec = []
    for disk, rel, key, size in files:
        _mtime[0] += 1_000_000_007  # distinct mtimes with a non-zero sub-second part
        spec.append([f"{DATA[disk]}/{rel}", key, size, _mtime[0]])
        EXPECTED[f"{disk}:{rel}"] = content_sha256(SEED, Entry(key, "archive", size=size))
    put("/root/fill.json", json.dumps(spec))
    run(f"python3 -c {shlex.quote(FILL)} {SEED} /root/fill.json")
    return {f"{d}:{r}": physical(f"{DATA[d]}/{r}") for d, r, _, _ in files}


def physical(path: str) -> int | None:
    return first_physical(run(f"filefrag -e {shlex.quote(path)}"))


def remove(disk: str, rel: str, keep_copy: str | None = None) -> None:
    """Delete a file of a disk; `keep_copy` keeps a copy with its timestamps (`fix -i` matches
    a moved file by name, size and timestamp, manual "fix" and "-i, --import")."""
    if keep_copy:
        run(f"mkdir -p {os.path.dirname(keep_copy)} && cp -p {DATA[disk]}/{rel} {keep_copy}")
    run(f"rm {DATA[disk]}/{rel}")
    EXPECTED.pop(f"{disk}:{rel}", None)


def compare_disk(disk: str, expected: dict[str, str]) -> dict:
    """SHA-256 of every expected file of a disk against its expected value, and every file the
    disk holds now (sorted), read directly from the data subvolume."""
    out = {"bit_exact": [], "differs": [], "missing": []}
    for key, sha in sorted(expected.items()):
        rel = key.split(":", 1)[1]
        rc, text = attempt(f"sha256sum {DATA[disk]}/{rel}")
        if rc != 0:
            out["missing"].append(rel)
        else:
            out["bit_exact" if text.split()[0] == sha else "differs"].append(rel)
    listing = run(f"cd {DATA[disk]} && find branch -type f | sort")
    out["files_now"] = listing.strip().splitlines()
    return out


def disk_expected(disk: str) -> dict[str, str]:
    return {k: v for k, v in EXPECTED.items() if k.startswith(f"{disk}:")}


# --- SnapRAID --------------------------------------------------------------------------------


def snapraid(name: str, args: str, conf: str = CONF, tool: str | None = None) -> dict:
    """One SnapRAID command (`tool` replaces `snapraid -c <conf>`, e.g. a snapraid-btrfs prefix,
    which passes options after the command through) with its log; the result keeps the exit
    status, the output, the summary tags and the full log text (for the caller to reduce)."""
    log = f"{LOGS}/{name}.log"
    started = time.monotonic()
    rc, out = attempt(f"{tool or f'snapraid -c {conf}'} {args} -l {log}")
    seconds = round(time.monotonic() - started, 2)
    log_text = server.execute(f"cat {log}")[1]
    return {"rc": rc, "seconds": seconds, "out": out, "log": log_text, "summary": summary(log_text)}


def brief(result: dict, lines: int = 8) -> dict:
    keys = ("exit", "block_unsynced", "added", "removed", "updated", "equal", "file_count")
    return {
        "rc": result["rc"],
        "seconds": result["seconds"],
        "summary": {k: v for k, v in result["summary"].items() if k in keys},
        "out_tail": tail(result["out"], lines),
    }


def locate(conf: str = CONF) -> dict[str, tuple[int, int, int]]:
    """Per disk:path (first, last, fragments) from `snapraid locate` (cmdline/locate.c:123-180)."""
    return parse_locate(run(f"snapraid -c {conf} --test-fmt disk locate"))


def xor_check(maps: dict[str, dict[int, list[tuple[str, int]]]], parity: str) -> dict:
    """Run the XOR guest script for the given position maps against the parity file."""
    spec = {
        "block_size": BLOCK,
        "parity": parity,
        "maps": {n: {str(p): m for p, m in pos.items()} for n, pos in maps.items()},
    }
    put("/root/xor.json", json.dumps(spec))
    return json.loads(run(f"python3 -c {shlex.quote(XOR)} /root/xor.json", timeout=1800))


def position_map(layout: dict[str, list[int]], paths: dict[str, str], top: int) -> dict:
    """Parity position -> [(file path, file block index)] for positions 0..top-1."""
    out: dict[int, list[tuple[str, int]]] = {p: [] for p in range(top)}
    for key, positions in layout.items():
        for index, pos in enumerate(positions):
            out.setdefault(pos, []).append((paths[key], index))
    return out


def live_paths(keys) -> dict[str, str]:
    return {k: f"{DATA[k.split(':', 1)[0]]}/{k.split(':', 1)[1]}" for k in keys}


def in_background(name: str, command: str) -> None:
    """Start a command detached from the driver's shell; its exit status lands in <name>.rc."""
    script = f"{command} > {LOGS}/{name}.out 2>&1; echo $? > {LOGS}/{name}.rc"
    # The detached shell must not hold the driver's output open, or the driver waits for it.
    run(f"setsid -f bash -c {shlex.quote(script)} </dev/null >/dev/null 2>&1")


# The nixpkgs `snapraid` is a makeWrapper script that execs `.snapraid-wrapped` with argv[0] set
# to the script's path, so the process name is `.snapraid-wrapp` and only the command line
# (".../bin/snapraid <args>") identifies it; the shells around it start with "bash".
SNAPRAID_PROCESS = faults.SNAPRAID_PROCESS


def kill_at_position(name: str, threshold: int) -> dict:
    """Wait until the -l log of a running sync shows `run:pos:<block>` with block >= threshold
    (polled every 0.2 s), then SIGKILL the SnapRAID process (fault kind kill-sync-at-position).
    Returns what the poll saw, the process list and the last line before the kill."""
    log = f"{LOGS}/{name}.log"
    seen, started = [], time.monotonic()
    while time.monotonic() - started < 600:
        text = server.execute(f"grep '^run:pos:' {log} | tail -n 1")[1].strip()
        if text and (not seen or seen[-1][1] != text):
            seen.append([round(time.monotonic() - started, 2), text])
        if text and int(text.split(":")[2]) >= threshold:
            processes = run("ps -eo pid,comm,args | grep -i snapraid | grep -v grep")
            event = faults.kill(server, SNAPRAID_PROCESS, point=f"sync:pos:{threshold}")
            return {
                "fault": [event.kind, event.point],
                "threshold": threshold,
                "killed_after_line": text,
                "poll": seen,
                "processes_at_kill": processes.strip().splitlines(),
            }
        time.sleep(0.2)
    raise AssertionError(f"no run:pos >= {threshold} in {log}")


def snapper_list(serial: str) -> list:
    rc, out = attempt(f"snapper --jsonout -c {serial} list --disable-used-space")
    if rc != 0:
        return [f"rc={rc}", *tail(out, 4)]
    return snapper_userdata(out[out.index("{") :], "snapraid-btrfs")


def settle_jobs() -> None:
    server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
