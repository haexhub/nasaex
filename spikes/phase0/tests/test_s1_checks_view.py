"""Checks S1.1 and S1.11 (vm-tests/s1/checks_view.py): the decisions behind them, over
fabricated manifests, and the boot diagnostics of S1.11 against fake machines (when they are
captured, before what, and that they never change the outcome). Reading the share and
rebooting the server need the VMs."""

import json

import pytest
from s1.checks_view import archive_view_differences, check_reboot_view, reboot_failures, trash_leaks
from s1.context import Lab

from testlib.manifest import Difference, Item
from testlib.recorder import Recorder

FILE = Item("file", 4, 0o644, 1_700_000_000, (), "a" * 64)
DIR = Item("dir", None, 0o755, None, (), None)


def test_the_archive_view_ignores_pool_only_paths_of_the_share():
    expected = {"archive": DIR, "archive/a.txt": FILE}
    share = {**expected, "pool-only": DIR, "pool-only/b.txt": FILE}

    assert archive_view_differences(expected, share) == []


def test_the_archive_view_reports_a_missing_file_and_every_differing_field():
    expected = {"archive": DIR, "archive/a.txt": FILE, "archive/b.txt": FILE}
    synthetic = Item("file", 4, 0o755, 1_700_000_000, (), "b" * 64)
    share = {"archive": DIR, "archive/a.txt": synthetic}

    assert archive_view_differences(expected, share) == [
        Difference("archive/a.txt", "mismatch", "mode", 0o644, 0o755),
        Difference("archive/a.txt", "mismatch", "sha256", "a" * 64, "b" * 64),
        Difference("archive/b.txt", "missing"),
    ]


def test_a_trash_entry_or_a_trash_directory_in_a_listing_is_a_leak():
    entries = ["shared/x.txt.17.42", "old.bin.18.43"]
    listing = [
        "shared",
        "shared/x.txt",
        "shared/x.txt.17.42",
        "deep/old.bin.18.43",
        "trash",
        "trash/whatever",
        "trashcan/ok.txt",
    ]

    assert trash_leaks(listing, entries) == [
        "deep/old.bin.18.43",
        "shared/x.txt.17.42",
        "trash",
        "trash/whatever",
    ]


def test_a_listing_without_trash_content_has_no_leak():
    assert trash_leaks(["a", "a/b.txt"], ["a/b.txt.1.2"]) == []


def test_reboot_needs_every_unit_active_and_the_same_view():
    units = {"mnt-base.mount": True, "mnt-cache.mount": True, "samba-smbd.service": True}

    assert reboot_failures(units, []) == []
    assert reboot_failures({**units, "mnt-cache.mount": False}, []) == [
        "mnt-cache.mount is not active after the boot"
    ]
    assert reboot_failures(units, [Difference("a.txt", "missing")]) == [
        "view differs after the reboot: a.txt missing"
    ]


FSTAB = (
    "/dev/disk/by-label/spikepool /mnt/pool btrfs subvol=pool,nofail 0 0\n"
    "/dev/disk/by-label/parity0 /mnt/parity ext4 nofail 0 0\n"
)


class Machine:
    """A driver machine for check_reboot_view: every command goes to one shared log; `down`
    units are reported inactive, `mounted` is what findmnt lists, `raising` commands raise,
    and `settle` is the exception the wait for the empty job queue raises (or None)."""

    def __init__(self, name, log, down=(), mounted="/mnt/pool\n/mnt/parity\n", raising=()):
        self.name, self.log = name, log
        self.down, self.mounted, self.raising = down, mounted, raising
        self.settle = None

    def execute(self, command, timeout=None):
        self.log.append((self.name, command))
        if any(command.startswith(prefix) for prefix in self.raising):
            raise RuntimeError(f"capture broke: {command}")
        if command.startswith("systemctl is-active"):
            return (3 if command.split()[-1] in self.down else 0), ""
        if command.startswith("cat /etc/fstab"):
            return 0, FSTAB
        if command.startswith("findmnt"):
            return 0, self.mounted
        if command.startswith("python3"):
            return 0, "{}"
        return 0, ""

    def succeed(self, command, **_):
        return self.execute(command)[1]

    def shutdown(self):
        self.log.append((self.name, "shutdown"))

    def start(self):
        self.log.append((self.name, "start"))

    def wait_for_unit(self, unit, **_):
        self.log.append((self.name, f"wait {unit}"))

    def wait_until_succeeds(self, command, **_):
        self.log.append((self.name, command))
        if self.settle:
            raise self.settle


def reboot_lab(tmp_path, monkeypatch, **server):
    monkeypatch.chdir(tmp_path)
    log = []
    lab = Lab(Machine("server", log, **server), Machine("client", log), {}, Recorder())
    return lab, log


def status(tmp_path, name):
    return json.loads((tmp_path / "status" / f"{name}.json").read_text())


def test_a_reboot_with_a_judged_unit_down_captures_diagnostics_and_fails_as_before(
    tmp_path, monkeypatch
):
    lab, log = reboot_lab(tmp_path, monkeypatch, down=("mnt-base.mount",), mounted="/\n")

    with pytest.raises(AssertionError) as failed:
        check_reboot_view(lab)

    assert str(failed.value) == "mnt-base.mount is not active after the boot"
    diag = status(tmp_path, "S1.11-boot-diagnostics")
    assert diag["missing_mounts"] == ["/mnt/parity", "/mnt/pool"]
    assert any(c.startswith("journalctl -b") for _, c in log)
    assert status(tmp_path, "S1.11-reboot-view")["units_active"]["mnt-base.mount"] is False


def test_a_missing_by_label_mount_is_captured_before_the_clients_mount_again(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch, mounted="/mnt/pool\n")

    check_reboot_view(lab)

    assert status(tmp_path, "S1.11-boot-diagnostics")["missing_mounts"] == ["/mnt/parity"]
    journal = next(i for i, (_, c) in enumerate(log) if c.startswith("journalctl -b"))
    remount = next(i for i, (m, c) in enumerate(log) if m == "client" and "mount -t cifs" in c)
    assert journal < remount


def test_a_complete_reboot_writes_no_diagnostics(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch)

    check_reboot_view(lab)

    assert not (tmp_path / "status" / "S1.11-boot-diagnostics.json").exists()
    assert not any(c.startswith("journalctl") for _, c in log)


def test_a_broken_capture_never_changes_the_outcome_of_the_check(tmp_path, monkeypatch):
    lab, _ = reboot_lab(
        tmp_path, monkeypatch, down=("mnt-cache.mount",), raising=("cat", "findmnt", "journalctl")
    )
    monkeypatch.setattr(
        "s1.boot_diag.host_state", lambda *a, **k: (_ for _ in ()).throw(MemoryError("host"))
    )

    with pytest.raises(AssertionError) as failed:
        check_reboot_view(lab)

    assert str(failed.value) == "mnt-cache.mount is not active after the boot"


def test_a_boot_that_never_settles_is_captured_and_raises_its_own_error(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch)
    lab.server.settle = TimeoutError("jobs still queued")

    with pytest.raises(TimeoutError, match="jobs still queued"):
        check_reboot_view(lab)

    assert status(tmp_path, "S1.11-boot-diagnostics")["settled"] is False
