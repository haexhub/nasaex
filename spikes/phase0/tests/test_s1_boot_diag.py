"""Boot diagnostics of S1.11 (vm-tests/s1/boot_diag.py): which reboot is captured, what is
selected and filtered, and that a capture never raises. The guest is a fake machine that answers
commands by prefix; the real capture needs the VMs."""

import pytest
from s1.boot_diag import (
    btrfs_lines,
    capture_if_incomplete,
    failed_units,
    host_state,
    journal_window,
    label_mounts,
    missing_mounts,
    unit_name,
    unit_run,
    units_to_inspect,
)

FSTAB = """# This is a generated file.  Do not modify!
/dev/disk/by-label/nixos / ext4 x-initrd.mount 0 1
/dev/disk/by-label/spikepool /mnt/pool btrfs subvol=pool,nofail,x-systemd.device-timeout=10s 0 0
/dev/disk/by-label/parity0 /mnt/parity ext4 nofail,x-systemd.device-timeout=10s 0 0
/dev/disk/by-label/arch0 /mnt/archive/arch0 btrfs subvol=data,nofail 0 0
/dev/disk/by-label/arch0 /mnt/archive-content/arch0 btrfs subvol=content,nofail 0 0
/dev/vdb1 /boot vfat defaults 0 2
tmpfs /tmp tmpfs mode=1777 0 0
"""
SPIKE_MOUNTS = {
    "/mnt/pool": "spikepool",
    "/mnt/parity": "parity0",
    "/mnt/archive/arch0": "arch0",
    "/mnt/archive-content/arch0": "arch0",
}
ALL_UP = {"mnt-base.mount": True, "mnt-cache.mount": True, "samba-smbd.service": True}


def test_label_mounts_are_the_by_label_lines_of_fstab_except_the_root_filesystem():
    assert label_mounts(FSTAB) == SPIKE_MOUNTS


def test_missing_mounts_are_the_expected_mount_points_findmnt_does_not_list():
    targets = "/\n/nix/store\n/mnt/pool\n/mnt/archive/arch0\n"

    assert missing_mounts(SPIKE_MOUNTS, targets) == ["/mnt/archive-content/arch0", "/mnt/parity"]
    assert missing_mounts(SPIKE_MOUNTS, "\n".join(SPIKE_MOUNTS)) == []


SHOW_RAN = """LoadState=loaded
ActiveState=active
SubState=exited
Result=success
ExecMainStatus=0
ExecMainStartTimestampMonotonic=5085064
ExecMainExitTimestampMonotonic=6027691
"""


def test_the_run_of_the_retrigger_unit_is_its_result_exit_status_and_run_time():
    # Task T043b: what S1.11 records of tables.reboot.retrigger_unit per boot (systemctl show).
    assert unit_run(SHOW_RAN) == {
        "load_state": "loaded",
        "active_state": "active",
        "sub_state": "exited",
        "result": "success",
        "exit_status": 0,
        "run_s": 0.943,
    }


def test_a_failed_or_absent_retrigger_unit_is_recorded_as_it_is():
    failed = SHOW_RAN.replace("Result=success", "Result=exit-code").replace(
        "ExecMainStatus=0", "ExecMainStatus=1"
    )
    absent = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\n" + (
        "ExecMainStatus=0\nExecMainStartTimestampMonotonic=0\nExecMainExitTimestampMonotonic=0\n"
    )

    assert (unit_run(failed)["result"], unit_run(failed)["exit_status"]) == ("exit-code", 1)
    assert unit_run(absent)["load_state"] == "not-found"
    assert unit_run(absent)["run_s"] is None
    assert unit_run("")["result"] is None


def test_unit_names_are_escaped_like_systemd_escape_path():
    assert unit_name("/dev/disk/by-label/spikepool", "device") == (
        "dev-disk-by\\x2dlabel-spikepool.device"
    )
    assert unit_name("/mnt/archive-content/arch0", "mount") == "mnt-archive\\x2dcontent-arch0.mount"
    assert unit_name("/mnt/pool/", "mount") == "mnt-pool.mount"


def test_failed_units_read_the_unit_column_with_or_without_the_bullet():
    listing = (
        "● mnt-pool.mount loaded failed failed /mnt/pool\n"
        "mnt-base.mount loaded failed failed Base pool\n"
        "\n"
    )

    assert failed_units(listing) == ["mnt-pool.mount", "mnt-base.mount"]
    assert failed_units("") == []


def test_units_to_inspect_are_every_by_label_device_and_mount_plus_the_failed_units():
    units = units_to_inspect(SPIKE_MOUNTS, ["mnt-base.mount", "mnt-pool.mount"])

    assert units == sorted(
        {
            "dev-disk-by\\x2dlabel-spikepool.device",
            "dev-disk-by\\x2dlabel-parity0.device",
            "dev-disk-by\\x2dlabel-arch0.device",
            "mnt-pool.mount",
            "mnt-parity.mount",
            "mnt-archive-arch0.mount",
            "mnt-archive\\x2dcontent-arch0.mount",
            "mnt-base.mount",
        }
    )


JOURNAL = """-- Boot 0123 --
[    0.000000] server kernel: Linux version 6.18.56
[    4.674970] server systemd[1]: Expecting device /dev/disk/by-label/arch0...
[   14.676000] server systemd[1]: Job dev-disk-by\\x2dlabel-arch0.device/start timed out.
[   20.100000] server smbd[812]: started
[   61.000000] server smbd[812]: late smbd line
[   61.500000] server systemd[1]: Started late unit.
[   62.000000] server kernel: BTRFS: device label spikepool devid 1 scanned by udev
[   63.000000] server systemd-udevd[300]: vdb: late event
[   64.000000] server (udev-worker)[363]: vdc: worker line
[   65.000000] server nscd[90]: mentions kernel: in its message
"""


def test_the_journal_keeps_everything_of_the_first_minute_and_then_kernel_systemd_udev():
    kept = journal_window(JOURNAL).splitlines()

    assert kept[:5] == JOURNAL.splitlines()[:5]
    assert kept[5:] == [
        "[   61.500000] server systemd[1]: Started late unit.",
        "[   62.000000] server kernel: BTRFS: device label spikepool devid 1 scanned by udev",
        "[   63.000000] server systemd-udevd[300]: vdb: late event",
        "[   64.000000] server (udev-worker)[363]: vdc: worker line",
    ]


def test_btrfs_lines_of_dmesg():
    dmesg = "[ 1.0] virtio_blk virtio2: [vdb] 4194304 512-byte logical blocks\n" + (
        "[ 5.2] BTRFS: device label spikepool devid 2 transid 9 /dev/vdc scanned by (udev-worker)\n"
        "[ 6.0] btrfs: something lower case\n"
    )

    assert btrfs_lines(dmesg) == dmesg.splitlines()[1:]


def test_host_state_reads_io_pressure_and_load_and_records_an_unreadable_file():
    def read(name):
        if name == "loadavg":
            raise OSError("no such file")
        return f"content of {name}\n"

    state = host_state(read)

    assert state["pressure/io"] == "content of pressure/io\n"
    assert "no such file" in state["loadavg"]
    assert isinstance(state["time"], float)


class Guest:
    """A guest that answers commands by prefix; `raising` commands raise instead."""

    def __init__(self, outputs, raising=()):
        self.outputs = outputs
        self.raising = raising
        self.commands = []

    def execute(self, command, timeout=None):
        self.commands.append(command)
        if any(command.startswith(prefix) for prefix in self.raising):
            raise RuntimeError(f"guest gone: {command}")
        for prefix, answer in self.outputs.items():
            if command.startswith(prefix):
                return answer
        return 0, ""


def guest_with(findmnt, links="spikepool\nparity0\narch0\n", raising=()):
    return Guest(
        {
            "cat /etc/fstab": (0, FSTAB),
            "findmnt": (0, findmnt),
            "ls -1 /dev/disk/by-label": (0, links),
            "lsblk -dnp -o NAME": (0, "/dev/vda\n/dev/vdb\n"),
            "systemctl list-units --failed": (0, "● mnt-pool.mount loaded failed failed x\n"),
            "journalctl -b -o": (0, JOURNAL),
            "dmesg": (0, "[ 5.2] BTRFS: device label spikepool devid 2\n[ 1.0] other\n"),
        },
        raising,
    )


def no_host(name):
    return ""


def test_a_complete_boot_is_not_captured_and_only_read():
    guest = guest_with("\n".join(SPIKE_MOUNTS))

    assert capture_if_incomplete(guest, ALL_UP, read_host=no_host) is None
    assert guest.commands == ["cat /etc/fstab 2>&1", "findmnt -rn -o TARGET 2>&1"]


def test_a_missing_by_label_mount_is_captured_even_with_every_judged_unit_active():
    guest = guest_with("/mnt/pool\n/mnt/archive/arch0\n/mnt/archive-content/arch0\n")

    data = capture_if_incomplete(guest, ALL_UP, read_host=no_host)

    assert data["missing_mounts"] == ["/mnt/parity"]
    assert data["errors"] == []
    assert data["journal_first_60s_then_kernel_systemd_udev"] == journal_window(JOURNAL)
    assert data["dmesg_btrfs"] == ["[ 5.2] BTRFS: device label spikepool devid 2"]
    commands = guest.commands
    for link in ("spikepool", "parity0", "arch0"):
        assert f"udevadm info --query=all --name=/dev/disk/by-label/{link} 2>&1" in commands
    assert "udevadm info --query=all --name=/dev/vdb 2>&1" in commands
    status = next(c for c in commands if c.startswith("systemctl status"))
    assert "'dev-disk-by\\x2dlabel-parity0.device'" in status and "mnt-pool.mount" in status
    assert "ls -l --time-style=full-iso /dev/disk/by-label/ 2>&1" in commands
    for wanted in ("lsblk -f", "/proc/pressure/io", "/proc/pressure/memory", "uptime"):
        assert any(wanted in c for c in commands)
    assert all(not c.startswith(("mount", "umount", "systemctl start")) for c in commands)


def test_a_judged_unit_down_is_captured_and_the_host_state_is_recorded():
    guest = guest_with("\n".join(SPIKE_MOUNTS))

    data = capture_if_incomplete(
        guest, {**ALL_UP, "mnt-base.mount": False}, read_host=lambda n: n, host_at_start={"x": 1}
    )

    assert data["units_active"]["mnt-base.mount"] is False
    assert data["host_at_capture"]["pressure/io"] == "pressure/io"
    assert data["host_at_capture"]["loadavg"] == "loadavg"
    assert data["host_at_start"] == {"x": 1}


def test_an_unsettled_boot_is_always_captured():
    guest = guest_with("\n".join(SPIKE_MOUNTS))

    data = capture_if_incomplete(guest, {}, settled=False, read_host=no_host)

    assert data["settled"] is False
    assert any(c.startswith("journalctl -b -o") for c in guest.commands)


def test_a_guest_that_fails_every_command_never_makes_the_capture_raise():
    guest = guest_with("", raising=("",))

    data = capture_if_incomplete(guest, {**ALL_UP, "mnt-cache.mount": False}, read_host=no_host)

    assert data["errors"]
    assert all("guest gone" in error for error in data["errors"])


def test_a_capture_that_fails_while_deciding_still_reports_instead_of_raising():
    guest = guest_with("", raising=("findmnt",))

    data = capture_if_incomplete(guest, ALL_UP, read_host=no_host)

    assert data is not None
    assert any("guest gone" in error for error in data["errors"])


@pytest.mark.parametrize("links", ["", "ls: cannot access '/dev/disk/by-label/'\n"])
def test_no_link_or_a_failed_listing_queries_no_link(links):
    guest = guest_with("/", links=links)
    if links:
        guest.outputs["ls -1 /dev/disk/by-label"] = (2, links)

    capture_if_incomplete(guest, ALL_UP, read_host=no_host)

    assert not any("--name=/dev/disk/by-label/" in c for c in guest.commands)
