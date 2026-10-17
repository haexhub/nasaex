# Probe of the fault kinds of contracts/fault-points.md. It observes, it does not judge the
# spikes: the outcomes decide whether `dm-error` replaces a kind (testlib/faults.py) and feed
# the `residual_risk` text. It never writes a record. Result: probe-faults.json in the working
# directory of the driver and a PROBE-RESULT line in the log.
import json
import re
from pathlib import Path

from testlib.faults import device_failed_at_boot

DISKS = {d["serial"]: d for d in TOPOLOGY["disks"]}
RESULT: dict = {"environment": {}, "kinds": {}}


def monitor(command: str) -> str:
    return server.send_monitor_command(command)


def shell(command: str, timeout: int = 60) -> tuple[int, str]:
    return server.execute(command, timeout=timeout)


def exists(path: str) -> bool:
    return shell(f"test -e {path}")[0] == 0


def drive_of(serial: str) -> str:
    """QEMU drive id of the disk with this serial, from the device id set in the node."""
    for block in re.split(r"\r?\n(?=\S)", monitor("info block")):
        if f"/machine/peripheral/disk-{serial}/" in block:
            return block.split()[0]
    raise AssertionError(f"no drive attached as disk-{serial}")


def by_id(serial: str) -> str:
    return f"/dev/disk/by-id/virtio-{serial}"


def settle_jobs() -> None:
    """Mounts are nofail with a device timeout: on a boot with blank disks their jobs are still
    pending when multi-user.target is reached. Wait until systemd has none, so that a manual
    mount cannot race a mount job."""
    server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)


def boot() -> None:
    server.start()
    server.wait_for_unit("multi-user.target")
    settle_jobs()


def kind(name: str, as_assumed: bool, **observed) -> None:
    RESULT["kinds"][name] = {"as_assumed": as_assumed, "observed": observed}
    print(f"PROBE {name}: as_assumed={as_assumed} {observed}")


# --- environment and harness basics -------------------------------------------------------
start_all()
server.wait_for_unit("multi-user.target")
settle_jobs()

versions = {
    "kernel": "uname -r",
    "mergerfs": "mergerfs -V 2>&1 | head -1",
    "snapraid": "snapraid --version 2>&1 | head -1",
    "btrfs_progs": "btrfs --version",
    "snapper": "snapper --version 2>&1 | head -1",
    "snapraid_btrfs": "snapraid-btrfs --version 2>&1 | head -1",
    "samba": "smbd --version",
    "e2fsprogs": "mkfs.ext4 -V 2>&1 | head -1",
}
for key, command in versions.items():
    RESULT["environment"][key] = server.succeed(command).strip()
qemu_version = re.search(r"\d+\.\d+\.\d+\S*", monitor("info version"))
RESULT["environment"]["qemu"] = qemu_version.group(0) if qemu_version else "unknown"
RESULT["environment"]["machine_type"] = server.succeed("cat /sys/class/dmi/id/product_name").strip()
RESULT["environment"]["tool_resources"] = TOPOLOGY["vmResources"]

outside_reachable = shell("timeout 5 bash -c 'echo > /dev/tcp/1.1.1.1/53'", timeout=20)[0] == 0
RESULT["environment"]["outside_network_reachable"] = outside_reachable
assert not outside_reachable, "the VMs must have no network access"

for serial in DISKS:
    server.succeed(f"test -b {by_id(serial)}")
print("PROBE all disks resolve by serial under /dev/disk/by-id")

# --- topology: format, mount, tiered-cache smoke --------------------------------------------
server.succeed("spike-mkfs all")
for mount in [
    "/mnt/pool",
    "/mnt/parity",
    "/mnt/archive/arch0",
    "/mnt/archive/arch1",
    "/mnt/archive/arch2",
    "/mnt/archive-content/arch0",
]:
    server.succeed(f"mount {mount}")
server.succeed("systemctl start mnt-base.mount mnt-cache.mount")
server.succeed("mkdir -p /mnt/cache/smoke && echo hello > /mnt/cache/smoke/new.txt")
placed_on_pool = exists("/mnt/pool/smoke/new.txt")
placed_on_archive = any(exists(f"{a['branch']}/smoke") for a in TOPOLOGY["archiveLayout"])
RESULT["tiered_cache_smoke"] = {
    "new_file_on_pool_branch": placed_on_pool,
    "new_file_on_an_archive_branch": placed_on_archive,
    "mount_options": server.succeed("findmnt -no OPTIONS /mnt/cache").strip(),
    "mount_source": server.succeed("findmnt -no SOURCE /mnt/cache").strip(),
}
print(f"PROBE tiered cache smoke: {RESULT['tiered_cache_smoke']}")
server.succeed("systemctl stop mnt-cache.mount mnt-base.mount")

# --- poweroff: machine.crash() with an unflushed-write probe ----------------------------------
server.succeed("mkdir -p /mnt/pool/probe && echo synced > /mnt/pool/probe/synced && sync")
server.succeed("echo unflushed > /mnt/pool/probe/unflushed")  # no sync, no fsync
server.crash()
boot()
mounts_after = {
    m: shell(f"findmnt -n {m}")[0] == 0
    for m in ["/mnt/pool", "/mnt/archive/arch0", "/mnt/parity", "/mnt/base", "/mnt/cache"]
}
synced_survived = shell("cat /mnt/pool/probe/synced")[1].strip() == "synced"
unflushed_present = exists("/mnt/pool/probe/unflushed")
kind(
    "poweroff",
    as_assumed=synced_survived and all(mounts_after.values()),
    synced_write_survived=synced_survived,
    unflushed_write_visible_after_restart=unflushed_present,
    mounts_back_without_manual_steps=mounts_after,
    pool_dmesg_errors=shell("dmesg | grep -ci 'btrfs.*error'")[1].strip(),
)

# --- device-unplug: device_del of a RAID1 pool member, selected by serial --------------------
server.succeed("echo before > /mnt/pool/probe/before-unplug && sync")
monitor("device_del disk-pool1")
gone = False
for _ in range(30):
    if not exists(by_id("pool1")):
        gone = True
        break
    server.sleep(1)  # waiting for the guest to acknowledge the removal, not a fault trigger
read_ok = shell("cat /mnt/pool/probe/before-unplug", timeout=30)
write_ok = shell("echo after > /mnt/pool/probe/after-unplug && sync", timeout=30)
kind(
    "device-unplug",
    as_assumed=gone,
    disk_disappeared_from_by_id=gone,
    pool_read_after=read_ok,
    pool_write_after=write_ok,
    other_disk_unaffected=shell("ls /mnt/archive/arch0", timeout=30)[0] == 0,
    btrfs_devices=shell("btrfs filesystem show /mnt/pool 2>&1", timeout=30)[1][-400:],
)

# --- device-ioerror: drive_del of an archive disk --------------------------------------------
drive = drive_of("arch1")
server.succeed("echo before > /mnt/archive/arch1/before-ioerror && sync")
monitor(f"drive_del {drive}")
node_remains = exists(by_id("arch1"))
write = shell(
    "dd if=/dev/zero of=/mnt/archive/arch1/ioerror bs=1M count=4 oflag=direct", timeout=60
)
raw = shell(f"dd if={by_id('arch1')} of=/dev/null bs=4k count=1 iflag=direct", timeout=30)
kind(
    "device-ioerror",
    as_assumed=node_remains and (write[0] != 0 or raw[0] != 0),
    drive=drive,
    device_node_remains=node_remains,
    direct_write_through_fs=write,
    raw_read=raw,
    btrfs_device_stats=shell("btrfs device stats /mnt/archive/arch1 2>&1", timeout=30)[1][-300:],
    dmesg_io_errors=shell("dmesg | grep -ci 'i/o error'")[1].strip(),
)

# --- device-failed-at-boot: swap the backing image for an empty one before the next start ----
server.crash()
target = DISKS["arch0"]
device_failed_at_boot(server, target, QEMU_IMG, point="probe:device-failed-at-boot")
boot()
blank = shell(f"blkid -p {by_id('arch0')}")[0] != 0
arch0_mount = shell("systemctl is-active mnt-archive-arch0.mount")[1].strip()
others = {m: shell(f"findmnt -n {m}")[0] == 0 for m in ["/mnt/pool", "/mnt/archive/arch1"]}
kind(
    "device-failed-at-boot",
    as_assumed=blank and arch0_mount != "active",
    replacement_is_blank=blank,
    failed_disk_mount_state=arch0_mount,
    other_mounts_up=others,
    unplugged_and_ioerror_disks_return_after_restart=exists(by_id("pool1"))
    and exists(by_id("arch1")),
    reached_multi_user=True,
)

Path("probe-faults.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
print("PROBE-RESULT " + json.dumps(RESULT, sort_keys=True))
