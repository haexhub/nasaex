"""The S2 history (task T065): a fresh array per scenario and what happens to it before a disk
fails, with the bookkeeping the parity model needs (testlib.parity_model).

  reset     a fresh array: the machine is powered off (QEMU process termination) and every disk
            image is replaced by an empty one (faults.device_failed_at_boot), so each scenario
            fails a disk by a real host-side fault on a machine that saw no earlier one; then
            boot, spike-mkfs, the mounts, both pools and the snapper subvolumes
  populate  seeded files written straight into the branch directory of an archive disk: the
            data set before the first sync, and files written to a disk after a sync (S2.3)
  add       a new file the way new data reaches the archive: created through the share (it
            lands on the pool branch), then moved by the mover primitives of
            guest/mover-effects.sh through the base pool (T030), never written into the archive
            through the share; refused unless it lands on a surviving disk (`not_on`)
  modify    bytes of an archived file inverted in place through the share (same inode)
  delete    an archived file deleted through the share
  sync      a plain or a snapshot-supported sync; on exit 0 the pending changes are folded into
            the synced layout by the model's allocation, with each file's physical start
            measured before the sync
  fail      a data disk fails by a host-side fault (device-ioerror or device-unplug)
  replace   steps 1 to 3 of the recovery sequence (guest/recover.sh): detect, lock, and replace
            by an unused spare of the same, a larger or a smaller size (nix/profiles.nix)

The share of S2 is the cache pool mounted on the `server` itself (maintainer decision of
2026-10-22: S2 has no SMB client; the in-place block changes are those S1 measured through SMB).
A file is named by its path in the share (`rel`); SnapRAID names it `branch/<rel>`, because its
data root is the data subvolume (task T028a). Paths are never reused within one history.

The `machine` argument is a driver machine object (duck-typed). Scaffolding for the spikes, not
product code."""

import base64
import json
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from probes.snapraid_observe import BLOCK, FILL, extent, first_physical, parse_locate

from testlib import faults
from testlib.fixtures import Entry, flipped_sha256
from testlib.parity_model import NewFile, allocate

SEED = "S2"
ARCH = ("arch0", "arch1", "arch2")
DATA = {s: f"/mnt/archive/{s}" for s in ARCH}
CONTENT = {s: f"/mnt/archive-content/{s}" for s in ARCH}
PARITY = "/mnt/parity"
POOL_BRANCH = "/mnt/pool"
BASE = "/mnt/base"
CACHE = "/mnt/cache"
UNITS = ("mnt-base.mount", "mnt-cache.mount")
LOGS = "/root/s2-logs"
MIB = 1 << 20
FIRST_MTIME_NS = 1_700_000_000_000_000_007
FAULTS = {"device-ioerror": faults.device_ioerror, "device-unplug": faults.device_unplug}

_FLIP = """import os, sys
with open(sys.argv[1], "r+b") as handle:
    for offset in map(int, sys.argv[2:]):
        handle.seek(offset)
        byte = handle.read(1)[0]
        handle.seek(offset)
        handle.write(bytes([byte ^ 0xFF]))
    handle.flush()
    os.fsync(handle.fileno())"""
_HASHES = """import hashlib, json, os, sys
out = {}
for rel in sys.argv[2:]:
    path = os.path.join(sys.argv[1], rel)
    try:
        with open(path, "rb") as handle:
            out[rel] = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        out[rel] = None
print(json.dumps(out))"""


def snapraid_path(rel: str) -> str:
    return f"branch/{rel}"


@dataclass
class File:
    disk: str
    size: int
    flips: list[int] = field(default_factory=list)  # every offset inverted so far, in order

    def sha256(self, rel: str) -> str:
        return flipped_sha256(SEED, Entry(rel, "archive", size=self.size), self.flips)


class History:
    def __init__(self, machine, topology: Mapping, qemu_img: str):
        self.machine = machine
        self.disks = {d["serial"]: d for d in topology["disks"]}
        self.qemu_img = qemu_img
        self._clear()

    def _clear(self) -> None:
        self.files: dict[str, File] = {}  # rel -> the live file
        self.synced: dict[str, dict[str, tuple[int, ...]]] = {s: {} for s in ARCH}
        self.synced_sha: dict[str, dict[str, str]] = {s: {} for s in ARCH}  # disk -> rel -> sha
        self.attempted: dict[str, dict[str, tuple[int, ...]]] = {}  # layout of the last sync run
        self._removed: dict[str, set[str]] = {s: set() for s in ARCH}  # synced, since changed
        self._added: dict[str, set[str]] = {s: set() for s in ARCH}  # to insert at the next sync
        self._used: set[str] = set()
        self._spares: set[str] = set()
        self._mtime = FIRST_MTIME_NS
        self._syncs = 0
        self.failed: list[str] = []

    # --- the array ------------------------------------------------------------------------

    def reset(self) -> None:
        machine = self.machine
        if machine.booted:
            machine.crash()
        for disk in self.disks.values():
            faults.device_failed_at_boot(machine, disk, self.qemu_img)
        machine.start()
        machine.wait_for_unit("multi-user.target")
        # Mounts are nofail with a device timeout: on a boot with blank disks their jobs are
        # still pending at multi-user.target (vm-tests/probes/probe_faults.py).
        machine.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
        machine.succeed("spike-mkfs all")
        for mount in [POOL_BRANCH, *DATA.values(), *CONTENT.values(), PARITY]:
            machine.succeed(f"findmnt -n {mount} >/dev/null || mount {mount}")
        machine.succeed(f"systemctl start {' '.join(UNITS)}")
        # The system disk keeps its content across the power-off.
        machine.succeed(f"rm -f /var/lib/snapraid/snapraid.* && mkdir -p {LOGS}")
        machine.succeed("spike-snapshots-init " + " ".join(ARCH))
        self._clear()

    # --- what happens before a failure ------------------------------------------------------

    def populate(self, files: Iterable[tuple[str, str, int]]) -> None:
        """(disk, rel, size) per file, written in this order into the disk's branch."""
        spec = []
        for disk, rel, size in files:
            self._unused(rel)
            spec.append([f"{DATA[disk]}/branch/{rel}", rel, size, self._next_mtime()])
            self._new(rel, disk, size)
        self._fill(spec)

    def add(self, rel: str, size: int, not_on: str | None = None) -> str:
        """Create `rel` through the share and move it the mover's way; the disk it landed on."""
        self._unused(rel)
        self._fill([[f"{CACHE}/{rel}", rel, size, self._next_mtime()]])
        pool = shlex.quote(f"{POOL_BRANCH}/{rel}")
        self.machine.succeed(f"test -f {pool}")  # the cache pool creates on the pool branch
        self.machine.succeed(
            f"mover-effects mover_copy_item {pool} {shlex.quote(f'{BASE}/{rel}')} 0"
            f" && mover-effects mover_unlink_pool_copy {pool} 0"
        )
        holders = [d for d in ARCH if self._exists(f"{DATA[d]}/branch/{rel}")]
        if len(holders) != 1:
            raise RuntimeError(f"{rel}: expected on one archive disk, found on {holders}")
        self._new(rel, holders[0], size)
        if holders[0] == not_on:
            raise RuntimeError(f"{rel} landed on {not_on}, the disk that is to fail")
        return holders[0]

    def modify(self, rel: str, offsets: Iterable[int]) -> None:
        """Invert the byte at each offset of `rel` in place through the share."""
        file, offsets = self._live(rel), list(offsets)
        before, after = (
            file.sha256(rel),
            flipped_sha256(SEED, Entry(rel, "archive", size=file.size), [*file.flips, *offsets]),
        )
        if after == before:
            raise ValueError(f"{rel}: these offsets would restore its content")
        archived = shlex.quote(f"{DATA[file.disk]}/branch/{rel}")
        inode = self.machine.succeed(f"stat -c %i {archived}").strip()
        self.machine.succeed(
            f"python3 -c {shlex.quote(_FLIP)} {shlex.quote(f'{CACHE}/{rel}')} "
            + " ".join(map(str, offsets))
        )
        if self.machine.succeed(f"stat -c %i {archived}").strip() != inode:
            raise RuntimeError(f"{rel}: the change through the share was not made in place")
        if self._hashes(file.disk, [rel])[rel] != after:
            raise RuntimeError(f"{rel}: the archived content is not the expected change")
        file.flips += offsets
        if rel in self.synced_sha[file.disk]:
            self._removed[file.disk].add(rel)
        self._added[file.disk].add(rel)

    def delete(self, rel: str) -> None:
        file = self._live(rel)
        self.machine.succeed(f"rm -- {shlex.quote(f'{CACHE}/{rel}')}")
        if self._exists(f"{DATA[file.disk]}/branch/{rel}"):
            raise RuntimeError(f"{rel}: still on {file.disk} after the delete through the share")
        del self.files[rel]
        if rel in self.synced_sha[file.disk]:
            self._removed[file.disk].add(rel)
        self._added[file.disk].discard(rel)

    def sync(self, snapshots: bool) -> tuple[int, str, str]:
        """(exit status, output, log path) of one sync; `attempted` is the layout it records."""
        physical = {
            disk: {rel: self._physical(disk, rel) for rel in sorted(self._added[disk])}
            for disk in ARCH
        }
        self.attempted = {
            disk: allocate(
                self.synced[disk],
                {snapraid_path(rel) for rel in self._removed[disk]},
                [
                    NewFile(snapraid_path(rel), self.files[rel].size, start)
                    for rel, start in physical[disk].items()
                ],
                BLOCK,
            )
            for disk in ARCH
        }
        self._syncs += 1
        log = f"{LOGS}/sync-{self._syncs}.log"
        command = f"snapsync sync --log {log}" if snapshots else f"snapraid -l {log} sync"
        rc, out = self.machine.execute(f"{command} 2>&1", timeout=900)
        if rc == 0:
            self.synced = self.attempted
            for disk in ARCH:
                self.synced_sha[disk] = {
                    rel: f.sha256(rel) for rel, f in self.files.items() if f.disk == disk
                }
                self._removed[disk].clear()
                self._added[disk].clear()
        return rc, out, log

    # --- failure and replacement --------------------------------------------------------------

    def fail(self, serial: str, kind: str = "device-ioerror") -> faults.FaultEvent:
        if kind not in FAULTS:
            raise ValueError(f"{kind}: a disk fails by {' or '.join(FAULTS)}")
        event = FAULTS[kind](self.machine, serial)
        self.failed.append(serial)
        return event

    def spare(self, serial: str, relation: str = "same") -> dict:
        """The unused spare of the same size, the smallest larger or the largest smaller one."""
        size = self.disks[serial]["sizeMiB"]
        spares = sorted(
            (d for d in self.disks.values() if d["role"] == "replacement"),
            key=lambda d: (d["sizeMiB"], d["serial"]),
        )
        match = {
            "same": [d for d in spares if d["sizeMiB"] == size],
            "larger": [d for d in spares if d["sizeMiB"] > size],
            "smaller": [d for d in reversed(spares) if d["sizeMiB"] < size],
        }
        if relation not in match:
            raise ValueError(f"{relation}: a spare is of the same, a larger or a smaller size")
        unused = [d for d in match[relation] if d["serial"] not in self._spares]
        if not unused:
            raise LookupError(f"no unused spare of a {relation} size for {serial}")
        return unused[0]

    def replace(self, serial: str, relation: str = "same") -> str:
        """Steps 1 to 3 of guest/recover.sh; the device of the spare that replaced `serial`."""
        spare = self.spare(serial, relation)
        device = faults.by_id(spare["serial"])
        size = self.disks[serial]["sizeMiB"] * MIB
        for step in (f"detect {serial}", f"lock {serial}", f"replace {serial} {device} {size}"):
            self.machine.succeed(f"recover {step}")
        self._spares.add(spare["serial"])
        return device

    # --- what the model and the checks read ---------------------------------------------------

    @property
    def changed(self) -> dict[str, set[str]]:
        """Per disk, the synced files changed in place or deleted since the last sync."""
        return {d: {snapraid_path(rel) for rel in self._removed[d]} for d in ARCH}

    @property
    def unsynced(self) -> dict[str, set[str]]:
        """Per disk, the files written or changed on it since the last sync."""
        return {d: {snapraid_path(rel) for rel in self._added[d]} for d in ARCH}

    def compare(self, disk: str) -> dict[str, list[str]]:
        """Each file of `disk` as of the last sync against its synced version, read from the
        data subvolume: bit_exact, differs (also when renamed *.unrecoverable) or missing."""
        expected = self.synced_sha[disk]
        paths = sorted((*expected, *(f"{rel}.unrecoverable" for rel in expected)))
        found = self._hashes(disk, paths)
        out: dict[str, list[str]] = {"bit_exact": [], "differs": [], "missing": []}
        for rel, sha in sorted(expected.items()):
            if found[rel] is None:
                renamed = f"{rel}.unrecoverable"
                category = (
                    "differs"
                    if renamed not in expected and found[renamed] is not None
                    else "missing"
                )
                out[category].append(rel)
            else:
                out["bit_exact" if found[rel] == sha else "differs"].append(rel)
        return out

    def layout_differences(self) -> list[str]:
        """Where `snapraid locate` disagrees with the synced layout of the model: the first and
        last position and the fragment count of every file (task T075 checks the model)."""
        seen = parse_locate(self.machine.succeed("snapraid --test-fmt disk locate"))
        wanted = {
            f"{disk}:{path}": extent(list(positions))
            for disk, files in self.synced.items()
            for path, positions in files.items()
            if positions
        }
        return [
            f"{key}: model {wanted.get(key)}, locate {seen.get(key)}"
            for key in sorted(wanted.keys() | seen.keys())
            if wanted.get(key) != seen.get(key)
        ]

    # --- helpers --------------------------------------------------------------------------------

    def _unused(self, rel: str) -> None:
        if rel in self._used:
            raise ValueError(f"{rel}: a path is never reused within one history")

    def _new(self, rel: str, disk: str, size: int) -> None:
        self._used.add(rel)
        self.files[rel] = File(disk, size)
        self._added[disk].add(rel)

    def _live(self, rel: str) -> File:
        if rel not in self.files:
            raise KeyError(f"{rel}: no live file of this history")
        return self.files[rel]

    def _next_mtime(self) -> int:
        self._mtime += 1_000_000_007  # distinct mtimes with a non-zero sub-second part
        return self._mtime

    def _exists(self, path: str) -> bool:
        return self.machine.execute(f"test -f {shlex.quote(path)}")[0] == 0

    def _physical(self, disk: str, rel: str) -> int:
        if self.files[rel].size == 0:
            return 0  # no block, no position
        path = shlex.quote(f"{DATA[disk]}/branch/{rel}")
        start = first_physical(self.machine.succeed(f"filefrag -e {path}"))
        if start is None:
            raise RuntimeError(f"{rel}: filefrag reports no extent")
        return start

    def _hashes(self, disk: str, rels: list[str]) -> dict[str, str | None]:
        args = " ".join(shlex.quote(r) for r in rels)
        root = shlex.quote(f"{DATA[disk]}/branch")
        return json.loads(self.machine.succeed(f"python3 -c {shlex.quote(_HASHES)} {root} {args}"))

    def _fill(self, spec: list) -> None:
        """Run FILL (probes.snapraid_observe) with the spec in a guest file, in pieces of 48 KiB
        (one argument may not exceed 128 KiB, MAX_ARG_STRLEN)."""
        raw, target = json.dumps(spec).encode(), "/root/s2-fill.json"
        self.machine.succeed(f": > {target}")
        for start in range(0, len(raw), 48 * 1024):
            data = base64.b64encode(raw[start : start + 48 * 1024]).decode()
            self.machine.succeed(f"echo {data} | base64 -d >> {target}")
        self.machine.succeed(f"python3 -c {shlex.quote(FILL)} {SEED} {target}")
