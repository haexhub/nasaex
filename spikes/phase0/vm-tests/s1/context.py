"""The S1 lab (task T041): the `server` and `client` of the S1 VM test as the checks drive them.

Server side: the branch directories of the S1 data set (`pool`, the archive branches and the
trash directories, keyed like vm-tests/s1/fixture.py), read and written directly, never through
a pool; the cache pool and its mount variants. Client side: two mount.cifs mounts of the share
(the default one and a second SMB session with `nosharesock,actimeo=0`, plus a short-lived
session of its own on demand, `fresh_session`) and smbclient
(testlib.smb). Evidence goes to status/ of the driver's output directory, which the evidence
archive keeps (testlib.evidence).

A mount variant remounts the cache pool by hand with the options of its systemd mount unit,
changed by the variant, and restores the unit afterwards; the clients and smbd let go of the
cache pool first, or the unmount would be refused as busy.

The `server` and `client` arguments are driver machine objects (duck-typed). Scaffolding for
the spikes, not product code."""

import base64
import hashlib
import json
import shlex
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from s1.fixture import ARCHIVE, POOL

from testlib.fixtures import Entry, Fixture, to_json
from testlib.manifest import Manifest, from_json, tree_hash
from testlib.smb import CifsMount, Share, SmbClient

CACHE = "/mnt/cache"
BASE = "/mnt/base"
FRESH_MOUNT = "/mnt/share3"
CACHE_UNIT = "mnt-cache.mount"
BASE_UNIT = "mnt-base.mount"
SMBD = "samba-smbd.service"
BASELINE_ARCHIVE_MODE = "NC"
EVIDENCE_DIR = Path("status")

_MANIFEST = """import sys
from pathlib import Path
from testlib.manifest import build_manifest, to_json
print(to_json(build_manifest(Path(sys.argv[1]))))"""
_HOLDERS = """import json, os, sys
roots, rel = json.loads(sys.argv[1]), sys.argv[2]
print(json.dumps([k for k, r in roots.items() if os.path.lexists(os.path.join(r, rel))]))"""
_POPULATE = """import base64, json, sys
from pathlib import Path
from testlib.fixtures import from_json, populate
fixture = from_json(base64.b64decode(sys.argv[1]).decode())
populate(fixture, {k: Path(v) for k, v in json.loads(sys.argv[2]).items()})"""
_WRITE = """import base64, sys
from testlib.fixtures import content_chunks, from_json
fixture = from_json(base64.b64decode(sys.argv[1]).decode())
with open(sys.argv[2], "wb") as handle:
    for chunk in content_chunks(fixture.seed, fixture.entries[0]):
        handle.write(chunk)"""
_FLIP = """import sys
with open(sys.argv[1], "r+b") as handle:
    for offset in map(int, sys.argv[2:]):
        handle.seek(offset)
        byte = handle.read(1)[0]
        handle.seek(offset)
        handle.write(bytes([byte ^ 0xFF]))"""
_GETXATTR = """import os, sys
print(os.getxattr(sys.argv[1], sys.argv[2]).decode())"""


@dataclass(frozen=True)
class Variant:
    """One case of a mount-variants scenario: its name, whether it gates, and the cache-pool
    options it changes against the baseline (criteria tables.mount_variants)."""

    name: str
    gating: bool
    overrides: Mapping[str, str]


def _split(options: str) -> list[tuple[str, str | None]]:
    pairs = []
    for option in options.split(","):
        key, sep, value = option.partition("=")
        pairs.append((key, value if sep else None))
    return pairs


def variant_options(configured: str, overrides: Mapping[str, str]) -> str:
    """The configured mount options with each override replacing the value of its option, or
    appended when the mount does not set it; order and flags without a value are kept."""
    pairs = _split(configured)
    keys = [key for key, _ in pairs]
    pairs = [(key, overrides.get(key, value)) for key, value in pairs]
    pairs += [(key, value) for key, value in overrides.items() if key not in keys]
    return ",".join(key if value is None else f"{key}={value}" for key, value in pairs)


def branches_with_mode(what: str, mode: str | None) -> str:
    """The branch list of the cache pool with `mode` on every archive branch (None: no mode);
    the pool branch comes first and is never changed."""
    pool, *archive = what.split(":")
    plain = [branch.partition("=")[0] for branch in archive]
    return ":".join([pool, *(b if mode is None else f"{b}={mode}" for b in plain)])


def _bytes(size: str) -> int:
    """A mergerfs size (a number with an optional K, M, G or T suffix, powers of 1024)."""
    power = "KMGT".find(size[-1].upper()) + 1
    return int(size[:-1]) * 1024**power if power else int(size)


def running_baseline(
    baseline: Mapping[str, str], effective: Callable[[str], str]
) -> tuple[dict[str, str], list[str]]:
    """What the running cache pool reports for every option of the criteria's baseline table
    (`effective` reads one option), the archive branch mode from its branch list, and every
    difference from the table."""
    seen = {key: effective(key) for key in baseline if key != "archive_branch_mode"}
    seen["branches"] = effective("branches")
    differences = []
    for key, expected in baseline.items():
        if key == "archive_branch_mode":
            for branch in seen["branches"].split(":")[1:]:  # the pool branch is first
                path, _, mode = branch.partition("=")
                mode = mode.partition(",")[0]  # a branch's own minfreespace follows a comma
                if mode != expected:
                    differences.append(
                        f"archive branch {path} has mode {mode}, the criteria say {expected}"
                    )
        elif key == "minfreespace":
            if int(seen[key]) != _bytes(expected):
                differences.append(
                    f"minfreespace is {seen[key]},"
                    f" the criteria say {expected} ({_bytes(expected)} bytes)"
                )
        elif seen[key] != expected:
            differences.append(f"{key} is {seen[key]}, the criteria say {expected}")
    return seen, differences


def under(prefix: str, entries: tuple[Entry, ...]) -> tuple[Entry, ...]:
    """The entries moved below `prefix`, so that each scenario (and case) owns its directory."""
    return tuple(
        replace(e, path=f"{prefix}/{e.path}", link_to=e.link_to and f"{prefix}/{e.link_to}")
        for e in entries
    )


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class Lab:
    def __init__(
        self,
        server,
        client,
        roots: Mapping[str, str],
        recorder,
        *,
        user: str | None = "spike",
        password: str = "spike",
        share_name: str = "cache",
        tables: Mapping | None = None,
    ):
        self.server = server
        self.client = client
        self.roots = dict(roots)
        self.recorder = recorder
        self.user = user
        self.password = password
        share = Share("server", share_name, user or "", password)
        self.share = CifsMount(client, share, "/mnt/share")
        self.second = CifsMount(client, share, "/mnt/share2", ("nosharesock", "actimeo=0"))
        self.smb = SmbClient(client, share)
        self.tables = tables or {}  # the criteria tables the checks read
        self.required_settings: dict = {}
        # In-place bit flips applied so far, per path: the expected content of a fixture file
        # that several cases change (S1.8) follows from all of them.
        self.flipped: dict[str, tuple[int, ...]] = {}

    @property
    def share_branches(self) -> dict[str, str]:
        return {k: v for k, v in self.roots.items() if k in (POOL, *ARCHIVE)}

    # --- server: branches, read and written directly ---------------------------------------

    @staticmethod
    def python(machine, code: str, *args: str) -> str:
        quoted = " ".join(shlex.quote(a) for a in args)
        return machine.succeed(f"python3 -c {shlex.quote(code)} {quoted}")

    def path(self, key: str, rel: str = "") -> str:
        return f"{self.roots[key]}/{rel}" if rel else self.roots[key]

    def manifest(self, key: str, rel: str = "") -> Manifest:
        """Manifest of a branch (or a path below it) read on the server; empty if absent."""
        return from_json(self.python(self.server, _MANIFEST, self.path(key, rel)))

    def tree_hashes(self, keys=ARCHIVE) -> dict[str, str]:
        return {key: tree_hash(self.manifest(key)) for key in keys}

    def holders(self, rel: str) -> list[str]:
        """The share branches (pool and archive, never a trash) on which `rel` exists."""
        return json.loads(self.python(self.server, _HOLDERS, json.dumps(self.share_branches), rel))

    def place(self, fixture: Fixture) -> None:
        """Populate a fixture on the branches as root, then hand the share branches to the
        share user, so that the client may change what the fixture placed."""
        self.python(self.server, _POPULATE, _b64(to_json(fixture)), json.dumps(self.roots))
        if self.user is not None:
            branches = " ".join(shlex.quote(r) for r in self.share_branches.values())
            self.server.succeed(f"chown -R {self.user}: {branches}")

    def remove(self, key: str, rel: str) -> None:
        self.server.succeed(f"rm -rf -- {shlex.quote(self.path(key, rel))}")

    def sha256(self, key: str, rel: str) -> str:
        return self.server.succeed(f"sha256sum < {shlex.quote(self.path(key, rel))}").split()[0]

    def inode(self, key: str, rel: str) -> int:
        return int(self.server.succeed(f"stat -c %i {shlex.quote(self.path(key, rel))}"))

    def move_command(self, rel: str, first: int = 0) -> str:
        """Shell command that moves a pool file the mover's way with the move primitives of
        guest/mover-effects.sh: copy through the base pool (M1), then unlink the pool copy (M5);
        with `first` = 1 their hooks fire (contracts/mover-steps.md)."""
        source, target = shlex.quote(self.path(POOL, rel)), shlex.quote(f"{BASE}/{rel}")
        return (
            f"mover-effects mover_copy_item {source} {target} {first}"
            f" && mover-effects mover_unlink_pool_copy {source} {first}"
        )

    def effective(self, option: str) -> str:
        """The value of a mergerfs option of the running cache pool (runtime xattr)."""
        value = self.python(self.server, _GETXATTR, f"{CACHE}/.mergerfs", f"user.mergerfs.{option}")
        return value.strip()

    # --- client ------------------------------------------------------------------------------

    def mount_clients(self) -> None:
        self.share.mount()
        self.second.mount()

    def umount_clients(self) -> None:
        for mount in (self.share, self.second):
            self.client.succeed(f"umount {shlex.quote(mount.mountpoint)} || true")

    def remount_clients(self) -> None:
        self.umount_clients()
        self.mount_clients()

    @contextmanager
    def fresh_session(self) -> Iterator[CifsMount]:
        """The share mounted by a mount.cifs session of its own (`nosharesock`: its own
        superblock, so no size or handle of another mount of the client is reused) for the
        duration, unmounted afterwards even when the block raises."""
        session = CifsMount(self.client, self.share.share, FRESH_MOUNT, ("nosharesock",))
        session.mount()
        try:
            yield session
        finally:
            session.umount()

    def client_run(self, command: str) -> tuple[int, str]:
        """Run a client command whose failure is an outcome, not a harness error: exit status
        and combined output."""
        return self.client.execute(f"{command} 2>&1")

    def rename_through(self, mount: CifsMount, src: str, dst: str) -> tuple[int, str]:
        """Rename or move through a client mount (`mv -T`); exit status and output."""
        source, target = shlex.quote(mount.path(src)), shlex.quote(mount.path(dst))
        return self.client_run(f"mv -T -- {source} {target}")

    def flip_through(self, mount: CifsMount, rel: str, offsets) -> None:
        """Invert the bits of the byte at each offset in place, through a client mount."""
        self.python(self.client, _FLIP, mount.path(rel), *map(str, offsets))

    def write_through(self, mount: CifsMount, rel: str, seed: str, entry: Entry) -> None:
        """Write the seeded content of `entry` to `rel` through a client mount."""
        fixture = Fixture(seed, (entry,))
        self.python(self.client, _WRITE, _b64(to_json(fixture)), mount.path(rel))

    # --- cache pool variants -------------------------------------------------------------------

    def cache_unit(self, field: str) -> str:
        """A field of the cache pool's systemd mount unit as configured (What, Options)."""
        return self.server.succeed(f"systemctl cat {CACHE_UNIT} | sed -n 's/^{field}=//p'").strip()

    @contextmanager
    def variant(
        self, overrides: Mapping[str, str], archive_mode: str | None = BASELINE_ARCHIVE_MODE
    ) -> Iterator[None]:
        """The cache pool mounted with `overrides` and `archive_mode` for the duration; the
        baseline (no override, NC) leaves the mounts alone."""
        if not overrides and archive_mode == BASELINE_ARCHIVE_MODE:
            yield
            return
        what = branches_with_mode(self.cache_unit("What"), archive_mode)
        options = variant_options(self.cache_unit("Options"), overrides)
        self._release_cache_pool()
        self.server.succeed(f"systemctl stop {CACHE_UNIT}")
        try:  # a variant that fails to mount still gives the next scenario the baseline back
            self.server.succeed(f"mergerfs -o {shlex.quote(options)} {shlex.quote(what)} {CACHE}")
            self._serve_cache_pool()
            yield
        finally:
            self._release_cache_pool()
            self.server.succeed(f"umount {CACHE} || true")
            self.server.succeed(f"systemctl start {CACHE_UNIT}")
            self._serve_cache_pool()

    def _release_cache_pool(self) -> None:
        self.umount_clients()
        self.server.succeed(f"systemctl stop {SMBD}")

    def _serve_cache_pool(self) -> None:
        self.server.succeed(f"systemctl start {SMBD}")
        self.mount_clients()

    # --- evidence --------------------------------------------------------------------------------

    def evidence(self, name: str, data) -> None:
        """Write status/<name>.json and reference it from the running scenario."""
        EVIDENCE_DIR.mkdir(exist_ok=True)
        path = EVIDENCE_DIR / f"{name}.json"
        text = json.dumps(data, indent=2, sort_keys=True, default=str, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        self.recorder.attach(path.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
