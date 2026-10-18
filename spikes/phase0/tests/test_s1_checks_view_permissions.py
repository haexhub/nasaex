"""Check S1.1 after the permissions amendment (task T043a, spec Clarifications 2026-10-18): what
the share is judged on (type, size, whole-second mtime, SHA-256 and the DOS attributes of
smbclient allinfo), what the cache pool mount of the server is judged on (mode, uid and gid
against the reference), and what is only recorded (the mode mount.cifs reports, the archive
and normal DOS bits). Over fabricated observations; reading them needs the VMs."""

from s1.checks_view import (
    archive_view_differences,
    cache_pool_permission_failures,
    dos_attribute_failures,
)

from testlib.manifest import Difference, Item

FILE = Item("file", 4, 0o640, 1_700_000_000, (("user.origin", "61"),), "a" * 64)
DIR = Item("dir", None, 0o755, None, (), None)
EXPECTED = {"archive": DIR, "archive/a.txt": FILE}
UID, GID = 1000, 100


def test_a_mode_or_xattr_that_differs_through_the_share_only_is_not_judged():
    share = {"archive": DIR, "archive/a.txt": Item("file", 4, 0o755, 1_700_000_000, (), "a" * 64)}

    assert archive_view_differences(EXPECTED, share) == []


def test_an_mtime_one_second_off_through_the_share_fails():
    share = {"archive": DIR, "archive/a.txt": Item("file", 4, 0o640, 1_700_000_001, (), "a" * 64)}

    assert archive_view_differences(EXPECTED, share) == [
        Difference("archive/a.txt", "mismatch", "mtime", 1_700_000_000, 1_700_000_001)
    ]


def test_a_path_of_the_wrong_type_or_a_wrong_size_through_the_share_fails():
    share = {"archive": FILE, "archive/a.txt": Item("file", 5, 0o640, 1_700_000_000, (), "a" * 64)}

    assert archive_view_differences(EXPECTED, share) == [
        Difference("archive", "mismatch", "kind", "dir", "file"),
        Difference("archive/a.txt", "mismatch", "size", 4, 5),
    ]


def test_the_reference_mode_uid_and_gid_on_the_cache_pool_mount_pass():
    observed = {"archive": [0o755, UID, GID], "archive/a.txt": [0o640, UID, GID]}

    assert cache_pool_permission_failures(EXPECTED, observed, UID, GID) == []


def test_a_mode_uid_or_gid_that_differs_on_the_cache_pool_mount_fails():
    observed = {"archive": [0o755, 0, GID], "archive/a.txt": [0o644, UID, 0]}

    assert cache_pool_permission_failures(EXPECTED, observed, UID, GID) == [
        "archive uid 0 on /mnt/cache, expected 1000",
        "archive/a.txt mode 0o644 on /mnt/cache, expected 0o640",
        "archive/a.txt gid 0 on /mnt/cache, expected 100",
    ]


def test_a_path_missing_on_the_cache_pool_mount_fails():
    observed = {"archive": [0o755, UID, GID], "archive/a.txt": None}

    assert cache_pool_permission_failures(EXPECTED, observed, UID, GID) == [
        "archive/a.txt is missing on /mnt/cache"
    ]


def test_the_directory_bit_on_directories_only_and_the_archive_or_normal_bit_pass():
    masks = {"archive": 0x10, "archive/a.txt": 0x20}

    assert dos_attribute_failures(EXPECTED, masks) == []
    assert dos_attribute_failures(EXPECTED, {"archive": 0x30, "archive/a.txt": 0x80}) == []


def test_a_hidden_or_system_bit_fails():
    masks = {"archive": 0x10 | 0x02, "archive/a.txt": 0x20 | 0x04}

    assert dos_attribute_failures(EXPECTED, masks) == [
        "archive DOS attributes 0x12: readonly, hidden or system set",
        "archive/a.txt DOS attributes 0x24: readonly, hidden or system set",
    ]


def test_a_readonly_bit_fails():
    assert dos_attribute_failures(EXPECTED, {"archive/a.txt": 0x21}) == [
        "archive/a.txt DOS attributes 0x21: readonly, hidden or system set"
    ]


def test_a_missing_directory_bit_or_a_directory_bit_on_a_file_fails():
    masks = {"archive": 0x20, "archive/a.txt": 0x10}

    assert dos_attribute_failures(EXPECTED, masks) == [
        "archive DOS attributes 0x20: directory bit missing",
        "archive/a.txt DOS attributes 0x10: directory bit on a file",
    ]
