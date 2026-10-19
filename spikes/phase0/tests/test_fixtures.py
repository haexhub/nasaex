"""Seeded fixtures: same seed and description give the same bytes and the same expected
manifest; large files are streamed; the guest generator reproduces the host's expectation."""

import hashlib
import os
import tracemalloc

import pytest

from testlib.fixtures import (
    CHUNK_SIZE,
    Entry,
    Fixture,
    content_chunks,
    content_sha256,
    edge_cases,
    expected_manifest,
    flipped_sha256,
    from_json,
    populate,
    to_json,
)
from testlib.manifest import build_manifest, compare


def digest(chunks):
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk)
    return h.hexdigest()


def xattrs_supported(path) -> bool:
    probe = path / "probe"
    probe.write_bytes(b"")
    try:
        os.setxattr(probe, "user.probe", b"1")
    except OSError:
        return False
    return True


@pytest.fixture
def small():
    return Fixture(
        seed="seed-1",
        entries=(
            Entry("docs/a.txt", branch="pool", size=1000, mode=0o640),
            Entry("docs/b.bin", branch="arch0", size=3 * CHUNK_SIZE + 17),
            Entry("empty.txt", branch="arch0", size=0),
        ),
    )


def test_same_seed_and_description_give_the_same_bytes(small):
    entry = small.entries[1]

    assert digest(content_chunks("seed-1", entry)) == digest(content_chunks("seed-1", entry))
    assert content_sha256("seed-1", entry) == digest(content_chunks("seed-1", entry))


def test_a_different_seed_or_path_gives_different_bytes(small):
    entry = small.entries[1]
    other_path = Entry("docs/other.bin", branch="arch0", size=entry.size)

    base = content_sha256("seed-1", entry)

    assert content_sha256("seed-2", entry) != base
    assert content_sha256("seed-1", other_path) != base


def test_stream_length_equals_the_size_for_every_size_class():
    for size in (0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 5 * CHUNK_SIZE):
        entry = Entry("f", branch="pool", size=size)
        chunks = list(content_chunks("s", entry))
        assert sum(len(c) for c in chunks) == size
        assert all(0 < len(c) <= CHUNK_SIZE for c in chunks)


def test_expected_manifest_is_reproducible_and_ignores_entry_order(small):
    reordered = Fixture(seed=small.seed, entries=tuple(reversed(small.entries)))

    assert expected_manifest(small) == expected_manifest(small)
    assert expected_manifest(small) == expected_manifest(reordered)


def test_expected_manifest_describes_files_modes_and_implicit_directories(small):
    manifest = expected_manifest(small)

    assert set(manifest) == {"docs", "docs/a.txt", "docs/b.bin", "empty.txt"}
    assert manifest["docs"].kind == "dir"
    assert manifest["docs/a.txt"].mode == 0o640
    assert manifest["docs/a.txt"].size == 1000
    assert manifest["empty.txt"].size == 0
    assert manifest["empty.txt"].sha256 == hashlib.sha256(b"").hexdigest()


def test_expected_manifest_can_be_limited_to_one_branch(small):
    pool = expected_manifest(small, branches=("pool",))
    archive = expected_manifest(small, branches=("arch0",))

    assert set(pool) == {"docs", "docs/a.txt"}
    assert set(archive) == {"docs", "docs/b.bin", "empty.txt"}


def test_large_files_are_streamed_not_materialised():
    entry = Entry("big.bin", branch="arch0", size=96 * 1024 * 1024)

    tracemalloc.start()
    sha = content_sha256("seed", entry)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(sha) == 64
    assert peak < 8 * 1024 * 1024


def test_a_file_above_4_gib_is_described_without_reading_it():
    # The expectation of a >4 GiB file is only computed when asked for; describing it is free.
    entry = Entry("huge.bin", branch="arch0", size=5 * 1024**3)

    manifest = expected_manifest(Fixture("s", (entry,)), with_hashes=False)

    assert manifest["huge.bin"].size == 5 * 1024**3
    assert manifest["huge.bin"].sha256 is None


def test_sparse_entry_has_zero_holes_and_seeded_extents():
    entry = Entry("sparse.bin", branch="arch0", size=4 * CHUNK_SIZE, extents=((CHUNK_SIZE, 100),))

    data = b"".join(content_chunks("s", entry))

    assert len(data) == 4 * CHUNK_SIZE
    assert data[:CHUNK_SIZE] == bytes(CHUNK_SIZE)
    assert data[CHUNK_SIZE : CHUNK_SIZE + 100] != bytes(100)
    assert data[CHUNK_SIZE + 100 :] == bytes(3 * CHUNK_SIZE - 100)


def test_edge_case_set_covers_the_spec_list():
    entries = edge_cases(branch="arch0")
    by_kind = {}
    for entry in entries:
        by_kind.setdefault(entry.kind, []).append(entry)

    assert any(e.kind == "file" and e.size == 0 for e in entries)  # empty and zero-byte
    assert any(max(len(part.encode()) for part in e.path.split("/")) >= 250 for e in entries)
    assert any(not e.path.isascii() for e in entries)
    assert any(e.extents is not None for e in entries)  # sparse
    assert by_kind.get("hardlink")
    assert by_kind.get("dir")  # directories that exist on one branch only


def test_populate_reproduces_the_expected_manifest_on_disk(tmp_path):
    if not xattrs_supported(tmp_path):
        pytest.skip("user xattrs are not supported on the test filesystem")
    fixture = Fixture(
        seed="roundtrip",
        entries=(
            *edge_cases(branch="arch0"),
            Entry("x/y/with-attr.txt", branch="arch0", size=300, xattrs=(("user.tag", "v1"),)),
            Entry("pool-only/new.txt", branch="pool", size=64),
        ),
    )
    roots = {"arch0": tmp_path / "arch0", "pool": tmp_path / "pool"}
    for root in roots.values():
        root.mkdir()

    populate(fixture, roots)

    for branch, root in roots.items():
        expected = expected_manifest(fixture, branches=(branch,))
        assert compare(expected, build_manifest(root)) == [], branch


def test_populate_leaves_holes_in_sparse_files(tmp_path):
    entry = Entry("sparse.bin", branch="b", size=8 * CHUNK_SIZE, extents=((0, 10),))
    root = tmp_path / "b"
    root.mkdir()

    populate(Fixture("s", (entry,)), {"b": root})

    stat = (root / "sparse.bin").stat()
    assert stat.st_size == 8 * CHUNK_SIZE
    assert stat.st_blocks * 512 < 2 * CHUNK_SIZE


def test_flipped_sha256_is_the_hash_after_inverting_the_bits_of_each_named_byte():
    entry = Entry("f.bin", "pool", size=2 * CHUNK_SIZE + 5)
    data = bytearray(b"".join(content_chunks("seed", entry)))
    offsets = (0, CHUNK_SIZE - 1, CHUNK_SIZE, 2 * CHUNK_SIZE + 4)
    for offset in offsets:
        data[offset] ^= 0xFF

    assert flipped_sha256("seed", entry, offsets) == hashlib.sha256(data).hexdigest()
    assert flipped_sha256("seed", entry, ()) == content_sha256("seed", entry)


def test_flipped_sha256_refuses_an_offset_outside_the_file():
    with pytest.raises(ValueError, match="offset 10"):
        flipped_sha256("seed", Entry("f.bin", "pool", size=10), (10,))


@pytest.mark.parametrize("kind", ["dir", "hardlink"])
def test_only_file_entries_have_content_of_their_own(kind):
    entry = Entry("x", "pool", kind=kind, link_to="y" if kind == "hardlink" else None)

    with pytest.raises(ValueError, match="only file entries"):
        list(content_chunks("seed", entry))


def test_a_fixture_round_trips_through_json_for_the_guest():
    extra = Entry("x.txt", "pool", size=3, mode=0o600, xattrs=(("user.k", "v"),))
    fixture = Fixture(seed="seed-1", entries=(*edge_cases("arch0"), extra))

    assert from_json(to_json(fixture)) == fixture
