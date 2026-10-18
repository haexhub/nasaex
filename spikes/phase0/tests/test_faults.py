"""Fault kinds: every trigger is host-side and fires at a named point, never after a sleep.
The machine is a recording fake; the real behaviour of the kinds was observed by the fault
probe (vm-tests/probes)."""

import stat
from pathlib import Path

import pytest

from testlib.faults import (
    HOOK_DIR,
    FaultEvent,
    arm,
    corrupt,
    device_failed_at_boot,
    device_ioerror,
    device_unplug,
    enospc,
    fire,
    kill,
    parse_drive,
    poweroff,
    release,
    sync_limited,
)

INFO_BLOCK = """drive4 (#block134): /state/vm-state-server/empty2.qcow2 (qcow2)
    Attached to:      /machine/peripheral/disk-arch0/virtio-backend
    Cache mode:       writeback

drive5 (#block245): /state/vm-state-server/empty3.qcow2 (qcow2)
    Attached to:      /machine/peripheral/disk-arch1/virtio-backend
    Cache mode:       writeback
"""


class FakeMachine:
    """Records every call; `sleep` must never be used to trigger a fault."""

    def __init__(self, state_dir=None, booted=True, outputs=None):
        self.calls: list[tuple[str, str]] = []
        self.state_dir = state_dir
        self.booted = booted
        self.outputs = outputs or {}

    def _record(self, what, command):
        self.calls.append((what, command))
        return self.outputs.get(command, "")

    def succeed(self, *commands, **_):
        return "".join(self._record("succeed", c) for c in commands)

    def execute(self, command, **_):
        return 0, self._record("execute", command)

    def wait_until_fails(self, command, **_):
        return self._record("wait_until_fails", command)

    def wait_for_file(self, path, **_):
        self._record("wait_for_file", path)

    def send_monitor_command(self, command):
        return self._record("monitor", command) or self.outputs.get("info block", "")

    def crash(self):
        self._record("crash", "")
        self.booted = False

    def sleep(self, _seconds):
        raise AssertionError("a fault must never be triggered by a delay")

    def commands(self, what):
        return [c for w, c in self.calls if w == what]


def test_kill_sends_sigkill_to_the_named_process():
    machine = FakeMachine()

    event = kill(machine, "mover-ref", point="post:M5")

    assert machine.commands("succeed") == ["pkill -9 -f mover-ref"]
    assert event == FaultEvent("kill", "post:M5", "mover-ref")


def test_poweroff_is_machine_crash_and_never_syncs():
    machine = FakeMachine()

    event = poweroff(machine, point="mid:M1:renamed")

    assert machine.commands("crash") == [""]
    assert not any("sync" in c for _, c in machine.calls)
    assert event.kind == "poweroff" and event.point == "mid:M1:renamed"


def test_device_unplug_removes_the_disk_by_serial_and_waits_for_the_guest():
    machine = FakeMachine()

    event = device_unplug(machine, "pool1", point="disk:pool1:fail")

    assert machine.commands("monitor") == ["device_del disk-pool1"]
    assert machine.commands("wait_until_fails") == ["test -e /dev/disk/by-id/virtio-pool1"]
    assert event.kind == "device-unplug"


def test_parse_drive_finds_the_drive_attached_as_a_disk():
    assert parse_drive(INFO_BLOCK, "arch1") == "drive5"
    assert parse_drive(INFO_BLOCK, "arch0") == "drive4"
    with pytest.raises(LookupError, match="arch9"):
        parse_drive(INFO_BLOCK, "arch9")


def test_device_ioerror_deletes_the_backend_of_the_drive_of_that_serial():
    machine = FakeMachine(outputs={"info block": INFO_BLOCK})

    event = device_ioerror(machine, "arch1", point="disk:arch1:fail")

    assert machine.commands("monitor") == ["info block", "drive_del drive5"]
    assert event.kind == "device-ioerror" and event.detail == "drive5"


def fake_qemu_img(tmp_path):
    script = tmp_path / "qemu-img"
    script.write_text('#!/bin/sh\n# create -f qcow2 <file> <size>\necho "$4:$5" > "$4"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_device_failed_at_boot_swaps_the_backing_image_for_an_empty_one(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "empty2.qcow2").write_text("old data")
    machine = FakeMachine(state_dir=state, booted=False)
    disk = {"serial": "arch0", "sizeMiB": 640, "image": "empty2.qcow2"}

    event = device_failed_at_boot(
        machine, disk, fake_qemu_img(tmp_path), point="boot:after-failure"
    )

    assert (state / "empty2.qcow2").read_text().strip() == f"{state / 'empty2.qcow2'}:640M"
    assert event.kind == "device-failed-at-boot"


def test_device_failed_at_boot_refuses_while_the_machine_runs(tmp_path):
    machine = FakeMachine(state_dir=tmp_path, booted=True)

    with pytest.raises(RuntimeError, match="running"):
        device_failed_at_boot(machine, {"serial": "a", "sizeMiB": 1, "image": "x"}, Path("true"))


def test_corrupt_overwrites_defined_bytes_in_place():
    machine = FakeMachine()

    event = corrupt(machine, "/mnt/archive/arch0/f.bin", offset=4096, length=8, point="fault:M2")

    assert machine.commands("succeed") == [
        "head -c 8 /dev/zero | tr '\\0' '\\245' | "
        "dd of=/mnt/archive/arch0/f.bin bs=1 seek=4096 conv=notrunc"
    ]
    assert event.kind == "corrupt"


DF = "df --output=avail -B1 /mnt/pool | tail -n1"


class FillingMachine(FakeMachine):
    """Answers each `df` with the next of the given free byte counts (the last one repeats)."""

    def __init__(self, free):
        super().__init__()
        self.free = list(free)

    def succeed(self, *commands, **_):
        out = super().succeed(*commands)
        if commands == (DF,):
            return f"{self.free.pop(0) if len(self.free) > 1 else self.free[0]}\n"
        return out


def test_enospc_fills_the_filesystem_leaving_a_defined_number_of_bytes():
    machine = FillingMachine([1000000, 4096])

    event = enospc(machine, "/mnt/pool", leave_bytes=4096, point="pool:free:4096")

    assert "fallocate -o 0 -l 995904 /mnt/pool/.spike-filler" in machine.commands("succeed")
    assert event.kind == "enospc"
    assert "4096 bytes free" in event.detail


def test_enospc_settles_pending_frees_before_every_measurement():
    # btrfs releases the extents of a deleted file at the next transaction commit; a free
    # count read before it is stale (S1 shakedown, run 1: the pool showed 4 GiB free after a
    # filler sized from that count).
    machine = FillingMachine([1000000, 4096])

    enospc(machine, "/mnt/pool", leave_bytes=4096)

    commands = machine.commands("succeed")
    measurements = [i for i, c in enumerate(commands) if c == DF]
    assert len(measurements) == 2
    assert all(commands[i - 1] == "sync -f /mnt/pool" for i in measurements)


def test_enospc_extends_the_filler_when_space_appears_after_filling():
    machine = FillingMachine([1000000, 504096, 4096])

    event = enospc(machine, "/mnt/pool", leave_bytes=4096)

    fills = [c for c in machine.commands("succeed") if c.startswith("fallocate")]
    assert fills == [
        "fallocate -o 0 -l 995904 /mnt/pool/.spike-filler",
        "fallocate -o 995904 -l 500000 /mnt/pool/.spike-filler",
    ]
    assert "4096 bytes free" in event.detail


def test_enospc_accepts_space_released_after_the_final_fill():
    machine = FillingMachine([1000000, 1000000, 1000000, 1000000, 4096])

    event = enospc(machine, "/mnt/pool", leave_bytes=4096)

    assert event.kind == "enospc"
    assert len([c for c in machine.commands("succeed") if c.startswith("fallocate")]) == 4


def test_enospc_refuses_when_less_than_the_wanted_space_is_free():
    machine = FillingMachine([100])

    with pytest.raises(ValueError, match="free"):
        enospc(machine, "/mnt/pool", leave_bytes=4096, point="pool:free:4096")


def test_enospc_refuses_when_the_free_space_does_not_come_down():
    machine = FillingMachine([1000000])

    with pytest.raises(ValueError, match="still 1000000 bytes free"):
        enospc(machine, "/mnt/pool", leave_bytes=4096)


def test_sync_limited_runs_a_block_range_of_the_sync():
    machine = FakeMachine()

    event = sync_limited(machine, start=100, count=50, point="sync:pos:150")

    assert machine.commands("succeed") == ["snapraid sync -S 100 -B 50"]
    assert event.kind == "sync-limited"


def test_a_point_is_armed_by_a_file_and_released_through_its_fifo():
    machine = FakeMachine()

    arm(machine, "post:M5")
    release(machine, "post:M5")

    assert machine.commands("succeed") == [
        f"mkdir -p {HOOK_DIR}/armed && touch {HOOK_DIR}/armed/post:M5",
        f"echo > {HOOK_DIR}/go/post:M5",
    ]


def test_fire_waits_for_the_point_marker_before_the_fault_and_never_sleeps():
    machine = FakeMachine()
    order = []

    event = fire(machine, "post:M3", lambda: order.append(list(machine.calls)) or "fault-result")

    assert machine.commands("wait_for_file") == [f"{HOOK_DIR}/reached/post:M3"]
    assert order[0][-1] == ("wait_for_file", f"{HOOK_DIR}/reached/post:M3")
    assert event == "fault-result"
