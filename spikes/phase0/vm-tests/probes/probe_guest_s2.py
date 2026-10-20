# The S2 guest-script probe (tasks T060, T061). It measures, it does not judge, and it never
# writes a record. Profile `small`, `server` only, with nix/snapper-configs.nix (the array and the
# snapper configs) and nix/archive-lock.nix (the command wrappers). Scaffolding for the Phase 0
# spikes, not product code.
#
# setup:   the generated configs, the wrappers in PATH, `.snapshots` per data disk, seeded files
# sync:    snapsync sync, complete, covered (synced set and --plain), a live change, a second
#          sync (the older set goes), free, and the force-empty option smuggled among the extras
# busy:    lock while a file on an archive disk is open for writing, then without it
# locked:  every write route while locked: a direct write, the base pool, the content location,
#          the wrappers, the real snapraid, snapper create/delete/cleanup, the mover primitive,
#          snapsync sync; SnapRAID status and diff; parity and content checksums before and after
# failure: unlock, arch2 fails (device-ioerror), lock; arch2 replaced on the spare of its size;
#          snapsync fix under the lock; files compared; unlock; snapsync sync, complete, covered
# Result: probe-guest-s2.json in the working directory of the driver and PROBE lines.
import json
import shlex
import time
from pathlib import Path

from testlib import faults

STARTED = time.monotonic()
ARCH = ["arch0", "arch1", "arch2"]
DATA = {s: f"/mnt/archive/{s}" for s in ARCH}
LOGS = "/root/logs"
# The real snapraid, as the archive-lock wrapper in PATH names it (nix/archive-lock.nix).
REAL_SNAPRAID = "$(sed -n 's/^export ARCHIVE_LOCK_SNAPRAID=//p' $(command -v snapraid))"
RESULT: dict = {}
EXPECTED: dict[str, str] = {}  # path -> SHA-256 as of the last completed sync


def attempt(command: str, timeout: int = 900) -> tuple[int, str]:
    return server.execute(f"{command} 2>&1", timeout=timeout)


def run(command: str, timeout: int = 900) -> str:
    return server.succeed(command, timeout=timeout)


def note(phase: str, key: str, value) -> None:
    RESULT.setdefault(phase, {})[key] = value
    print(f"PROBE {phase}.{key}: {json.dumps(value)[:800]}")


def measure(phase: str, key: str, command: str) -> int:
    rc, out = attempt(command)
    note(phase, key, {"rc": rc, "out": out.strip().splitlines()[-30:]})
    return rc


def sha(paths: list[str]) -> dict[str, str]:
    out = run("sha256sum " + " ".join(shlex.quote(p) for p in paths))
    return {line.split(None, 1)[1].strip(): line.split()[0] for line in out.splitlines()}


def array_files() -> list[str]:
    out = run("find /mnt/archive/*/branch -type f | sort")
    return out.split()


def guarded_state() -> dict[str, str]:
    """Checksums of the parity file and both content copies."""
    return sha(
        [
            "/mnt/parity/snapraid.parity",
            "/mnt/parity/snapraid.content",
            "/var/lib/snapraid/snapraid.content",
        ]
    )


def snapper_numbers() -> dict[str, list]:
    out = {}
    for serial in ARCH:
        rc, text = attempt(
            f"env LC_ALL=C snapper --csvout -c {serial} list --disable-used-space "
            "--columns number,userdata"
        )
        out[serial] = text.strip().splitlines()[1:] if rc == 0 else [f"rc={rc}", text.strip()]
    return out


def mount_options() -> dict[str, str]:
    out = run("findmnt -rn -o TARGET,OPTIONS | grep -E '^/mnt/(archive|parity)|^/var/lib/snapraid'")
    return dict(line.split(" ", 1) for line in out.splitlines())


# --- setup -----------------------------------------------------------------------------------

server.wait_for_unit("multi-user.target")
run("spike-mkfs all")
CONTENT = [f"/mnt/archive-content/{s}" for s in ARCH]
for mount in [*DATA.values(), *CONTENT, "/mnt/parity", "/mnt/pool"]:
    run(f"findmnt -n {mount} >/dev/null || mount {mount}")
run("systemctl start mnt-base.mount")
run(f"mkdir -p {LOGS}")
note("setup", "snapraid_conf", run("cat /etc/snapraid.conf").splitlines())
note("setup", "snapper_config_arch0", run("cat /etc/snapper/configs/arch0").splitlines())
measure("setup", "sysconfig_snapper", "cat /etc/sysconfig/snapper")
note("setup", "which", run("type -a snapraid snapraid-btrfs snapsync archive-lock").splitlines())
measure(
    "setup",
    "snapshots_init",
    "spike-snapshots-init arch0 arch1 arch2 && stat -c '%n %U %a' /mnt/archive/*/.snapshots "
    "&& btrfs subvolume show /mnt/archive/arch0/.snapshots | head -3",
)
for serial, count in (("arch0", 3), ("arch1", 4), ("arch2", 3)):
    for i in range(count):
        target = f"{DATA[serial]}/branch/f{i}.bin"
        run(f"dd if=/dev/urandom of={target} bs=64K count={8 + 5 * i} status=none")
run("sync")

# --- sync ------------------------------------------------------------------------------------

measure("sync", "sync1", f"snapsync sync --log {LOGS}/sync1.log")
measure("sync", "complete1", "snapsync complete")
measure("sync", "covered1", f"snapsync covered --log {LOGS}/covered1.log")
measure("sync", "covered_plain1", f"snapsync covered --plain --log {LOGS}/covered-plain1.log")
note("sync", "snapshots1", snapper_numbers())
run(f"dd if=/dev/urandom of={DATA['arch0']}/branch/live.bin bs=64K count=6 status=none && sync")
measure("sync", "covered_after_live_change", f"snapsync covered --log {LOGS}/covered2.log")
measure(
    "sync",
    "covered_plain_after_live_change",
    f"snapsync covered --plain --log {LOGS}/covered-plain2.log",
)
measure("sync", "free_before_sync2", "snapsync free arch0")
measure("sync", "sync2", f"snapsync sync --log {LOGS}/sync2.log")
note("sync", "snapshots2", snapper_numbers())
measure("sync", "free_after_sync2", "snapsync free arch0")
measure("sync", "complete2", "snapsync complete")
measure("sync", "covered_plain2", f"snapsync covered --plain --log {LOGS}/covered-plain3.log")
measure("sync", "smuggled_force_empty", "snapsync sync -- -E")
note("sync", "snapshots_after_smuggled", snapper_numbers())
EXPECTED.update(sha(array_files()))

# --- busy ------------------------------------------------------------------------------------

# A writer that holds a file on arch1 open; it writes its PID first (a pattern kill would also
# match the shell that runs it).
HOLDER = (
    "import os, time; f = open('/mnt/archive/arch1/branch/held.bin', 'a'); "
    "open('/run/held.pid', 'w').write(str(os.getpid())); time.sleep(3600)"
)
run(f"setsid -f python3 -c {shlex.quote(HOLDER)} </dev/null >/dev/null 2>&1")
server.wait_until_succeeds("test -s /run/held.pid")
measure("busy", "lock_with_writer", "archive-lock lock 'probe: busy'")
note("busy", "status_with_writer", run("archive-lock status").splitlines())
measure("busy", "sync_refused_while_partly_locked", "snapraid sync")
run("pid=$(cat /run/held.pid); kill $pid; while kill -0 $pid 2>/dev/null; do sleep 0.1; done")
measure("busy", "lock_again", "archive-lock lock 'probe: busy'")
measure("busy", "unlock", "archive-lock unlock")
run(f"rm {DATA['arch1']}/branch/held.bin && snapsync sync --log {LOGS}/sync3.log")

# --- locked ----------------------------------------------------------------------------------

before = guarded_state()
snap_before = snapper_numbers()
measure("locked", "lock", "archive-lock lock 'probe: healthy array'")
note("locked", "status", run("archive-lock status").splitlines())
note("locked", "mount_options", mount_options())
measure("locked", "direct_write", f"touch {DATA['arch0']}/branch/new.bin")
measure(
    "locked",
    "direct_rewrite",
    f"dd if=/dev/zero of={DATA['arch0']}/branch/f0.bin bs=4K count=1 conv=notrunc",
)
measure("locked", "direct_delete", f"rm {DATA['arch2']}/branch/f0.bin")
measure("locked", "base_pool_mount", "findmnt -n /mnt/base")
measure("locked", "base_pool_touch", "touch /mnt/base/branchless.bin")
measure("locked", "base_pool_ls", "ls -la /mnt/base")
measure("locked", "content_location_write", "touch /var/lib/snapraid/x")
measure("locked", "parity_write", "touch /mnt/parity/x")
measure("locked", "wrapper_sync", "snapraid sync")
measure("locked", "wrapper_scrub", "snapraid scrub")
measure("locked", "wrapper_srb_sync", "snapraid-btrfs sync")
measure("locked", "wrapper_srb_diff", "snapraid-btrfs diff")
measure("locked", "real_snapraid_sync", f"{REAL_SNAPRAID} -l {LOGS}/real-sync.log sync")
measure("locked", "real_snapraid_touch", f"{REAL_SNAPRAID} touch")
measure("locked", "snapraid_status", f"snapraid -l {LOGS}/status-locked.log status")
measure("locked", "snapraid_diff", f"snapraid -l {LOGS}/diff-locked.log diff")
measure("locked", "srb_ls", "env LC_ALL=C snapraid-btrfs ls")
measure("locked", "snapper_create", "snapper -c arch0 create -d probe")
measure(
    "locked", "snapper_delete", f"snapper -c arch1 delete {snap_before['arch1'][-1].split(',')[0]}"
)
measure(
    "locked",
    "snapper_cleanup_unit",
    "systemctl start snapper-cleanup.service; systemctl status snapper-cleanup.service | tail -5",
)
measure(
    "locked",
    "mover_copy_item",
    f"mover-effects mover_copy_item {DATA['arch0']}/branch/f1.bin /mnt/base/moved.bin 0",
)
measure("locked", "snapsync_sync", "snapsync sync")
note("locked", "guarded_unchanged", guarded_state() == before)
note("locked", "snapshots_unchanged", snapper_numbers() == snap_before)
note("locked", "files_unchanged", sha(array_files()) == EXPECTED)
measure("locked", "unlock", "archive-lock unlock")
note("locked", "mount_options_after_unlock", mount_options())
measure(
    "locked",
    "write_after_unlock",
    f"touch {DATA['arch0']}/branch/after.bin && rm {DATA['arch0']}/branch/after.bin",
)

# --- failure ---------------------------------------------------------------------------------

event = faults.device_ioerror(server, "arch2")
note("failure", "fault", [event.kind, event.detail])
measure("failure", "read_failed_disk", f"cat {DATA['arch2']}/branch/f1.bin > /dev/null")
measure("failure", "lock", "timeout 120 archive-lock lock 'arch2 failed'")
note("failure", "status", run("archive-lock status").splitlines())
note("failure", "mount_options", mount_options())
measure("failure", "dmesg", "dmesg | grep -i -E 'btrfs|arch2|I/O' | tail -n 15")
measure("failure", "wrapper_sync", "snapraid sync")
failed_mounts = f"{DATA['arch2']} /mnt/archive-content/arch2"
measure("failure", "unmount_failed", f"umount {failed_mounts} || umount -l {failed_mounts}")
spare = faults.by_id("spare2")
run(f"mkfs.btrfs -q -f -L arch2 {spare} && udevadm settle")
run(f"mkdir -p /mnt/new-arch2 && mount {spare} /mnt/new-arch2")
run("btrfs subvolume create /mnt/new-arch2/data && btrfs subvolume create /mnt/new-arch2/content")
run(f"umount /mnt/new-arch2 && mount -o subvol=data {spare} {DATA['arch2']}")
run(f"mkdir -p {DATA['arch2']}/branch {DATA['arch2']}/trash")
measure("failure", "snapshots_init_replacement", "spike-snapshots-init arch2")
note("failure", "status_after_replacement", run("archive-lock status").splitlines())
measure("failure", "fix", f"snapsync fix arch2 --log {LOGS}/fix.log")
arch2 = {p: s for p, s in EXPECTED.items() if p.startswith(DATA["arch2"] + "/")}
now = {}
for path in arch2:
    rc, out = attempt(f"sha256sum {path}")
    now[path] = out.split()[0] if rc == 0 else f"rc={rc}"
note("failure", "arch2_bit_exact", sorted(p for p in arch2 if now[p] == arch2[p]))
note("failure", "arch2_not_bit_exact", sorted(p for p in arch2 if now[p] != arch2[p]))
measure("failure", "unlock", "archive-lock unlock")
if measure("failure", "sync_after_replacement", f"snapsync sync --log {LOGS}/sync4.log"):
    # Which config refuses a snapshot, and does a fresh snapperd change it?
    for serial in ARCH:
        measure("failure", f"snapper_create_{serial}", f"snapper -c {serial} create -p -d diag")
    measure("failure", "snapperd_journal", "journalctl -u snapperd --no-pager | tail -n 20")
    measure("failure", "stop_snapperd", "systemctl stop snapperd.service")
    for serial in ARCH:
        measure(
            "failure", f"snapper_create_fresh_{serial}", f"snapper -c {serial} create -p -d diag2"
        )
    measure("failure", "sync_after_snapperd_stop", f"snapsync sync --log {LOGS}/sync5.log")
measure("failure", "complete", "snapsync complete")
measure("failure", "covered", f"snapsync covered --log {LOGS}/covered4.log")
note("failure", "all_files_bit_exact", sha(array_files()) == EXPECTED)

RESULT["seconds"] = round(time.monotonic() - STARTED, 1)
Path("probe-guest-s2.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True))
