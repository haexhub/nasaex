"""Checks S1.6 and S1.7 (task T046): renames and moves, and attributes that survive them.

S1.6, per mount variant (the baseline gates, `passthrough.io=rw` with `cache.files=auto-full`
is informational): the case places a file and a directory of every origin (archive only, also
a directory on two archive disks; pool only; a directory on the pool and an archive branch) and
two target directories (pool only, archive only), then the client renames each within its
parent and moves an archive file into the pool-only directory and a pool file into the
archive-only directory. Every rename must exit with status 0, and the client manifest after all
renames must equal the manifest before with each old name replaced by the new one: the
complete tree under the new name and nothing under the old. A rename error (EXDEV or any other)
or a partial result fails the case; no rename setting is configured, so none is recorded as
required unless the shakedown finds one.

S1.7: an archive-only and a pool-only file, each with a user xattr set on its branch, get a
user xattr through mount.cifs and the hidden and system DOS attributes through smbclient. The
attributes are read back after the set, after a rename, after a new client mount, through a
second SMB session and after the pool file was moved into the archive with the move primitives
of the mover. DOS attributes are read with smbclient allinfo only: the cifs pseudo-xattr showed
stale values in the probe of task T039. Scaffolding for the spikes, not product code."""

from collections.abc import Mapping

from s1.context import Lab, Variant, under
from s1.fixture import POOL

from testlib.fixtures import Entry, Fixture
from testlib.manifest import Manifest, compare, describe
from testlib.smb import DOS_HIDDEN, DOS_SYSTEM, CifsMount

SEED = "S1.6-7"
PLACED = (
    Entry("archive-file.txt", "arch0", size=1100),
    Entry("pool-file.txt", POOL, size=1200),
    Entry("archive-dir/x.txt", "arch0", size=1300),
    Entry("archive-dir/sub/y.txt", "arch1", size=1400),
    Entry("pool-dir/z.txt", POOL, size=1500),
    Entry("both-dir/p.txt", POOL, size=1600),
    Entry("both-dir/q.txt", "arch1", size=1700),
    Entry("moved-archive.txt", "arch1", size=1800),
    Entry("pool-target/keep.txt", POOL, size=1900),
    Entry("moved-pool.txt", POOL, size=2000),
    Entry("archive-target/keep.txt", "arch0", size=2100),
)
RENAMES = (
    ("archive-file.txt", "archive-file.renamed.txt"),
    ("pool-file.txt", "pool-file.renamed.txt"),
    ("archive-dir", "archive-dir.renamed"),
    ("pool-dir", "pool-dir.renamed"),
    ("both-dir", "both-dir.renamed"),
    ("moved-archive.txt", "pool-target/moved-archive.txt"),
    ("moved-pool.txt", "archive-target/moved-pool.txt"),
)

PRESET = ("user.preset", "from-branch")
ATTRIBUTED = {
    "archive.txt": Entry("s1.7/archive.txt", "arch0", size=1000, xattrs=(PRESET,)),
    "pool.txt": Entry("s1.7/pool.txt", POOL, size=1000, xattrs=(PRESET,)),
}
CLIENT_XATTR = "user.s1"
HIDDEN_SYSTEM = DOS_HIDDEN | DOS_SYSTEM


def renamed(manifest: Manifest, src: str, dst: str) -> Manifest:
    """The manifest after renaming `src` (a file, or a directory with everything below it)."""
    moved = {}
    for path, item in manifest.items():
        if path == src:
            moved[dst] = item
        elif path.startswith(src + "/"):
            moved[dst + path[len(src) :]] = item
        else:
            moved[path] = item
    return moved


def attribute_failures(
    step: str, observed: Mapping[str, dict], expected: Mapping[str, Mapping[str, str]]
) -> list[str]:
    failures = []
    for key, want in expected.items():
        got = observed.get(key, {"error": "not observed"})
        if "error" in got:
            failures.append(f"{step}: {key} could not be read: {got['error']}")
            continue
        for name, value in want.items():
            if got.get(name) != value:
                failures.append(f"{step}: {key} {name} is {got.get(name)!r}, expected {value!r}")
        dos = got.get("dos")
        if dos is None or dos & HIDDEN_SYSTEM != HIDDEN_SYSTEM:
            shown = dos if dos is None else hex(dos)
            failures.append(f"{step}: {key} DOS attributes {shown} lack hidden and system")
    return failures


def check_rename_move(lab: Lab, variant: Variant) -> None:
    prefix = f"s1.6/{variant.name}"
    lab.place(Fixture(SEED, under(prefix, PLACED)))
    with lab.variant(variant.overrides):
        before = lab.share.manifest(prefix)
        results = {}
        for src, dst in RENAMES:
            results[src] = lab.rename_through(lab.share, f"{prefix}/{src}", f"{prefix}/{dst}")
        after = lab.share.manifest(prefix)
        rename_policy = lab.effective("func.rename")
    expected = before
    for src, dst in RENAMES:
        expected = renamed(expected, src, dst)
    differences = compare(expected, after)
    failures = [
        f"rename of {src} failed: {out.strip()}" for src, (code, out) in results.items() if code
    ]
    if differences:
        failures.append(f"the tree after the renames differs: {describe(differences)}")
    lab.evidence(
        f"S1.6-{variant.name}",
        {
            "variant": variant.name,
            "gating": variant.gating,
            "overrides": dict(variant.overrides),
            "renames": {src: [dst, *results[src]] for src, dst in RENAMES},
            "effective_func_rename": rename_policy,
            "differences": describe(differences, limit=None),
        },
    )
    if variant.gating:
        lab.required_settings["rename"] = {
            "func.rename": rename_policy,
            "setting": "none beyond the baseline",
            "found_by": "S1.6",
        }
    assert not failures, "; ".join(failures)


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode(errors="replace")


def _observe(lab: Lab, mount: CifsMount, rels: Mapping[str, str]) -> dict:
    seen = {}
    for key, rel in rels.items():
        try:
            values: dict = {
                name: _text(mount.getxattr(rel, name)) for name in (CLIENT_XATTR, PRESET[0])
            }
            values["dos"] = lab.smb.dos_attributes(rel)
        except Exception as error:  # noqa: BLE001 - an unreadable attribute is an outcome
            values = {"error": f"{type(error).__name__}: {error}"}
        seen[key] = values
    return seen


def _set_attributes(lab: Lab, rels: Mapping[str, str]) -> list[str]:
    failures = []
    for key, rel in rels.items():
        try:
            lab.share.setxattr(rel, CLIENT_XATTR, key.removesuffix(".txt").encode())
            lab.smb.set_dos_attributes(rel, "+hs")
        except Exception as error:  # noqa: BLE001 - a refused attribute change is an outcome
            failures.append(f"set: {key}: {type(error).__name__}: {error}")
    return failures


def check_attributes_preserved(lab: Lab) -> None:
    lab.place(Fixture(SEED, tuple(ATTRIBUTED.values())))
    rels = {key: entry.path for key, entry in ATTRIBUTED.items()}
    expected = {
        key: {CLIENT_XATTR: key.removesuffix(".txt"), PRESET[0]: PRESET[1]} for key in ATTRIBUTED
    }
    failures = _set_attributes(lab, rels)
    steps = {"set": _observe(lab, lab.share, rels)}

    new_rels = {key: rel.replace(".txt", ".renamed.txt") for key, rel in rels.items()}
    for key, rel in rels.items():
        status, out = lab.rename_through(lab.share, rel, new_rels[key])
        if status:
            failures.append(f"rename of {key} failed: {out.strip()}")
    steps["rename"] = _observe(lab, lab.share, new_rels)
    lab.remount_clients()
    steps["remount"] = _observe(lab, lab.share, new_rels)
    steps["second session"] = _observe(lab, lab.second, new_rels)

    pool_rel = new_rels["pool.txt"]
    lab.server.succeed(lab.move_command(pool_rel))
    moved_to = lab.holders(pool_rel)
    steps["mover"] = _observe(lab, lab.second, new_rels)
    if POOL in moved_to or not moved_to:
        failures.append(f"the mover's move left the pool file on {moved_to}")

    for step, observed in steps.items():
        failures += attribute_failures(step, observed, expected)
    lab.evidence(
        "S1.7-attributes",
        {"steps": steps, "moved_to": moved_to, "expected": expected, "failures": failures},
    )
    assert not failures, "; ".join(failures)
