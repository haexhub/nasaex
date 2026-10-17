"""Checks S1.6 and S1.7 (vm-tests/s1/checks_rename_attrs.py): the decisions behind them, over
fabricated manifests and observations. Renaming through the share, setting attributes and the
mover's move need the VMs."""

from s1.checks_rename_attrs import RENAMES, attribute_failures, renamed

from testlib.manifest import Item
from testlib.smb import DOS_ARCHIVE, DOS_HIDDEN, DOS_SYSTEM

F = Item("file", 1, 0o755, 5, (), "f" * 64)
D = Item("dir", None, 0o755, None, (), None)


def test_renamed_moves_a_file_or_a_whole_directory_and_nothing_else():
    manifest = {"a": D, "a/x.txt": F, "a/sub": D, "a/sub/y.txt": F, "ab.txt": F, "t": D}

    assert renamed(manifest, "a", "t/b") == {
        "t/b": D,
        "t/b/x.txt": F,
        "t/b/sub": D,
        "t/b/sub/y.txt": F,
        "ab.txt": F,
        "t": D,
    }
    assert renamed(manifest, "ab.txt", "c.txt") == {
        **{k: v for k, v in manifest.items() if k != "ab.txt"},
        "c.txt": F,
    }


def test_the_renames_cover_every_origin_and_moves_across_origins():
    sources = [src for src, _ in RENAMES]

    for origin in ("archive-file", "pool-file", "archive-dir", "pool-dir", "both-dir"):
        assert any(src.startswith(origin) for src in sources), origin
    assert ("moved-archive.txt", "pool-target/moved-archive.txt") in RENAMES
    assert ("moved-pool.txt", "archive-target/moved-pool.txt") in RENAMES


EXPECTED = {"archive.txt": {"user.s1": "archive", "user.preset": "from-branch"}}
HS = DOS_HIDDEN | DOS_SYSTEM


def test_attributes_kept_give_no_failure_whatever_other_dos_bits_are_set():
    observed = {
        "archive.txt": {"user.s1": "archive", "user.preset": "from-branch", "dos": HS | DOS_ARCHIVE}
    }

    assert attribute_failures("rename", observed, EXPECTED) == []


def test_a_lost_xattr_a_lost_dos_bit_or_an_error_is_a_failure_of_that_step():
    observed = {"archive.txt": {"user.s1": None, "user.preset": "from-branch", "dos": DOS_HIDDEN}}

    assert attribute_failures("remount", observed, EXPECTED) == [
        "remount: archive.txt user.s1 is None, expected 'archive'",
        "remount: archive.txt DOS attributes 0x2 lack hidden and system",
    ]
    broken = {"archive.txt": {"error": "SmbError: no attributes line"}}
    assert attribute_failures("mover", broken, EXPECTED) == [
        "mover: archive.txt could not be read: SmbError: no attributes line"
    ]
