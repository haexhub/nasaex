"""Checks S1.1 and S1.11 (task T043): the archive as the share shows it, and the same view after
a reboot.

S1.1 (amended by task T043a, spec Clarifications 2026-10-18) compares every path of the archive
branches with the reference manifest derived from the S1 data set. Through the mount.cifs mount
it judges type, size, mtime in whole seconds and SHA-256; smbclient, the second client
implementation, checksums the archive files, and `smbclient allinfo` must show the directory
bit exactly on directories and none of readonly, hidden and system. Mode, owner and group are
judged on the cache pool mount of the server, where mergerfs passes the branch attributes
through: the reference mode, and the uid and primary gid of the share user. The mode, uid and
gid that mount.cifs reports (its own mapping without unix extensions, mount.cifs(8)) and each
full allinfo mask are recorded, never judged; user xattrs are S1.7's. The trash exclusion
is part of S1.1: no trash entry and no trash directory in the client manifest or in the listing
of either pool on the server, and a file created through the share at the path of a trash entry
lands on the pool and leaves the trash untouched. That exclusion is an expected behaviour that
was UNVERIFIED before this check; its outcome is recorded in the evidence either way.

S1.11 reboots the server and does nothing by hand there: the base pool, the cache pool and
smbd must come back by themselves, and the client manifest of the whole share must be
identical before and after. A boot that comes up incomplete leaves its diagnostics
(s1.boot_diag, task T051e) in the evidence first; they never change the outcome. Scaffolding
for the spikes, not product code."""

import json
from collections.abc import Iterable, Mapping

from s1 import boot_diag
from s1.context import BASE, BASE_UNIT, CACHE, CACHE_UNIT, SMBD, Lab
from s1.fixture import ARCHIVE, FIXTURE, NEW_AT_TRASHED_PATH, POOL, TRASHED_PATH, trash, view

from testlib.fixtures import content_sha256, expected_manifest
from testlib.manifest import Difference, Manifest, compare, describe
from testlib.smb import DOS_HIDDEN, DOS_READONLY, DOS_SYSTEM

# smbclient checksums every archive file up to this size (the file above 4 GiB is S1.8's).
SMBCLIENT_MAX_BYTES = 64 * 1024 * 1024
TRASH_DIR = "trash"
DOS_DIRECTORY = 0x10
# The reference copy stores no DOS attribute and the share maps none of these from the mode
# (Samba defaults map readonly, hidden and system = no); archive, normal and sparse are recorded.
DOS_NEVER = DOS_READONLY | DOS_HIDDEN | DOS_SYSTEM

# Mode (stat.S_IMODE), uid and gid of each relative path below a root; None if it is absent.
_PERMISSIONS = """import json, os, stat, sys
found = {}
for rel in json.loads(sys.argv[2]):
    try:
        info = os.lstat(os.path.join(sys.argv[1], rel))
    except FileNotFoundError:
        found[rel] = None
    else:
        found[rel] = [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
print(json.dumps(found))"""


def archive_view_differences(expected: Manifest, share: Manifest) -> list[Difference]:
    """Differences between the archive's reference manifest and the share at those paths, in
    type, size, mtime and SHA-256; paths the share shows from the pool only are not part of the
    archive view. The mode and user xattrs mount.cifs reports are its own, never judged."""
    shown = {path: share[path] for path in expected if path in share}
    return compare(expected, shown, ignore=("mode", "xattrs"))


def cache_pool_permission_failures(
    expected: Manifest, observed: Mapping[str, list[int] | None], uid: int, gid: int
) -> list[str]:
    """Mode, uid and gid of every reference path on the server's cache pool mount against the
    reference mode and the share user's uid and primary gid; `observed` maps each path to
    [mode, uid, gid], or None when the path is absent."""
    failures = []
    for path, item in expected.items():
        found = observed.get(path)
        if found is None:
            failures.append(f"{path} is missing on {CACHE}")
            continue
        wanted = {"mode": item.mode, "uid": uid, "gid": gid}
        for (field, want), got in zip(wanted.items(), found, strict=True):
            if got != want:
                show = oct if field == "mode" else str
                failures.append(f"{path} {field} {show(got)} on {CACHE}, expected {show(want)}")
    return failures


def dos_attribute_failures(expected: Manifest, masks: Mapping[str, int]) -> list[str]:
    """The allinfo mask of each path in `masks`: the directory bit exactly on the reference
    directories, none of readonly, hidden and system; every other bit is not judged."""
    failures = []
    for path, mask in sorted(masks.items()):
        is_dir = expected[path].kind == "dir"
        if is_dir and not mask & DOS_DIRECTORY:
            failures.append(f"{path} DOS attributes {mask:#x}: directory bit missing")
        if not is_dir and mask & DOS_DIRECTORY:
            failures.append(f"{path} DOS attributes {mask:#x}: directory bit on a file")
        if mask & DOS_NEVER:
            failures.append(f"{path} DOS attributes {mask:#x}: readonly, hidden or system set")
    return failures


def trash_leaks(listing: Iterable[str], entries: Iterable[str]) -> list[str]:
    """Listed paths that show the trash: a trash directory at the top, or a trash entry at its
    own path or below any directory."""
    entries = list(entries)

    def leaked(path: str) -> bool:
        if path.split("/")[0] == TRASH_DIR:
            return True
        return any(path == entry or path.endswith("/" + entry) for entry in entries)

    return sorted(path for path in listing if leaked(path))


def reboot_failures(units_active: Mapping[str, bool], differences: list[Difference]) -> list[str]:
    down = [unit for unit, up in units_active.items() if not up]
    failures = [f"{unit} is not active after the boot" for unit in down]
    if differences:
        failures.append(f"view differs after the reboot: {describe(differences)}")
    return failures


def _pool_listing(lab: Lab, mountpoint: str) -> list[str]:
    out = lab.server.succeed(f"find {mountpoint} -mindepth 1 -printf '%P\\n'")
    return [line for line in out.splitlines() if line]


def _permissions(lab: Lab, machine, root: str, paths) -> dict[str, list[int] | None]:
    return json.loads(lab.python(machine, _PERMISSIONS, root, json.dumps(sorted(paths))))


def _octal_modes(found: Mapping[str, list[int] | None]) -> dict:
    """[mode, uid, gid] per path for the evidence, the mode in octal."""
    return {path: v and [oct(v[0]), *v[1:]] for path, v in found.items()}


def check_archive_view(lab: Lab) -> None:
    failures: list[str] = []
    expected = expected_manifest(FIXTURE, ARCHIVE)
    share = lab.share.manifest()
    differences = archive_view_differences(expected, share)
    if differences:
        failures.append(f"archive view differs: {describe(differences)}")

    smbclient = {}
    for path, item in expected.items():
        if item.kind == "file" and item.size <= SMBCLIENT_MAX_BYTES:
            smbclient[path] = lab.smb.sha256(path)
    wrong = sorted(path for path, sha in smbclient.items() if sha != expected[path].sha256)
    if wrong:
        failures.append(f"smbclient checksum differs: {', '.join(wrong)}")

    # A path missing through the share already fails above; allinfo reads the others.
    masks = {path: lab.smb.dos_attributes(path) for path in expected if path in share}
    failures += dos_attribute_failures(expected, masks)

    owner = {key: int(lab.server.succeed(f"id -{key} {lab.user}")) for key in ("u", "g")}
    on_cache = _permissions(lab, lab.server, CACHE, expected)
    failures += cache_pool_permission_failures(expected, on_cache, owner["u"], owner["g"])
    through_cifs = _permissions(lab, lab.client, lab.share.mountpoint, expected)

    trash_entries = [
        path
        for serial in ARCHIVE
        for path, item in view(trash(serial)).items()
        if item.kind == "file"
    ]
    listings = {"share": sorted(share), CACHE: _pool_listing(lab, CACHE)}
    listings[BASE] = _pool_listing(lab, BASE)
    leaks = {name: trash_leaks(listing, trash_entries) for name, listing in listings.items()}
    for name, found in leaks.items():
        if found:
            failures.append(f"trash visible in {name}: {', '.join(found)}")

    lab.write_through(lab.share, TRASHED_PATH, FIXTURE.seed, NEW_AT_TRASHED_PATH)
    created = {
        "sha256": lab.share.sha256(TRASHED_PATH),
        "expected_sha256": content_sha256(FIXTURE.seed, NEW_AT_TRASHED_PATH),
        "holders": lab.holders(TRASHED_PATH),
    }
    if created["sha256"] != created["expected_sha256"]:
        failures.append("the file created at the trashed path shows other content")
    if created["holders"] != [POOL]:
        failures.append(f"the file created at the trashed path lies on {created['holders']}")
    trash_after = {
        serial: describe(compare(view(trash(serial)), lab.manifest(trash(serial))))
        for serial in ARCHIVE
    }
    failures += [f"trash of {s} changed: {d}" for s, d in trash_after.items() if d]

    lab.evidence(
        "S1.1-archive-view",
        {
            "archive_paths": len(expected),
            "differences": describe(differences, limit=None),
            "smbclient_checked": len(smbclient),
            "smbclient_differs": wrong,
            "share_user": {"uid": owner["u"], "gid": owner["g"]},
            "cache_pool_mode_uid_gid": _octal_modes(on_cache),
            "mount_cifs_mode_uid_gid_recorded": _octal_modes(through_cifs),
            "allinfo_masks_recorded": {path: f"{mask:#x}" for path, mask in masks.items()},
            "trash_entries": trash_entries,
            "trash_leaks": leaks,
            "created_at_trashed_path": created,
            "trash_after_create": trash_after,
            "failures": failures,
        },
    )
    assert not failures, "; ".join(failures)


def _boot_diagnostics(lab: Lab, units: Mapping[str, bool], settled: bool, host) -> None:
    """Capture the boot state into the evidence if the boot is incomplete (s1.boot_diag);
    whatever goes wrong here never reaches the check."""
    try:
        data = boot_diag.capture_if_incomplete(
            lab.server, units, settled=settled, host_at_start=host
        )
        if data is not None:
            lab.evidence("S1.11-boot-diagnostics", data)
    except Exception:  # the diagnostics are evidence only, never an outcome
        pass


def check_reboot_view(lab: Lab) -> None:
    before = lab.share.manifest()
    lab.umount_clients()
    lab.server.shutdown()
    try:
        host = boot_diag.host_state()
    except Exception:  # evidence only
        host = None
    lab.server.start()
    try:
        lab.server.wait_for_unit("multi-user.target")
        lab.server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
    except Exception:
        _boot_diagnostics(lab, {}, False, host)
        raise
    units = {
        unit: lab.server.execute(f"systemctl is-active {unit}")[0] == 0
        for unit in (BASE_UNIT, CACHE_UNIT, SMBD)
    }
    _boot_diagnostics(lab, units, True, host)
    differences: list[Difference] = []
    if all(units.values()):
        lab.mount_clients()
        differences = compare(before, lab.share.manifest())
    failures = reboot_failures(units, differences)
    lab.evidence(
        "S1.11-reboot-view",
        {
            "units_active": units,
            "paths": len(before),
            "differences": describe(differences, limit=None),
        },
    )
    assert not failures, "; ".join(failures)
