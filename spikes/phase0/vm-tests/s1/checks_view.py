"""Checks S1.1 and S1.11 (task T043): the archive as the share shows it, and the same view after
a reboot.

S1.1 compares every path of the archive branches, as the client sees it through the mount.cifs
mount, with the reference manifest derived from the S1 data set, in every manifest field: name,
type, size, mtime, mode, user xattrs and SHA-256 (spec User Story 1 scenario 1 names
permissions; the mode a mount.cifs mount reports is an open point of the criteria changelog).
smbclient, the second client implementation, checksums the archive files. The trash exclusion
is part of S1.1: no trash entry and no trash directory in the client manifest or in the listing
of either pool on the server, and a file created through the share at the path of a trash entry
lands on the pool and leaves the trash untouched. That exclusion is an expected behaviour that
was UNVERIFIED before this check; its outcome is recorded in the evidence either way.

S1.11 reboots the server and does nothing by hand there: the base pool, the cache pool and
smbd must come back by themselves, and the client manifest of the whole share must be
identical before and after. A boot that comes up incomplete leaves its diagnostics
(s1.boot_diag, task T051e) in the evidence first; they never change the outcome. Scaffolding
for the spikes, not product code."""

from collections.abc import Iterable, Mapping

from s1 import boot_diag
from s1.context import BASE, BASE_UNIT, CACHE, CACHE_UNIT, SMBD, Lab
from s1.fixture import ARCHIVE, FIXTURE, NEW_AT_TRASHED_PATH, POOL, TRASHED_PATH, trash, view

from testlib.fixtures import content_sha256, expected_manifest
from testlib.manifest import Difference, Manifest, compare, describe

# smbclient checksums every archive file up to this size (the file above 4 GiB is S1.8's).
SMBCLIENT_MAX_BYTES = 64 * 1024 * 1024
TRASH_DIR = "trash"


def archive_view_differences(expected: Manifest, share: Manifest) -> list[Difference]:
    """Differences between the archive's reference manifest and the share at those paths;
    paths the share shows from the pool only are not part of the archive view."""
    return compare(expected, {path: share[path] for path in expected if path in share})


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
