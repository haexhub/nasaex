"""Check S1.9 after the out-of-space amendment (task T047a, spec Clarifications 2026-10-19): per
write (the mount.cifs create, the smbclient upload, the mount.cifs append) the error the client
reports, archive and trash unchanged (tree hashes, no inotify event), the written path on the
pool branch only, a leftover that is a byte-exact prefix of the source, no other pool file
changed, the append head intact, and a retry that succeeds. Over fabricated observations; the
full pool and the writes need the VMs."""

import hashlib

from s1.checks_large_space import space_failures

TABLE = {"errno": "ENOSPC", "smb_status": "NT_STATUS_DISK_FULL", "write_bytes": 1024}
SOURCE = bytes(range(256)) * 4


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def observed(step: str, leftover: bytes | None = SOURCE[:100]) -> dict:
    """What a clean failed write and its retry leave: the criteria's error, nothing on the
    archive, the leftover a prefix of the source on the pool branch only."""
    if step == "put":
        report = [1, "cli_push returned NT_STATUS_DISK_FULL\nclosing remote file"]
        retry = [0, "putting file /root/s1.9-source.bin as \\s1.9\\new-smbclient.bin"]
    else:
        report = {"errno": "ENOSPC", "step": "fsync", "written": 1024}
        retry = {"errno": None, "step": None, "written": 1024}
    seen = {
        "report": report,
        "archive_changed": [],
        "archive_events": [],
        "holders": [] if leftover is None else ["pool"],
        "leftover": None if leftover is None else {"size": len(leftover), "sha256": sha(leftover)},
        "prefix_sha256": None if leftover is None else sha(SOURCE[: len(leftover)]),
        "pool_rest_differences": "",
        "retry": {"report": retry, "holders": ["pool"], "reads_back": True},
    }
    if step == "append":
        seen["head_intact"] = True
    return seen


def writes(**changes) -> dict:
    """The three writes, clean, with `changes` merged into the observation of each step."""
    clean = {step: observed(step) for step in ("create", "put", "append")}
    return {step: {**seen, **changes.get(step, {})} for step, seen in clean.items()}


def test_a_prefix_leftover_in_every_write_passes():
    assert space_failures(TABLE, writes()) == []


def test_an_absent_or_empty_leftover_is_a_prefix():
    absent, empty = observed("create", None), observed("put", b"")

    assert space_failures(TABLE, {"create": absent, "put": empty}) == []


def test_a_zero_filled_leftover_of_the_same_length_fails():
    zeros = bytes(100)
    found = {"leftover": {"size": 100, "sha256": sha(zeros)}}

    assert space_failures(TABLE, writes(create=found)) == [
        "create: the leftover on the pool branch is not a prefix of the source"
        " (partial, prefix differs)"
    ]


def test_a_leftover_with_other_content_of_the_same_length_fails():
    found = {"leftover": {"size": 100, "sha256": sha(SOURCE[1:101])}}

    assert space_failures(TABLE, writes(append=found)) == [
        "append: the leftover on the pool branch is not a prefix of the source"
        " (partial, prefix differs)"
    ]


def test_a_leftover_on_an_archive_branch_fails():
    observations = writes(create={"holders": ["pool", "arch1"]}, put={"holders": ["arch0"]})

    assert space_failures(TABLE, observations) == [
        "create: the written path lies on ['pool', 'arch1']",
        "put: the written path lies on ['arch0']",
    ]


def test_a_changed_trash_tree_hash_or_an_inotify_event_fails():
    event = "CREATE /mnt/archive/arch0/branch/s1.9/new.bin"
    observations = writes(
        put={"archive_changed": ["trash:arch0"]}, append={"archive_events": [event]}
    )

    assert space_failures(TABLE, observations) == [
        "put: trash:arch0 changed",
        f"append: 1 write event(s) on the archive filesystems: {event}",
    ]


def test_a_changed_other_pool_file_fails():
    difference = "pool-only/notes.txt sha256 'a' -> 'b'"

    assert space_failures(TABLE, writes(create={"pool_rest_differences": difference})) == [
        f"create: other pool files changed: {difference}"
    ]


def test_a_write_that_reports_success_under_the_precondition_fails():
    observations = writes(
        create={"report": {"errno": None, "step": None, "written": 1024}},
        put={"report": [0, "putting file /root/s1.9-source.bin as \\s1.9\\new-smbclient.bin"]},
        append={"report": {"errno": "EIO", "step": "fsync", "written": 1024}},
    )

    assert space_failures(TABLE, observations) == [
        "create returned errno None, expected ENOSPC",
        "put reported success (exit 0)",
        "put did not report NT_STATUS_DISK_FULL",
        "append returned errno EIO, expected ENOSPC",
    ]


def test_a_damaged_append_head_fails():
    assert space_failures(TABLE, writes(append={"head_intact": False})) == [
        "the appended file no longer begins with its original bytes"
    ]


def test_a_failing_retry_fails():
    observations = writes(
        create={
            "retry": {
                "report": {"errno": "ENOSPC", "step": "fsync", "written": 1024},
                "holders": ["pool"],
                "reads_back": True,
            }
        },
        put={"retry": {"report": [1, "NT_STATUS_DISK_FULL"], "holders": [], "reads_back": False}},
        append={"retry": {"report": {"errno": None}, "holders": ["arch2"], "reads_back": True}},
    )

    assert space_failures(TABLE, observations) == [
        "create: the retry reported an error (ENOSPC at fsync)",
        "put: the retry reported an error (exit 1: NT_STATUS_DISK_FULL)",
        "put: the retry lies on []",
        "put: the retry reads back with other data",
        "append: the retry lies on ['arch2']",
    ]
