"""Check S1.9 after the out-of-space amendment (task T047a, spec Clarifications 2026-10-19): per
write (the mount.cifs create, the smbclient upload, the mount.cifs append) the error the client
reports, archive and trash unchanged (tree hashes, no inotify event), the written path on the
pool branch only, a leftover that is a byte-exact prefix of the source, no other pool file
changed, the append head intact, and a retry that succeeds. Also the wait for the freed space that
precedes the retries (task T047b): its target and its poll with an upper bound. Over fabricated
observations; the full pool and the writes need the VMs."""

import hashlib

from s1.checks_large_space import (
    SPACE_BOUND_S,
    SPACE_EPSILON,
    SPACE_POLL_S,
    freed_space_target,
    space_failures,
    wait_for_freed_space,
)

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
        "filler_bytes": 5000,
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


def test_a_missing_retry_fails_instead_of_passing():
    skipped = writes()
    del skipped["create"]["retry"]

    assert space_failures(TABLE, skipped) == ["create: the retry did not run"]


def test_the_freed_space_target_is_the_last_filler_plus_the_deleted_leftovers_less_epsilon():
    # The leftovers are 100 bytes each; the create's and the upload's are deleted, the append's
    # stays, and only the filler of the last write is still counted by the free bytes.
    observations = writes(create={"filler_bytes": 1}, append={"filler_bytes": 7000})

    assert freed_space_target(observations, epsilon=50) == 7000 + 100 + 100 - 50


def test_an_absent_leftover_adds_nothing_to_the_freed_space_target():
    observations = writes(create={"leftover": None})

    assert freed_space_target(observations, epsilon=0) == 5000 + 100


def test_the_epsilon_is_the_documented_16_mib_and_the_poll_is_half_a_second_up_to_a_minute():
    assert SPACE_EPSILON == 16 * 1024 * 1024
    assert (SPACE_POLL_S, SPACE_BOUND_S) == (0.5, 60)
    assert freed_space_target(writes()) == 5000 + 200 - SPACE_EPSILON


class Clock:
    """A clock that only moves when the poll sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_the_poll_reports_the_seconds_until_the_space_appeared():
    clock, samples = Clock(), iter([0, 0, 0, 900, 1000])

    freed = wait_for_freed_space(lambda: next(samples), 1000, clock=clock, sleep=clock.sleep)

    assert freed == {"appeared": True, "waited_s": 2.0, "avail": 1000, "target": 1000, "samples": 5}
    assert clock.sleeps == [0.5] * 4


def test_the_poll_does_not_wait_when_the_space_is_already_there():
    clock = Clock()

    freed = wait_for_freed_space(lambda: 5000, 1000, clock=clock, sleep=clock.sleep)

    assert freed["appeared"] is True
    assert (freed["waited_s"], freed["samples"], clock.sleeps) == (0.0, 1, [])


def test_the_poll_gives_up_after_the_bound_and_says_so():
    clock = Clock()

    freed = wait_for_freed_space(lambda: 3, 1000, clock=clock, sleep=clock.sleep)

    assert freed == {
        "appeared": False,
        "waited_s": 60.0,
        "avail": 3,
        "target": 1000,
        "samples": 121,
    }
    assert len(clock.sleeps) == 120


def test_the_poll_takes_the_bound_and_the_interval_it_is_given():
    clock = Clock()

    freed = wait_for_freed_space(
        lambda: 0, 1, bound_s=1, interval_s=0.5, clock=clock, sleep=clock.sleep
    )

    assert (freed["appeared"], freed["samples"], freed["waited_s"]) == (False, 3, 1.0)
