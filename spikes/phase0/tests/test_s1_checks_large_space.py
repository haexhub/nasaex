"""Checks S1.8 and S1.9 (vm-tests/s1/checks_large_space.py): the decisions behind S1.9 over
fabricated observations. Large files, the filler and the client writes need the VMs."""

from s1.checks_large_space import space_failures

TABLE = {"errno": "ENOSPC", "smb_status": "NT_STATUS_DISK_FULL", "write_bytes": 1000}
CLEAN = {
    "errnos": {"create": "ENOSPC", "append": "ENOSPC"},
    "smbclient_output": "putting file ... NT_STATUS_DISK_FULL closing remote file",
    "changed_archive_branches": [],
    "new_sizes": {"s1.9/new.bin": None, "s1.9/new-smbclient.bin": 1000},
    "append_head_intact": True,
}


def test_a_clean_out_of_space_error_everywhere_passes():
    assert space_failures(TABLE, **CLEAN) == []


def test_another_errno_a_missing_smb_status_or_a_written_archive_fails():
    observed = {
        **CLEAN,
        "errnos": {"create": None, "append": "EIO"},
        "smbclient_output": "putting file ... NT_STATUS_OK",
        "changed_archive_branches": ["arch2"],
    }

    assert space_failures(TABLE, **observed) == [
        "create returned errno None, expected ENOSPC",
        "append returned errno EIO, expected ENOSPC",
        "smbclient did not report NT_STATUS_DISK_FULL",
        "archive branch arch2 changed",
    ]


def test_a_shorter_file_at_a_new_path_or_a_damaged_append_target_fails():
    observed = {
        **CLEAN,
        "new_sizes": {"s1.9/new.bin": 0, "s1.9/new-smbclient.bin": 999},
        "append_head_intact": False,
    }

    assert space_failures(TABLE, **observed) == [
        "s1.9/new.bin is visible with 0 of 1000 bytes",
        "s1.9/new-smbclient.bin is visible with 999 of 1000 bytes",
        "the appended file no longer begins with its original bytes",
    ]
