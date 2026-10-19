"""Checks S1.1 and S1.11 (vm-tests/s1/checks_view.py): the decisions behind them, over
fabricated manifests, and the boot diagnostics of S1.11 against fake machines (when they are
captured, before what, and that they never change the outcome). Reading the share and
rebooting the server need the VMs."""

import json

import pytest
from s1.checks_view import (
    archive_view_differences,
    boot_failures,
    check_reboot_view,
    reboot_failures,
    trash_leaks,
)
from s1.context import Lab

from testlib.manifest import Difference, Item, to_json
from testlib.recorder import Recorder

FILE = Item("file", 4, 0o644, 1_700_000_000, (), "a" * 64)
DIR = Item("dir", None, 0o755, None, (), None)


def test_the_archive_view_ignores_pool_only_paths_of_the_share():
    expected = {"archive": DIR, "archive/a.txt": FILE}
    share = {**expected, "pool-only": DIR, "pool-only/b.txt": FILE}

    assert archive_view_differences(expected, share) == []


def test_the_archive_view_reports_a_missing_file_and_every_judged_differing_field():
    # The mode mount.cifs reports is recorded, not judged (task T043a).
    expected = {"archive": DIR, "archive/a.txt": FILE, "archive/b.txt": FILE}
    synthetic = Item("file", 4, 0o755, 1_700_000_000, (), "b" * 64)
    share = {"archive": DIR, "archive/a.txt": synthetic}

    assert archive_view_differences(expected, share) == [
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


def boot(index=1, down=(), missing=(), settled=True):
    units = ("mnt-base.mount", "mnt-cache.mount", "samba-smbd.service")
    return {
        "index": index,
        "settled": settled,
        "units_active": {unit: unit not in down for unit in units},
        "missing_mounts": list(missing),
    }


def test_a_boot_needs_every_judged_unit_active_and_every_by_label_mount_mounted():
    # Task T043b: every by-label mount of /etc/fstab is judged after each boot (proposed in
    # spec Clarifications 2026-10-19, S1.11 reboots).
    assert boot_failures(boot(3)) == []
    assert boot_failures(boot(3, down=("mnt-cache.mount",), missing=("/mnt/parity",))) == [
        "reboot 3: mnt-cache.mount is not active after the boot",
        "reboot 3: /mnt/parity is not mounted after the boot",
    ]
    assert boot_failures({"index": 4, "settled": False}) == ["reboot 4: the boot never settled"]


def test_the_reboots_need_complete_boots_and_the_same_view_after_the_last():
    boots = [boot(1), boot(2, missing=("/mnt/archive/arch1",))]

    assert reboot_failures([boot(1), boot(2)], []) == []
    assert reboot_failures(boots, [Difference("a.txt", "missing")]) == [
        "reboot 2: /mnt/archive/arch1 is not mounted after the boot",
        "view differs after the last reboot: a.txt missing",
    ]


FSTAB = (
    "/dev/disk/by-label/spikepool /mnt/pool btrfs subvol=pool,nofail 0 0\n"
    "/dev/disk/by-label/parity0 /mnt/parity ext4 nofail 0 0\n"
)
ALL_MOUNTED = "/mnt/pool\n/mnt/parity\n"
UNIT = "pool-retrigger.service"
UNIT_TEXT = "# /etc/systemd/system/pool-retrigger.service\n[Unit]\nDefaultDependencies=no\n"
SHOW = (
    "LoadState=loaded\nActiveState=active\nSubState=exited\nResult={result}\n"
    "ExecMainStatus={status}\nExecMainStartTimestampMonotonic=5085064\n"
    "ExecMainExitTimestampMonotonic=6027691\n"
)
BEFORE = to_json({"a.txt": FILE})


class Machine:
    """A driver machine for check_reboot_view: every command goes to one shared log. Boots are
    counted from 1 at each start; `down` units are reported inactive, `mounted` is what findmnt
    lists (both per boot number when a dict, else for every boot), `raising` commands raise,
    `settle` is the exception the wait for the empty job queue raises (or None), `unit_result`
    is the Result and ExecMainStatus of the retrigger unit, and `manifests` are the client
    manifests in order (the last one repeats)."""

    def __init__(
        self,
        name,
        log,
        down=(),
        mounted=ALL_MOUNTED,
        raising=(),
        unit_result=("success", 0),
        manifests=(BEFORE,),
    ):
        self.name, self.log = name, log
        self.down, self.mounted, self.raising = down, mounted, raising
        self.unit_result, self.manifests = unit_result, list(manifests)
        self.settle = None
        self.boots = 0

    def _now(self, value, default):
        return value.get(self.boots, default) if isinstance(value, dict) else value

    def execute(self, command, timeout=None):
        self.log.append((self.name, command))
        if any(command.startswith(prefix) for prefix in self.raising):
            raise RuntimeError(f"capture broke: {command}")
        if command.startswith("systemctl is-active"):
            return (3 if command.split()[-1] in self._now(self.down, ()) else 0), ""
        if command.startswith("cat /etc/fstab"):
            return 0, FSTAB
        if command.startswith("findmnt"):
            return 0, self._now(self.mounted, ALL_MOUNTED)
        if command.startswith("systemctl cat"):
            return 0, UNIT_TEXT
        if command.startswith("systemctl show"):
            result, status = self.unit_result
            return 0, SHOW.format(result=result, status=status)
        if command.startswith("python3"):
            return 0, self.manifests.pop(0) if len(self.manifests) > 1 else self.manifests[0]
        return 0, ""

    def succeed(self, command, **_):
        return self.execute(command)[1]

    def shutdown(self):
        self.log.append((self.name, "shutdown"))

    def start(self):
        self.boots += 1
        self.log.append((self.name, "start"))

    def wait_for_unit(self, unit, **_):
        self.log.append((self.name, f"wait {unit}"))

    def wait_until_succeeds(self, command, **_):
        self.log.append((self.name, command))
        if self.settle:
            raise self.settle


def reboot_lab(tmp_path, monkeypatch, count=1, client=None, **server):
    monkeypatch.chdir(tmp_path)
    log = []
    tables = {"reboot": {"count": count, "retrigger_unit": UNIT}}
    lab = Lab(
        Machine("server", log, **server),
        Machine("client", log, **(client or {})),
        {},
        Recorder(),
        tables=tables,
    )
    return lab, log


def status(tmp_path, name):
    return json.loads((tmp_path / "status" / f"{name}.json").read_text())


def reboots_done(log):
    return sum(1 for machine, command in log if (machine, command) == ("server", "shutdown"))


def test_twenty_complete_boots_with_the_same_view_pass_and_are_recorded(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch, count=20)

    check_reboot_view(lab)

    assert reboots_done(log) == 20
    view = status(tmp_path, "S1.11-reboot-view")
    assert [b["index"] for b in view["reboots"]] == list(range(1, 21))
    first = view["reboots"][0]
    assert first["units_active"] == {
        "mnt-base.mount": True,
        "mnt-cache.mount": True,
        "samba-smbd.service": True,
    }
    assert first["missing_mounts"] == []
    assert first["retrigger_unit"]["result"] == "success"
    assert first["retrigger_unit"]["exit_status"] == 0
    assert first["retrigger_unit"]["run_s"] == 0.943
    assert {"shutdown_s", "boot_s"} <= set(first)
    assert lab.required_settings["reboot"] == {
        "retrigger_unit": UNIT,
        "unit_text": UNIT_TEXT,
        "count": 20,
        "reboots_completed": 20,
        "found_by": "S1.11",
    }
    # The clients mount again only after the last reboot; nothing is started on the server.
    mounts = [i for i, (m, c) in enumerate(log) if m == "client" and "mount -t cifs" in c]
    last_start = max(i for i, entry in enumerate(log) if entry == ("server", "start"))
    assert mounts and min(mounts) > last_start
    assert not any(m == "server" and c.startswith("systemctl start") for m, c in log)
    assert not (tmp_path / "status" / "S1.11-boot-diagnostics.json").exists()


def test_a_unit_down_at_reboot_k_fails_captures_and_runs_no_further_reboot(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch, count=20, down={3: ("mnt-base.mount",)})

    with pytest.raises(AssertionError) as failed:
        check_reboot_view(lab)

    assert str(failed.value) == "reboot 3: mnt-base.mount is not active after the boot"
    assert reboots_done(log) == 3
    assert status(tmp_path, "S1.11-boot-diagnostics")["reboot"] == 3
    assert [b["index"] for b in status(tmp_path, "S1.11-reboot-view")["reboots"]] == [1, 2, 3]
    assert lab.required_settings["reboot"]["reboots_completed"] == 2
    # Not every share unit came back, so the clients do not mount again.
    assert not any(m == "client" and "mount -t cifs" in c for m, c in log)


def test_a_missing_by_label_mount_fails_before_clients_mount_again(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch, count=20, mounted={2: "/mnt/pool\n"})

    with pytest.raises(AssertionError) as failed:
        check_reboot_view(lab)

    assert str(failed.value) == "reboot 2: /mnt/parity is not mounted after the boot"
    assert reboots_done(log) == 2
    assert status(tmp_path, "S1.11-boot-diagnostics")["missing_mounts"] == ["/mnt/parity"]
    assert not any(m == "client" and "mount -t cifs" in c for m, c in log)
    assert any(c.startswith("journalctl -b") for _, c in log)


def test_a_view_difference_after_the_last_reboot_fails(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch, count=20, client={"manifests": (BEFORE, "{}")})

    with pytest.raises(AssertionError) as failed:
        check_reboot_view(lab)

    assert str(failed.value) == "view differs after the last reboot: a.txt missing"
    assert reboots_done(log) == 20
    assert status(tmp_path, "S1.11-reboot-view")["differences"] == "a.txt missing"


def test_a_failed_retrigger_unit_is_recorded_and_not_judged(tmp_path, monkeypatch):
    lab, _ = reboot_lab(tmp_path, monkeypatch, count=2, unit_result=("exit-code", 1))

    check_reboot_view(lab)

    unit = status(tmp_path, "S1.11-reboot-view")["reboots"][1]["retrigger_unit"]
    assert (unit["result"], unit["exit_status"]) == ("exit-code", 1)


def test_a_complete_reboot_writes_no_diagnostics(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch)

    check_reboot_view(lab)

    assert not (tmp_path / "status" / "S1.11-boot-diagnostics.json").exists()
    assert not any(c.startswith("journalctl") for _, c in log)


def test_a_broken_capture_never_changes_the_outcome_of_the_check(tmp_path, monkeypatch):
    lab, _ = reboot_lab(
        tmp_path,
        monkeypatch,
        down=("mnt-cache.mount",),
        raising=("journalctl", "udevadm", "lsblk", "ls "),
    )
    monkeypatch.setattr(
        "s1.boot_diag.host_state", lambda *a, **k: (_ for _ in ()).throw(MemoryError("host"))
    )

    with pytest.raises(AssertionError) as failed:
        check_reboot_view(lab)

    assert str(failed.value) == "reboot 1: mnt-cache.mount is not active after the boot"


def test_a_boot_that_never_settles_is_captured_and_raises_its_own_error(tmp_path, monkeypatch):
    lab, log = reboot_lab(tmp_path, monkeypatch, count=20)
    lab.server.settle = TimeoutError("jobs still queued")

    with pytest.raises(TimeoutError, match="jobs still queued"):
        check_reboot_view(lab)

    assert reboots_done(log) == 1
    assert status(tmp_path, "S1.11-boot-diagnostics")["settled"] is False
    view = status(tmp_path, "S1.11-reboot-view")
    assert view["reboots"][0]["settled"] is False
    assert lab.required_settings["reboot"]["reboots_completed"] == 0
