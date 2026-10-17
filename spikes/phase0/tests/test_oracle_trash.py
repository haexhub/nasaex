"""Oracle invariants added by the trash amendment of FR-019 (maintainer decision of 2026-10-16):
trash content in (e), completed client writes (f), client deletes of a selected pool file (g)
and the permitted state per phase of a vanished item (contracts/mover-steps.md). Every
violation is fabricated and must be flagged; the host's injection log is the only record of a
delete, the oracle never reads mover output."""

import pytest

from testlib.manifest import Item
from testlib.oracle import (
    DeleteInjection,
    Observed,
    check_client_delete,
    check_completed_writes,
    check_trash_survival_after_device_failure,
)

COPIED = "a" * 64  # the copy the mover made (the fixture's expected bytes of item 1)
OTHER = "b" * 64
WRITTEN = "c" * 64  # content after a client write
PATH = "items/a-replacing.bin"
TRASH_NAME = f"{PATH}.1700000000123456789.4242"  # <path>.<copy-time ctime ns>.<pool inode>


def file_item(sha=COPIED, size=10):
    return Item(kind="file", size=size, mode=0o644, mtime=1, xattrs=(), sha256=sha)


COPY = file_item()


def observed(share=None, pool=None, archive=None, trash=None):
    return Observed(
        share={} if share is None else share,
        pool={} if pool is None else pool,
        archive={"arch0": {}, "arch1": {}} if archive is None else archive,
        trash={} if trash is None else trash,
    )


# The client's delete left the archive copy on arch1, or removed it (host-measured).
ARCHIVE_KEPT = DeleteInjection(PATH, frozenset({"arch1"}))
ARCHIVE_GONE = DeleteInjection(PATH, frozenset())
TRASHED = {"arch1": {TRASH_NAME: file_item()}}
IN_ARCHIVE = {"arch0": {}, "arch1": {PATH: file_item()}}


def details(violations):
    return [(v.invariant, v.path) for v in violations]


# (e) extended: no trash content lost by a single device failure


def test_trash_content_restored_under_its_trash_name_is_fine():
    restored = observed(trash={"arch1": {TRASH_NAME: file_item()}})

    assert check_trash_survival_after_device_failure(TRASHED, restored) == []


def test_trash_content_restored_under_its_former_archive_path_is_fine():
    # Judged by content: the landing path is recorded by the check, not judged (spec A6).
    restored = observed(archive=IN_ARCHIVE)

    assert check_trash_survival_after_device_failure(TRASHED, restored) == []


def test_a_trash_entry_lost_after_a_device_failure_violates_e():
    violations = check_trash_survival_after_device_failure(TRASHED, observed())

    assert details(violations) == [("e", TRASH_NAME)]


def test_a_trash_entry_restored_with_other_bytes_violates_e():
    restored = observed(trash={"arch1": {TRASH_NAME: file_item(sha=OTHER)}})

    assert details(check_trash_survival_after_device_failure(TRASHED, restored)) == [
        ("e", TRASH_NAME)
    ]


# (f) the content of every completed client write exists in the pool or in the archive


def test_a_completed_write_found_in_the_pool_is_fine():
    obs = observed(pool={PATH: file_item(sha=WRITTEN)})

    assert check_completed_writes({PATH: WRITTEN}, obs) == []


def test_a_completed_write_found_in_the_archive_under_any_path_is_fine():
    obs = observed(archive={"arch0": {"elsewhere.bin": file_item(sha=WRITTEN)}})

    assert check_completed_writes({PATH: WRITTEN}, obs) == []


def test_a_lost_completed_write_violates_f():
    obs = observed(pool={PATH: file_item()}, archive=IN_ARCHIVE)

    assert details(check_completed_writes({PATH: WRITTEN}, obs)) == [("f", PATH)]


def test_a_completed_write_seen_only_in_the_share_view_is_lost():
    # The raw pool and archive branches are the truth; the share view is derived from them.
    obs = observed(share={PATH: file_item(sha=WRITTEN)})

    assert details(check_completed_writes({PATH: WRITTEN}, obs)) == [("f", PATH)]


# (g) after the run that handled the delete


def test_trash_outcome_with_one_identical_trash_copy_is_fine():
    assert check_client_delete(ARCHIVE_KEPT, COPY, observed(trash=TRASHED)) == []


def test_archive_gone_outcome_with_nothing_left_is_fine():
    # A vanished item without archive copy: the client's delete removed it, as the host recorded.
    assert check_client_delete(ARCHIVE_GONE, COPY, observed()) == []


def test_a_deleted_file_that_reappears_through_the_share_violates_g():
    obs = observed(share={PATH: file_item()}, trash=TRASHED)

    assert ("g", PATH) in details(check_client_delete(ARCHIVE_KEPT, COPY, obs))


def test_an_archive_copy_left_in_the_branch_violates_g():
    obs = observed(share={PATH: file_item()}, archive=IN_ARCHIVE)

    assert ("g", PATH) in details(check_client_delete(ARCHIVE_KEPT, COPY, obs))


def test_a_missing_trash_copy_violates_g():
    violations = check_client_delete(ARCHIVE_KEPT, COPY, observed())

    assert details(violations) == [("g", PATH)]
    assert "deleted the archive copy" in violations[0].detail


def test_a_duplicated_trash_copy_violates_g():
    second = f"{PATH}.1700000000999999999.4242"
    trash = {"arch1": {TRASH_NAME: file_item(), second: file_item()}}

    violations = check_client_delete(ARCHIVE_KEPT, COPY, observed(trash=trash))

    assert details(violations) == [("g", PATH)]
    assert "2 trash copies" in violations[0].detail


def test_a_trash_copy_with_other_bytes_violates_g():
    trash = {"arch1": {TRASH_NAME: file_item(sha=OTHER)}}

    assert details(check_client_delete(ARCHIVE_KEPT, COPY, observed(trash=trash))) == [("g", PATH)]


def test_a_trash_copy_on_another_disk_than_the_archive_copy_violates_g():
    trash = {"arch0": {TRASH_NAME: file_item()}}

    assert details(check_client_delete(ARCHIVE_KEPT, COPY, observed(trash=trash))) == [("g", PATH)]


def test_a_trash_copy_although_the_delete_removed_the_archive_copy_violates_g():
    assert details(check_client_delete(ARCHIVE_GONE, COPY, observed(trash=TRASHED))) == [
        ("g", PATH)
    ]


def test_trash_entries_of_other_paths_do_not_count():
    trash = {
        "arch1": {
            TRASH_NAME: file_item(),
            f"{PATH}.old.1.2": file_item(),
            "items/a-replacing.bin2.1.2": file_item(),
        }
    }

    assert check_client_delete(ARCHIVE_KEPT, COPY, observed(trash=trash)) == []


def test_the_pool_branch_named_in_branches_after_delete_is_no_archive_branch():
    injection = DeleteInjection(PATH, frozenset({"pool"}))

    assert check_client_delete(injection, COPY, observed()) == []


# Permitted state per phase of a vanished item (mover contract)


def test_before_the_trash_move_the_archive_copy_is_in_place():
    obs = observed(share={PATH: file_item()}, archive=IN_ARCHIVE)

    assert check_client_delete(ARCHIVE_KEPT, COPY, obs, phase="deleted") == []


def test_before_the_trash_move_a_trash_copy_violates_the_permitted_state():
    obs = observed(archive=IN_ARCHIVE, trash=TRASHED)

    assert details(check_client_delete(ARCHIVE_KEPT, COPY, obs, phase="deleted")) == [("g", PATH)]


def test_before_the_trash_move_a_changed_archive_copy_violates_the_permitted_state():
    obs = observed(archive={"arch1": {PATH: file_item(sha=OTHER)}})

    assert ("g", PATH) in details(check_client_delete(ARCHIVE_KEPT, COPY, obs, phase="deleted"))


@pytest.mark.parametrize(
    "state",
    [
        {"archive": IN_ARCHIVE},  # rename not durable after a poweroff
        {"trash": TRASHED},  # rename durable
    ],
)
def test_between_the_trash_points_either_name_is_permitted(state):
    obs = observed(**state)

    assert check_client_delete(ARCHIVE_KEPT, COPY, obs, phase="trash-renamed") == []


@pytest.mark.parametrize("phase", ["deleted", "trash-renamed", "trashed"])
def test_content_existing_nowhere_is_flagged_in_every_phase(phase):
    violations = check_client_delete(ARCHIVE_KEPT, COPY, observed(), phase=phase)

    assert ("g", PATH) in details(violations)
    assert any("nowhere" in v.detail for v in violations)


@pytest.mark.parametrize("phase", ["deleted", "trash-renamed", "trashed"])
def test_content_existing_nowhere_is_accepted_when_the_delete_removed_the_archive_copy(phase):
    assert check_client_delete(ARCHIVE_GONE, COPY, observed(), phase=phase) == []


@pytest.mark.parametrize("phase", ["deleted", "trash-renamed", "trashed"])
def test_a_pool_copy_of_a_vanished_item_violates_the_permitted_state(phase):
    state = {"archive": IN_ARCHIVE} if phase != "trashed" else {"trash": TRASHED}
    obs = observed(pool={PATH: file_item()}, **state)

    assert ("g", PATH) in details(check_client_delete(ARCHIVE_KEPT, COPY, obs, phase=phase))


def test_an_unknown_phase_is_refused():
    with pytest.raises(ValueError):
        check_client_delete(ARCHIVE_KEPT, COPY, observed(), phase="post:M2")


# inject:modify-archive-after-delete:M4 together with inject:delete-pool-after:M3


def test_archive_copy_changed_in_place_stays_visible_and_untrashed():
    injection = DeleteInjection(PATH, frozenset({"arch1"}), write_after_delete_sha256=WRITTEN)
    obs = observed(
        share={PATH: file_item(sha=WRITTEN)},
        archive={"arch0": {}, "arch1": {PATH: file_item(sha=WRITTEN)}},
    )

    assert check_client_delete(injection, COPY, obs) == []


def test_archive_copy_changed_in_place_but_trashed_violates_g():
    injection = DeleteInjection(PATH, frozenset({"arch1"}), write_after_delete_sha256=WRITTEN)
    obs = observed(trash={"arch1": {TRASH_NAME: file_item(sha=WRITTEN)}})

    assert ("g", PATH) in details(check_client_delete(injection, COPY, obs))


def test_a_write_after_the_delete_removed_the_archive_copy_is_a_new_pool_file():
    injection = DeleteInjection(PATH, frozenset(), write_after_delete_sha256=WRITTEN)
    obs = observed(share={PATH: file_item(sha=WRITTEN)}, pool={PATH: file_item(sha=WRITTEN)})

    assert check_client_delete(injection, COPY, obs) == []


def test_a_write_after_the_delete_that_is_not_visible_violates_g():
    injection = DeleteInjection(PATH, frozenset(), write_after_delete_sha256=WRITTEN)

    assert ("g", PATH) in details(check_client_delete(injection, COPY, observed()))


def test_the_injection_log_cannot_carry_mover_output():
    with pytest.raises(TypeError):
        DeleteInjection(PATH, frozenset(), mover_report="vanished-archive-gone")
