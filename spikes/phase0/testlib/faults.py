"""Fault kinds of specs/001-phase0-storage-spikes/contracts/fault-points.md.

Every trigger is host-side and fires at a named point, never after a sleep (FR-010): a point is
reached because a guest process says so (hook marker), a log line says so, or a scenario step
finished. `fire` waits for the marker of a point and then runs the fault.

Probe outcome (vm-tests/probes/probe_faults.py, pinned QEMU 10.2.4, kernel 6.18.56, virtio-blk
on the `pc` machine type): `poweroff`, `device-unplug`, `device-ioerror` and
`device-failed-at-boot` behave as the contract lists, so no kind is replaced by `dm-error`.
`poweroff` is QEMU process termination: data the guest synced survives, data only in the guest
page cache is lost, and the machine restarts on the same disks with its mounts. It is not host
power loss: device-level volatile caches are not emulated (stated in every record's
`residual_risk`). The kinds `write-error`, `kill-sync-at-position` and `concurrent-modify` are
added by the spikes that need them (S2, S3).

The `machine` arguments are driver machine objects (duck-typed). Standard library only."""

import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HOOK_DIR = "/run/spike-hooks"
DEFAULT_FILLER = ".spike-filler"
FILL_ROUNDS = 4  # enospc: measurements of the free space, each followed by a fill if needed


@dataclass(frozen=True)
class FaultEvent:
    kind: str
    point: str
    detail: str = ""


def by_id(serial: str) -> str:
    return f"/dev/disk/by-id/virtio-{serial}"


def device_id(serial: str) -> str:
    """The QEMU device id the nodes give each disk (nix/nodes.nix)."""
    return f"disk-{serial}"


# --- named points: arm, wait, release --------------------------------------------------------


def arm(machine, point: str, hook_dir: str = HOOK_DIR) -> None:
    machine.succeed(f"mkdir -p {hook_dir}/armed && touch {hook_dir}/armed/{point}")


def wait_reached(machine, point: str, hook_dir: str = HOOK_DIR, timeout: int = 600) -> None:
    machine.wait_for_file(f"{hook_dir}/reached/{point}", timeout=timeout)


def release(machine, point: str, hook_dir: str = HOOK_DIR) -> None:
    machine.succeed(f"echo > {hook_dir}/go/{point}")


def fire(machine, point: str, fault: Callable[[], FaultEvent], hook_dir: str = HOOK_DIR):
    """Wait until the guest reports the (armed) point, then run the fault."""
    wait_reached(machine, point, hook_dir)
    return fault()


# --- process and machine faults ----------------------------------------------------------------


def kill(machine, process_pattern: str, point: str = "") -> FaultEvent:
    """kill -9 of a guest process, normally while it is blocked at a hook."""
    machine.succeed(f"pkill -9 -f {shlex.quote(process_pattern)}")
    return FaultEvent("kill", point, process_pattern)


def poweroff(machine, point: str = "") -> FaultEvent:
    """QEMU process termination through the driver. Never calls sync."""
    machine.crash()
    return FaultEvent("poweroff", point)


# --- device faults, selected by serial -----------------------------------------------------------


def device_unplug(machine, serial: str, point: str = "") -> FaultEvent:
    machine.send_monitor_command(f"device_del {device_id(serial)}")
    # The guest acknowledges the removal asynchronously; waiting for the effect is not a trigger.
    machine.wait_until_fails(f"test -e {by_id(serial)}")
    return FaultEvent("device-unplug", point, serial)


def parse_drive(info_block: str, serial: str) -> str:
    """QEMU drive id of the disk with this serial, from the output of `info block`."""
    for block in re.split(r"\r?\n(?=\S)", info_block):
        if f"/machine/peripheral/{device_id(serial)}/" in block:
            return block.split()[0]
    raise LookupError(f"no drive is attached as {device_id(serial)}")


def device_ioerror(machine, serial: str, point: str = "") -> FaultEvent:
    drive = parse_drive(machine.send_monitor_command("info block"), serial)
    machine.send_monitor_command(f"drive_del {drive}")
    return FaultEvent("device-ioerror", point, drive)


def device_failed_at_boot(machine, disk: dict, qemu_img: Path | str, point: str = "") -> FaultEvent:
    """Replace the backing image of a disk by an empty one before the next start.

    Also the "replacement disk" step: the start command of a machine is fixed and would
    re-attach the old image. `disk` is an entry of the topology (serial, sizeMiB, image)."""
    if machine.booted:
        raise RuntimeError("the machine is running; power it off before swapping a backing image")
    image = Path(machine.state_dir) / disk["image"]
    image.unlink(missing_ok=True)
    subprocess.run(
        [str(qemu_img), "create", "-f", "qcow2", str(image), f"{disk['sizeMiB']}M"],
        check=True,
        capture_output=True,
    )
    return FaultEvent("device-failed-at-boot", point, disk["serial"])


# --- data and space faults -----------------------------------------------------------------------


def corrupt(machine, path: str, offset: int, length: int, point: str = "") -> FaultEvent:
    """Overwrite `length` bytes of the file in place with a fixed pattern (content only)."""
    machine.succeed(
        f"head -c {length} /dev/zero | tr '\\0' '\\245' | "
        f"dd of={path} bs=1 seek={offset} conv=notrunc"
    )
    return FaultEvent("corrupt", point, f"{path}@{offset}+{length}")


def enospc(
    machine, mountpoint: str, leave_bytes: int, point: str = "", filler: str = DEFAULT_FILLER
) -> FaultEvent:
    """Fill the filesystem with a filler file so that at most `leave_bytes` stay free.

    btrfs releases the extents of a deleted file only at the next transaction commit, so the
    free count is read after `sync -f` (which commits it), and read again after filling: space
    that appears later extends the filler (a few rounds at most, then the fault is refused)."""
    filled, first = 0, None
    for _ in range(FILL_ROUNDS):
        machine.succeed(f"sync -f {mountpoint}")
        avail = int(machine.succeed(f"df --output=avail -B1 {mountpoint} | tail -n1").strip())
        first = avail if first is None else first
        if avail <= leave_bytes:
            if not filled:
                raise ValueError(
                    f"only {avail} bytes are free on {mountpoint}, wanted {leave_bytes} left"
                )
            detail = f"{mountpoint}: {avail} bytes free after filling, of {first}"
            return FaultEvent("enospc", point, detail)
        machine.succeed(f"fallocate -o {filled} -l {avail - leave_bytes} {mountpoint}/{filler}")
        filled += avail - leave_bytes
    raise ValueError(f"still {avail} bytes free on {mountpoint} after {FILL_ROUNDS} fills")


def sync_limited(machine, start: int, count: int, point: str = "") -> FaultEvent:
    """A sync that processes only a block range and ends normally."""
    machine.succeed(f"snapraid sync -S {start} -B {count}")
    return FaultEvent("sync-limited", point, f"S={start} B={count}")
