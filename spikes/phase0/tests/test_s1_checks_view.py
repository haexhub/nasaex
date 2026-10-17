"""Checks S1.1 and S1.11 (vm-tests/s1/checks_view.py): the decisions behind them, over
fabricated manifests. Reading the share and rebooting the server need the VMs."""

from s1.checks_view import archive_view_differences, reboot_failures, trash_leaks

from testlib.manifest import Difference, Item

FILE = Item("file", 4, 0o644, 1_700_000_000, (), "a" * 64)
DIR = Item("dir", None, 0o755, None, (), None)


def test_the_archive_view_ignores_pool_only_paths_of_the_share():
    expected = {"archive": DIR, "archive/a.txt": FILE}
    share = {**expected, "pool-only": DIR, "pool-only/b.txt": FILE}

    assert archive_view_differences(expected, share) == []


def test_the_archive_view_reports_a_missing_file_and_every_differing_field():
    expected = {"archive": DIR, "archive/a.txt": FILE, "archive/b.txt": FILE}
    synthetic = Item("file", 4, 0o755, 1_700_000_000, (), "b" * 64)
    share = {"archive": DIR, "archive/a.txt": synthetic}

    assert archive_view_differences(expected, share) == [
        Difference("archive/a.txt", "mismatch", "mode", 0o644, 0o755),
        Difference("archive/a.txt", "mismatch", "sha256", "a" * 64, "b" * 64),
        Difference("archive/b.txt", "missing"),
    ]


def test_a_trash_entry_or_a_trash_directory_in_a_listing_is_a_leak():
    entries = ["shared/x.txt.17.42", "old.bin.18.43"]
    listing = [
        "shared",
        "shared/x.txt",
        "shared/x.txt.17.42",
        "deep/old.bin.18.43",
        "trash",
        "trash/whatever",
        "trashcan/ok.txt",
    ]

    assert trash_leaks(listing, entries) == [
        "deep/old.bin.18.43",
        "shared/x.txt.17.42",
        "trash",
        "trash/whatever",
    ]


def test_a_listing_without_trash_content_has_no_leak():
    assert trash_leaks(["a", "a/b.txt"], ["a/b.txt.1.2"]) == []


def test_reboot_needs_every_unit_active_and_the_same_view():
    units = {"mnt-base.mount": True, "mnt-cache.mount": True, "samba-smbd.service": True}

    assert reboot_failures(units, []) == []
    assert reboot_failures({**units, "mnt-cache.mount": False}, []) == [
        "mnt-cache.mount is not active after the boot"
    ]
    assert reboot_failures(units, [Difference("a.txt", "missing")]) == [
        "view differs after the reboot: a.txt missing"
    ]
