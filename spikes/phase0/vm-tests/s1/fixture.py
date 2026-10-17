"""The S1 data set (task T040): what the branches and the trash hold before the S1 checks act
on the share. A description for testlib.fixtures: the host derives expected manifests from it,
the guest populates the branches from the same description (`populate` with `roots`).

Branch keys are `pool`, the archive serials of the `large` profile and `trash:<serial>` for the
trash directory beside the branch directory of an archive disk (task T028a). The edge cases of
the spec each appear once: an archive branch with no entry (arch2, the empty archive), an empty
directory on the pool only (the empty pool; the pool branch itself holds pool-only files),
directories only on archive disks, zero-byte, 255-byte and non-ASCII names, a sparse file and a
hard link (testlib.fixtures.edge_cases), and a file above 4 GiB on the 10 GiB archive disk.

No file path lives on two branches, so the expected outcome of a client delete does not depend
on the unlink policy of the cache pool (the mergerfs default `epall` or the candidate `ff`, see
vm-tests/probes/concurrent_delete.results.md); a check that needs a file on two branches
creates it itself and takes the policy as a parameter.

Scaffolding for the spikes, not product code."""

from testlib.fixtures import CHUNK_SIZE, Entry, Fixture, edge_cases, expected_manifest
from testlib.manifest import Manifest

SEED = "S1"
POOL = "pool"
POOL_BRANCH = "/mnt/pool"  # spike.tieredCache.poolBranch (nix/tiered-cache.nix)
ARCHIVE = ("arch0", "arch1", "arch2")  # archive serials of the `large` profile (nix/profiles.nix)
SHARE_BRANCHES = (POOL, *ARCHIVE)

# Above 2**32 bytes and not a whole number of chunks, so 32-bit offsets and the tail both show.
LARGE_SIZE = 2**32 + CHUNK_SIZE + 1

# A trash entry is `<pool-relative path>.<copy-time ctime ns>.<pool inode>` (mover contract).
TRASH_CTIME_NS = 1_700_000_000_123_456_789
TRASH_INODE = 4242

# A trash entry of arch0 has this pool-relative path; the S1.1 check creates the file through
# the share and expects no collision with the trashed entry.
TRASHED_PATH = "shared/trashed-then-created.txt"


def trash(serial: str) -> str:
    """Branch key of the trash directory of an archive disk."""
    return f"trash:{serial}"


def trash_name(path: str) -> str:
    return f"{path}.{TRASH_CTIME_NS}.{TRASH_INODE}"


# Written through the share by the checks, never populated; expected to land on the pool.
NEW_LARGE = Entry("large/new-through-share.bin", POOL, size=2**32 + 2 * CHUNK_SIZE + 3)
NEW_AT_TRASHED_PATH = Entry(TRASHED_PATH, POOL, size=96)

ENTRIES = (
    # pool only, with an empty directory that exists on the pool alone
    Entry("pool-only/notes.txt", POOL, size=1000),
    Entry("pool-only/nested/data.bin", POOL, size=70_000),
    Entry("pool-empty-dir", POOL, kind="dir"),
    # a directory on the pool and on two archive branches, different files in each
    Entry("shared/from-pool.txt", POOL, size=300),
    Entry("shared/from-archive.txt", "arch0", size=400),
    Entry("shared/sub/deep-archive.txt", "arch1", size=500, mode=0o600),
    # archive only, one directory on two archive disks, attributes already present
    Entry(
        "archive/docs/report.txt",
        "arch0",
        size=4096,
        mtime=1_600_000_000,
        xattrs=(("user.origin", "archive"),),
    ),
    Entry("archive/photos/img-0001.jpg", "arch0", size=300_000, mode=0o640),
    Entry("archive/photos/img-0002.jpg", "arch1", size=250_000),
    Entry("archive-empty-dir", "arch1", kind="dir"),
    *edge_cases("arch0"),
    Entry("large/archived.bin", "arch1", size=LARGE_SIZE),
    # arch2: no branch entry (the empty archive); the trash of arch0 and arch2 is populated
    Entry(trash_name(TRASHED_PATH), trash("arch0"), size=128),
    Entry(trash_name("archive/docs/old-report.txt"), trash("arch0"), size=2048),
    Entry(trash_name("pool-only/deleted.bin"), trash("arch2"), size=3000),
)

FIXTURE = Fixture(SEED, ENTRIES)


def roots(topology: dict) -> dict[str, str]:
    """Guest directory of every branch key, from the archive layout of the topology."""
    layout = {disk["serial"]: disk for disk in topology["archiveLayout"]}
    missing = [serial for serial in ARCHIVE if serial not in layout]
    if missing:
        raise ValueError(f"the topology has no archive disk {', '.join(missing)}")
    found = {POOL: POOL_BRANCH}
    for serial in ARCHIVE:
        found[serial] = layout[serial]["branch"]
        found[trash(serial)] = layout[serial]["trash"]
    return found


def view(branch: str, fixture: Fixture = FIXTURE, with_hashes: bool = True) -> Manifest:
    """Expected manifest of one branch or trash directory."""
    return expected_manifest(fixture, (branch,), with_hashes)


def share_view(fixture: Fixture = FIXTURE, with_hashes: bool = True) -> Manifest:
    """Expected manifest of the share: the pool and archive branches as one tree, no trash."""
    return expected_manifest(fixture, SHARE_BRANCHES, with_hashes)
