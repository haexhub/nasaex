"""Check S1.10 (vm-tests/s1/checks_mover_view.py): the decision behind it over fabricated
observations. Holding the mover at its hooks and reading through the share need the VMs."""

from s1.checks_mover_view import mover_view_failures

EXPECTED = {"size": 10, "sha256": "e" * 64}
SEEN = {"visible": True, "size": 10, "sha256": "e" * 64}
GOOD = {
    "steps": {"mid:M1:renamed": SEEN, "mid:M5:unlinked": SEEN},
    "expected": EXPECTED,
    "reader": {"size": 10, "sha256": "e" * 64},
    "mover_status": 0,
    "holders": ["arch1"],
    "consistent_after_s": 1.5,
    "bound_s": 30,
    "refresh": None,
    "attributes_kept": True,
}


def test_a_move_seen_complete_at_every_point_and_consistent_in_time_passes():
    assert mover_view_failures(**GOOD) == []


def test_a_missing_or_partial_file_at_a_point_fails_with_the_point_named():
    steps = {
        "mid:M1:renamed": {"visible": False},
        "mid:M5:unlinked": {"visible": True, "size": 4, "sha256": "p" * 64},
        "mid:M5:item-1-done": {"error": "OSError: stale file handle"},
    }

    assert mover_view_failures(**{**GOOD, "steps": steps}) == [
        "mid:M1:renamed: the file is not visible through the share",
        "mid:M5:unlinked: the file is partial or differs (4 bytes)",
        "mid:M5:item-1-done: the file could not be read: OSError: stale file handle",
    ]


def test_a_broken_reader_a_failed_mover_or_a_file_left_on_the_pool_fails():
    observed = {
        **GOOD,
        "reader": {"size": 5, "sha256": "x" * 64},
        "mover_status": 1,
        "holders": ["pool", "arch1"],
        "attributes_kept": False,
    }

    assert mover_view_failures(**observed) == [
        "the open reader read 5 bytes with another hash",
        "the mover exited with status 1",
        "after the move the file lies on ['pool', 'arch1']",
        "the attributes differ after the move",
    ]


def test_an_inconsistent_view_needs_a_verified_refresh_procedure():
    late = {**GOOD, "consistent_after_s": None}

    assert mover_view_failures(**late) == [
        "the view differs after 30 s and no refresh procedure is verified"
    ]
    assert mover_view_failures(**{**late, "refresh": {"verified": True}}) == []
    assert mover_view_failures(**{**late, "refresh": {"verified": False}}) == [
        "the view differs after 30 s and no refresh procedure is verified"
    ]
    assert mover_view_failures(**{**GOOD, "consistent_after_s": 31.0}) == [
        "the view differs after 30 s and no refresh procedure is verified"
    ]
