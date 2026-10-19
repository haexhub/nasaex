"""Oracle invariants (a) to (e) of FR-019 over fabricated observed state. Every violation is
fabricated and must be flagged; the oracle takes manifests only, never mover or SnapRAID output."""

import pytest

from testlib.manifest import Item
from testlib.oracle import (
    Observed,
    Violation,
    check_invariants,
    check_no_loss_or_resurrection,
    check_no_partial_content,
    check_pool_copy_coverage,
    check_survival_after_device_failure,
)

GOOD = "a" * 64
OLD = "b" * 64
BAD = "c" * 64


def file_item(sha=GOOD, size=10):
    return Item(kind="file", size=size, mode=0o644, mtime=1, xattrs=(), sha256=sha)


EXPECTED = {"live/one.bin": file_item(), "live/two.bin": file_item()}


def observed(share=None, pool=None, archive=None):
    return Observed(
        share=EXPECTED if share is None else share,
        pool={} if pool is None else pool,
        archive={"arch0": {}} if archive is None else archive,
    )


def invariants(violations):
    return sorted({v.invariant for v in violations})


def test_a_consistent_state_has_no_violation():
    assert check_invariants(EXPECTED, observed()) == []


def test_a_wrong_content_through_the_share_violates_a():
    share = {**EXPECTED, "live/one.bin": file_item(sha=BAD)}

    violations = check_invariants(EXPECTED, observed(share=share))

    assert Violation("a", "live/one.bin", violations[0].detail) in violations
    assert "a" in invariants(violations)


def test_a_wrong_size_through_the_share_violates_a():
    share = {**EXPECTED, "live/one.bin": file_item(size=3)}

    assert "a" in invariants(check_invariants(EXPECTED, observed(share=share)))


def test_a_missing_pool_copy_without_an_archive_copy_violates_b():
    pool_origin = {"live/one.bin"}

    violations = check_pool_copy_coverage(EXPECTED, observed(), pool_origin)

    assert [(v.invariant, v.path) for v in violations] == [("b", "live/one.bin")]


def test_a_missing_pool_copy_with_a_wrong_archive_copy_violates_b():
    obs = observed(archive={"arch0": {"live/one.bin": file_item(sha=BAD)}})

    assert invariants(check_pool_copy_coverage(EXPECTED, obs, {"live/one.bin"})) == ["b"]


def test_a_missing_pool_copy_with_an_expected_archive_copy_is_fine():
    obs = observed(archive={"arch0": {}, "arch1": {"live/one.bin": file_item()}})

    assert check_pool_copy_coverage(EXPECTED, obs, {"live/one.bin"}) == []


def test_a_present_pool_copy_needs_no_archive_copy():
    obs = observed(pool={"live/one.bin": file_item()})

    assert check_pool_copy_coverage(EXPECTED, obs, {"live/one.bin"}) == []


def test_files_that_never_lived_on_the_pool_are_not_checked_by_b():
    assert check_pool_copy_coverage(EXPECTED, observed(), pool_origin=set()) == []


def test_a_live_file_missing_from_the_share_violates_c():
    share = {"live/two.bin": file_item()}

    violations = check_no_loss_or_resurrection(EXPECTED, observed(share=share), deleted=set())

    assert [(v.invariant, v.path) for v in violations] == [("c", "live/one.bin")]


def test_a_client_deleted_file_that_reappears_in_the_share_violates_c():
    share = {**EXPECTED, "gone.bin": file_item()}

    violations = check_no_loss_or_resurrection(EXPECTED, observed(share=share), {"gone.bin"})

    assert [(v.invariant, v.path) for v in violations] == [("c", "gone.bin")]


def test_a_client_deleted_file_left_on_an_archive_branch_violates_c():
    obs = observed(archive={"arch0": {"gone.bin": file_item()}})

    violations = check_no_loss_or_resurrection(EXPECTED, obs, {"gone.bin"})

    assert [(v.invariant, v.path) for v in violations] == [("c", "gone.bin")]


def test_a_client_deleted_file_that_is_gone_everywhere_is_no_violation():
    obs = observed(pool={"live/one.bin": file_item()}, archive={"arch0": {"x.bin": file_item()}})

    assert check_no_loss_or_resurrection(EXPECTED, obs, {"gone.bin"}) == []


def test_partial_content_violates_d():
    share = {**EXPECTED, "live/one.bin": file_item(sha=BAD, size=4)}

    violations = check_no_partial_content(EXPECTED, share)

    assert [(v.invariant, v.path) for v in violations] == [("d", "live/one.bin")]


def test_complete_old_or_complete_new_content_is_not_partial():
    share = {**EXPECTED, "live/one.bin": file_item(sha=OLD)}
    acceptable = {"live/one.bin": {GOOD, OLD}}

    assert check_no_partial_content(EXPECTED, share, acceptable) == []


def test_temporary_names_are_ignored_by_d():
    share = {**EXPECTED, "live/.one.bin.tmp-123": file_item(sha=BAD, size=2)}

    assert check_no_partial_content(EXPECTED, share) == []


def test_a_live_file_lost_after_a_device_failure_violates_e():
    restored = {"live/one.bin": file_item()}

    violations = check_survival_after_device_failure(EXPECTED, restored)

    assert [(v.invariant, v.path) for v in violations] == [("e", "live/two.bin")]


def test_a_live_file_restored_with_other_bytes_violates_e():
    restored = {**EXPECTED, "live/two.bin": file_item(sha=BAD)}

    assert invariants(check_survival_after_device_failure(EXPECTED, restored)) == ["e"]


def test_full_survival_after_a_device_failure_is_fine():
    assert check_survival_after_device_failure(EXPECTED, dict(EXPECTED)) == []


def test_all_violations_are_reported_not_only_the_first():
    share = {"live/one.bin": file_item(sha=BAD), "gone.bin": file_item()}

    violations = check_invariants(
        EXPECTED, observed(share=share), deleted={"gone.bin"}, pool_origin={"live/two.bin"}
    )

    assert invariants(violations) == ["a", "b", "c", "d"]


def test_directories_in_manifests_are_not_files_and_do_not_matter():
    expected = {**EXPECTED, "live": Item("dir", None, 0o755, None, (), None)}
    share = {**EXPECTED, "live": Item("dir", None, 0o700, None, (), None)}

    assert check_invariants(expected, observed(share=share)) == []


def test_the_oracle_cannot_be_fed_mover_output_or_snapraid_status():
    with pytest.raises(TypeError):
        Observed(share={}, pool={}, archive={}, mover_log="all steps completed")
    with pytest.raises(TypeError):
        Observed(share={}, pool={}, archive={}, snapraid_status="in sync")
