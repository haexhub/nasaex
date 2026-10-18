# Probe result: the two-device pool race at boot (task T051f)

Measured on 2026-10-18 with the probe `pool_race.nix` / `probe_pool_race.py` (first boot and per-boot
measurement shared with T051a in `boot_probe_setup.py`, parsing in `pool_race.py`), one VM test at a time,
KVM, machine state on an ext4 disk. A probe never writes a record; the full output of each run is
`probe-pool-race.json` (and `boot-<n>-diagnostics.json` for an incomplete boot) in the output directory of
the driver (evidence, not committed). Nothing below changes `nix/nodes.nix` or adopts a mount setting.

Question: one of 11 real S1.11 reboots (shakedown T051, run 2) came up without its pool: only
`dev-disk-by\x2dlabel-spikepool.device` timed out (`x-systemd.device-timeout=10s`, `nofail`), the two RAID1
members had been scanned 2.3 ms apart by two udev workers, and mnt-pool, mnt-base and mnt-cache failed with
result 'dependency' (S1 criteria changelog). T051a, T051d and T051e found 0 failures in 68 reboots with scan
gaps of 0.04 to 86 ms. By which mechanism can the pool's device unit fail to appear, and can it be
reproduced on purpose?

## Step 1: how the pool's device unit gets ready (source reading, no VM)

Sources of the pinned nixpkgs `a033e66` (`nix eval --inputs-from ./spikes/phase0 nixpkgs#...`):
systemd 260.5 (`systemd.src` = `/nix/store/s8lxzd3r71kn8f8gfj9vq4ghdzvlna7f-source`, installed rule
`/nix/store/kmzlca218j6j1ar292fxj7707qzypij8-systemd-260.5/lib/udev/rules.d/64-btrfs.rules`, the file
`udevadm cat 64-btrfs.rules` reports in the guest), kernel 6.18.56
(`/nix/store/7vkdil4rjwzk6xqvfxfsrhc6xb1krf8a-linux-6.18.56.tar.xz`), btrfs-progs 6.19.1
(`/nix/store/9wdxa5cl8n0b3yc1qnsjnbj0gld2xbls-btrfs-progs-v6.19.1.tar.xz`; it plays no part at boot: the
rule uses udev's builtin, not `btrfs device ready`). Paths below are relative to those trees.

1. **Kernel: who is "ready".** `BTRFS_IOC_DEVICES_READY` on `/dev/btrfs-control`
   (`fs/btrfs/super.c:2269-2284`) takes the global `uuid_mutex`, registers the device
   (`btrfs_scan_one_device`, `fs/btrfs/volumes.c:1453`; a new member increments `fs_devices->num_devices` and
   prints `BTRFS: device label ... scanned by <comm> (<pid>)`, `volumes.c:875-886`; `total_devices` comes from the
   superblock, `volumes.c:994`) and returns `!(num_devices == total_devices)`. The calls are serialised by
   `uuid_mutex`, so of the two members exactly one, the first registered in that boot, sees an incomplete volume
   however close the two scans are; the second sees it complete. Nothing in the kernel tells the first one later.
2. **udev builtin.** `udev-builtin-btrfs.c:49-55` sets `ID_BTRFS_READY=1` when the ioctl returns 0, else `0`.
3. **Rule** (`64-btrfs.rules`, installed file, lines 3-17): line 9 `IMPORT{builtin}="btrfs ready $devnode"`; line 12
   `ENV{ID_BTRFS_READY}=="0", ENV{SYSTEMD_READY}="0"`; line 15 `ENV{ID_BTRFS_READY}=="1", RUN+=".../udevadm trigger
   -s block -p ID_BTRFS_READY=0"`. So the first member is marked `SYSTEMD_READY=0` in **every** boot and depends on
   the second member's (or any other ready btrfs device's) RUN to be re-evaluated. Line 6
   (`ENV{SYSTEMD_READY}=="0", GOTO="btrfs_end"`) does not block that re-evaluation: an event device received from the
   kernel is sealed and does not load the old database (`sd-device/device-private.c:445-464`, `:969`), so a change
   event starts without the old `SYSTEMD_READY=0` (measured: the re-triggered member ends with `ID_BTRFS_READY=1`).
4. **The retrigger is level-triggered on the database.** `udevadm trigger -p ID_BTRFS_READY=0` enumerates
   `/sys/class/block` and matches the property against each device's uevent file plus its udev database
   (`sd-device/sd-device.c:2056-2065`, `sd-device/device-enumerator.c:649-653`). It sends a change event only to
   members whose database **already** says `ID_BTRFS_READY=0` at that moment.
5. **Order inside one udev worker** (`udev-worker.c:228-261`, `udev-event.c:372-450`): the rules run (the ioctl at
   rule 64), then `update_devnode` creates/updates the symlinks (`udev-event.c:417`, `udev_node_update`), then the
   database is written (`udev-event.c:443`), then the RUN programs run (`udev-worker.c:233`, the retrigger), then
   the database is finalised and the event is broadcast to PID 1 (`udev-worker.c:249-261`). Events of different
   devices run in parallel workers (11 at most in this VM, `StatusText` read back; formula
   `udev-config.c:309-327`).
6. **The shared link.** Both members claim `/dev/disk/by-label/spikepool` (`60-persistent-storage.rules:141`) with
   the same priority 0; whoever updates it last with equal priority takes it over (`udev-node.c:541-548`).
7. **PID 1.** A devlink `.device` unit is set up from a uevent only if the device the link **currently resolves to**
   is ready (tagged `systemd`, not `SYSTEMD_READY=0`) and not still being processed (`core/device.c:786-811`,
   `device_is_ready` `:752-784`); otherwise the unit is put on the not-ready list (it goes inactive if it was
   active). The mount depends only on its `What=` (`core/mount.c:361-366`, Requires; BindsTo only when bound to the
   device); btrfs `device=` options do not add device dependencies (no handling in `fstab-generator`).

**Hypothesis.** Call A the member whose ioctl ran first (not ready) and B the other. From the reading alone:
- *Miss*: A's database write (`udev-event.c:443`) happens after **every** retrigger enumeration of that boot: B's,
  and those of the three single-device archive disks, whose events also report `ID_BTRFS_READY=1` and so run the
  same retrigger (`btrfs_skip_registration` skips an unmounted single-device filesystem, `volumes.c:1437-1439`
  and `:1484-1491`, and the ioctl returns 0 for the resulting NULL, `super.c:2275-2279`). A then stays
  `SYSTEMD_READY=0` for the rest of the boot; nothing else re-triggers it (no further btrfs event, no
  write-close on the device).

Added after the variant `delay` below showed that the miss alone does not fail the mount:
- *Link*: when PID 1 handles B's (ready) event, `/dev/disk/by-label/spikepool` already points to A (A updated the
  link after B, step 6). Then PID 1 never sets the label unit up (step 7), the device job hits
  `x-systemd.device-timeout=10s` and mnt-pool fails with 'dependency'.

The two scan lines are the two ioctls; their distance (2.3 ms in the failed S1.11 boot) says nothing by itself:
the windows lie around A's database write and B's broadcast, milliseconds later.

**Upstream reports** (web search, 2026-10-18): systemd issue
[#1921](https://github.com/systemd/systemd/issues/1921) (2015-2016) describes multi-device btrfs members "left with
SYSTEMD_READY=0" and queued device jobs never finishing, fixed by garbage-collecting redundant device jobs
(systemd 233), not by changing the retrigger; an Arch forum thread
([2024](https://bbs.archlinux.org/viewtopic.php?id=296593)) reports a btrfs RAID1 failing "once every 7 boots" with
`Timed out waiting for device /dev/disk/by-uuid/...`, without a diagnosis. According to the search results the
retrigger came with systemd PR #6607 ("assemble multidevice btrfs volumes without external tools"; not checked
against the systemd history). No report was found that names the database-write window of step 4; that the S1.11
failure is this mechanism is UNVERIFIED (its journal is gone).

## Step 2: environment and measurement

- The S1 server exactly as T051a boots it: profile `large` with **every** labelled disk of `nix/nodes.nix`
  (pool0, pool1, arch0 to arch2, parity0, the spares), all eight mounts with `nofail,x-systemd.device-timeout=10s`,
  the tiered-cache mounts (mnt-base, mnt-cache) and the S1 share. The archive disks were kept on purpose: their
  events run the same retrigger (Step 1). The `server` VM alone, 1536 MiB; 2 vCPUs (as in S1) or 4 vCPUs
  (`virtualisation.cores` forced in the probe, `nproc` read back). Host: 24 CPUs, the maintainer's workstation with
  the desktop in use; before every run no other QEMU process and `MemAvailable` at least 10 GiB. The host was
  rebooted into a new kernel (7.1.5 to 7.2.6) at 22:06 during the first 2-vCPU run (not by the probe); the runs
  after that are marked "host 7.2.6".
- Guest: kernel 6.18.56, systemd 260 (260.5); `udevadm cat 64-btrfs.rules` shows the systemd 260.5 file of Step 1
  unchanged; udevd `StatusText` `Processing with 11 children at max` with 2 and with 4 vCPUs (memory-bound:
  `MIN(2 * cpus + 16, MAX(RAM / 128 MiB, 10))`).
- Added for the probe only: `probe-udev-monitor.service` runs `udevadm monitor --kernel --udev --property
  --subsystem-match=block` from before `systemd-udev-trigger.service` into `/run/probe-udev-monitor.log`
  (CLOCK_MONOTONIC stamps); it saw both pool members' kernel add events in every boot of every run.
- Per boot, after `multi-user.target` and an empty job queue, besides everything T051a records (`collect`):
  `cat /run/probe-udev-monitor.log` (kept: the pool members' kernel and udev events with SEQNUM, SYNTH_UUID,
  ID_BTRFS_READY, SYSTEMD_READY), `journalctl -b -o short-monotonic` (scan lines with the worker PID, pool lines),
  `readlink` and `lstat` ctime of the pool link, `ls -l` of its udev stack directory, `systemctl show` of the pool
  device unit and `systemctl show -p StatusText,TasksCurrent systemd-udevd.service`. A boot whose mounts are not
  all active, or whose not-ready member never became ready, also gets `s1.boot_diag.capture_if_incomplete` (T051e,
  unchanged; it writes nothing when every fstab mount is mounted) and a wait of up to 60 s for the pool device unit.
- "Stuck": the member that saw `ID_BTRFS_READY=0` on its add event had no later udev event with
  `ID_BTRFS_READY=1`. "Retriggers": kernel change events of that member with a SEQNUM above its add event's.
- Two probe defects were found and fixed during the work; what they voided was measured again: (1) the late wait
  spliced the escaped unit name into a quoted `sh -c` script and polled a unit that does not exist (fixed in
  `a9d8853`); every "late wait" below comes from runs after that fix. (2) The first 2-vCPU run counted only change
  events logged after the add's broadcast; a trigger can find the database (written before RUN and the broadcast)
  while the add is being finished, and its change event, logged earlier, is still handled after the add (SEQNUM
  order). Fixed in `b84296b`; the numbers below were recounted by SEQNUM from the kept events.

Host commands (one at a time, state directory on ext4, removed afterwards):

```sh
nix build ./spikes/phase0#driver-probe-pool-race-<variant>
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=100 nix run ./spikes/phase0#probe-pool-race-cpu2       # and cpu4
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=5 PROBE_STOP_AT_FAILURE=0 nix run ./spikes/phase0#probe-pool-race-<delay variant>
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=3 PROBE_STOP_AT_FAILURE=0 PROBE_RECOVER=change nix run ./spikes/phase0#probe-pool-race-delay-both   # and scan
```

## Step 2a: reboots with 2 and 4 vCPUs (nodes.nix options, nothing delayed)

Stopped at the first failure. The 2-vCPU run 1 was cut off after 67 boots by the host reboot (no failure up to
then); run 2 added 33 boots, so 100 boots with 2 vCPUs exist, in two runs on two host kernels. The 4-vCPU run
stopped at its first failure, boot 15 (the 16th).

| | 2 vCPUs, run 1 (host 7.1.5, code `72d9fed`) | 2 vCPUs, run 2 (host 7.2.6, `a9d8853`) | 4 vCPUs (host 7.2.6, `a9d8853`) |
| --- | --- | --- | --- |
| boots | 67 (interrupted) | 33 | 16 (stopped at the failure) |
| boots with the pool and every mount up | 67 | 33 | 15 |
| failed boots (S1.11 signature) | 0 | 0 | 1 (boot 15) |
| not-ready member stuck | 0 | 0 | 1 (the failed boot) |
| retriggers reaching the not-ready member (of 4 possible: B and three archive disks): 0 / 2 / 3 / 4 | 0 / 2 / 3 / 62 | 0 / 1 / 2 / 30 | 1 / 1 / 2 / 12 |
| scan gap of the two members (ms), min / median / max | 0.041 / 2.588 / 70.525 | 0.055 / 3.001 / 40.928 | 0.042 / 1.546 / 3.745 |
| boots up with scan gap < 3 ms | 37 | 16 | 11 |
| label unit active minus job start (s), min / median / max | 0.547 / 0.662 / 1.027 | 0.591 / 0.642 / 0.820 | 0.430 / 0.461 / 0.563 (15 boots) |
| not-ready member ready minus its not-ready add (ms), min / median / max | 126 / 218 / 291 | 171 / 199 / 256 | 69 / 87 / 110 (15 boots) |
| pool link owned by the not-ready member at the end | 67 | 33 | 16 |
| host 1-min load at start, min / median / max; host `io some avg10` max | 1.21 / 3.08 / 4.53; 0.68 | 0.83 / 1.69 / 2.63; 0.00 | 1.78 / 2.35 / 4.56; 0.00 |

With 2 vCPUs 0 of 100 boots failed (below 2.95 % per boot at 95 %, exact binomial, under these conditions); at
least two of the four retriggers reached the not-ready member in every boot. With 4 vCPUs boot 15 failed on its
own, nothing delayed or injected, with the S1.11 signature. Its journal, verbatim:

```text
[4.791389] server systemd[1]: Expecting device /dev/disk/by-label/spikepool...
[5.222126] server kernel: Btrfs loaded, zoned=yes, fsverity=yes
[5.226032] server kernel: BTRFS: device label spikepool devid 1 transid 9 /dev/vdb (253:16) scanned by (udev-worker) (382)
[5.226088] server kernel: BTRFS: device label spikepool devid 2 transid 9 /dev/vdc (253:32) scanned by (udev-worker) (386)
[14.906201] server systemd[1]: dev-disk-by\x2dlabel-spikepool.device: Job dev-disk-by\x2dlabel-spikepool.device/start timed out.
[14.906323] server systemd[1]: Timed out waiting for device /dev/disk/by-label/spikepool.
[14.906440] server systemd[1]: Dependency failed for /mnt/pool.
[14.906496] server systemd[1]: Dependency failed for /mnt/cache.
[14.906595] server systemd[1]: Dependency failed for /mnt/base.
[14.906669] server systemd[1]: mnt-pool.mount: Job mnt-pool.mount/start failed with result 'dependency'.
[14.906703] server systemd[1]: dev-disk-by\x2dlabel-spikepool.device: Job dev-disk-by\x2dlabel-spikepool.device/start failed with result 'timeout'.
```

Monitor log of the two members (complete; no other event of either member followed):

```text
KERNEL [4.883496] add vdb SEQNUM=1699
KERNEL [4.883634] add vdc SEQNUM=1702
UDEV   [5.244091] add vdb SEQNUM=1699 ID_BTRFS_READY=0 SYSTEMD_READY=0
UDEV   [5.251037] add vdc SEQNUM=1702 ID_BTRFS_READY=1
```

The pool link `/dev/disk/by-label/spikepool -> ../../vdb` (lstat ctime 5.2409 s, before vdc's broadcast at 5.2510 s);
boot capture (`boot-15-diagnostics.json`): `udevadm info /dev/vdb`: `ID_BTRFS_READY=0`, `SYSTEMD_READY=0`;
`/dev/vdc`: `ID_BTRFS_READY=1`; `udevadm settle --timeout=0` exit 0; missing mount `/mnt/pool`; the pool device unit
was still not active after a further 60 s. Scan gap 0.056 ms, workers 382 (vdb) and 386 (vdc); udevd 11 workers at
most; host 1-min load 2.34, host `io some avg10` 0.00. Both elements of the hypothesis are visible: no retrigger
reached vdb (that all four ran before vdb's database write is the inference; their enumeration time is not logged),
and vdb owned the link before PID 1 handled vdc's ready event. In this boot the btrfs module was loaded 4 ms before
the two scans (`Btrfs loaded` at 5.222 s), so the first `btrfs ready` calls of all workers met at once; whether that
is why the scans are often so close was not measured (the other boots did not keep that line).

## Step 2b: deterministic reproduction

Two probe-only udev rules (`pool_race.nix`), not stopped at a failure. The output of the first `delay` and
`delay-both` runs (host 7.1.5) was overwritten by the later runs of the same variant; their lines below were read
from it during the work. The mitigation runs (a) to (c) ran on `72d9fed` (host 7.1.5).

- `delay`: on the add event with `ID_FS_LABEL=spikepool` and `ID_BTRFS_READY=0`, `PROGRAM="sleep 2"`. PROGRAM runs
  while the rules are applied, so A's links and database are written 2 s after its ioctl.
- `delay-both`: the same with 1 s, plus on the add event with `ID_BTRFS_READY=1` a `RUN+="sleep 2"`, which runs after
  the retrigger RUN of `64-btrfs.rules` and before the broadcast to PID 1, so A's link update falls between B's
  retrigger and PID 1 handling B's event.

| | `delay` | `delay-both` |
| --- | --- | --- |
| boots (runs) | 5 (host 7.1.5, `34f8f15`) + 5 (host 7.2.6, `a9d8853`, late wait) | 5 (host 7.1.5, `72d9fed`) + 3 + 3 (host 7.2.6, `a9d8853`, late wait and recovery) |
| not-ready member stuck (no retrigger, never ready) | 10 of 10 | 11 of 11 |
| pool link owned by the stuck member at the end | 10 of 10 | 11 of 11 |
| `Found device /dev/disk/by-label/spikepool` | 10 of 10, 0.59 to 0.65 s after the job started (first 5) | 0 of 11 |
| label device unit at the end | inactive/dead in 10 | inactive/dead in 11 |
| device job timed out | 0 | 11 of 11, at 13.91 to 14.82 s |
| mnt-pool, mnt-base, mnt-cache | active in 10 | failed with result 'dependency' in 11 |
| other mounts and smbd | active | active |
| pool device unit active within a further 60 s (fixed late wait) | 0 of 5 | 0 of 6 |

`delay`: B came first, the label unit appeared through B and the pool mounted; about 2 s later the stuck A took the
link over (boot 0: link ctime 6.466 s, A's udev add 6.468 s) and the label unit went inactive/dead while
`/mnt/pool` stayed mounted (the mount `Requires=` its device, it is not bound to it). Boot 0 (host 7.1.5), monitor
log and journal (`vdb` is A):

```text
UDEV [4.482344] add vdc  ID_BTRFS_READY=1
[4.487740] server systemd[1]: Found device /dev/disk/by-label/spikepool.
UDEV [6.467519] add vdb  ID_BTRFS_READY=0 SYSTEMD_READY=0   (no change event for vdb follows)
```

`delay-both` reproduces the S1.11 signature: only the pool's label job times out, the three pool mounts fail.
Boot 0 (host 7.1.5), journal verbatim (`vdc` is A, stuck; monitor: `UDEV [6.110958] add vdc ID_BTRFS_READY=0
SYSTEMD_READY=0`, `UDEV [7.144800] add vdb ID_BTRFS_READY=1`, no change event for vdc):

```text
[4.531176] server systemd[1]: Expecting device /dev/disk/by-label/spikepool...
[5.090460] server kernel: BTRFS: device label spikepool devid 2 transid 9 /dev/vdc (253:32) scanned by (udev-worker) (364)
[5.090547] server kernel: BTRFS: device label spikepool devid 1 transid 9 /dev/vdb (253:16) scanned by (udev-worker) (366)
[14.498549] server systemd[1]: dev-disk-by\x2dlabel-spikepool.device: Job dev-disk-by\x2dlabel-spikepool.device/start timed out.
[14.498613] server systemd[1]: Timed out waiting for device /dev/disk/by-label/spikepool.
[14.498714] server systemd[1]: Dependency failed for /mnt/pool.
[14.498761] server systemd[1]: Dependency failed for /mnt/cache.
[14.498856] server systemd[1]: Dependency failed for /mnt/base.
[14.498934] server systemd[1]: mnt-pool.mount: Job mnt-pool.mount/start failed with result 'dependency'.
[14.498971] server systemd[1]: dev-disk-by\x2dlabel-spikepool.device: Job dev-disk-by\x2dlabel-spikepool.device/start failed with result 'timeout'.
```

The boot capture (`boot-0-diagnostics.json`) agrees: `/dev/disk/by-label/spikepool -> ../../vdc`; `udevadm info
/dev/vdc`: `ID_BTRFS_READY=0`, `SYSTEMD_READY=0`; `/dev/vdb`: `ID_BTRFS_READY=1`; `udevadm settle --timeout=0` exit 0
(no event pending); missing mount `/mnt/pool`. Scan gaps in these boots: 0.036 to 23 ms; the reproduction does not
depend on how close the scans are.

## Step 2c: candidate mitigations and manual recovery under `delay-both` (measured, not adopted)

| run | what changes | pool mounted | pool device job / unit |
| --- | --- | --- | --- |
| `delay-both` | nothing (reference) | 0 of 5 | timed out in 5 |
| (a) `delay-both-device-opts` | fstab read back `/dev/disk/by-label/spikepool /mnt/pool btrfs subvol=pool,device=/dev/disk/by-id/virtio-pool0,device=/dev/disk/by-id/virtio-pool1,nofail,x-systemd.device-timeout=10s 0 0` | 0 of 5 | timed out in 5, at 14.92 to 16.88 s |
| (b) `delay-both-retrigger` | `probe-pool-retrigger.service` (oneshot, after `systemd-udev-trigger.service`, before `local-fs.target`): `udevadm settle --timeout=30; udevadm trigger --action=add --settle /dev/disk/by-id/virtio-pool0 /dev/disk/by-id/virtio-pool1` | 5 of 5 | found in 5, 4.66 to 4.69 s after the job started (timeout 10 s) |
| (c) `delay-both-uuid` | fstab `/dev/disk/by-uuid/5f0a7c1e-0000-4000-8000-000000051f00 /mnt/pool btrfs subvol=pool,nofail,x-systemd.device-timeout=10s 0 0`; fsid set after `spike-mkfs` with `btrfstune -f -U` (`Fsid change finished`, `blkid` shows it on both members) | 0 of 5 | `Timed out waiting for device /dev/disk/by-uuid/5f0a7c1e-0000-4000-8000-000000051f00.` in 5 |
| after the failed boot, by hand: `udevadm trigger --action=change --settle /dev/<stuck member>` | (host 7.2.6) | by itself: 0 of 3 within 10 s; after `systemctl start mnt-pool.mount mnt-base.mount mnt-cache.mount`: 3 of 3 (exit 0) | unit active 0.09 to 0.10 s after the trigger in 3 of 3; member `ID_BTRFS_READY=1`, no `SYSTEMD_READY=0` |
| after the failed boot, by hand: `btrfs device scan /dev/<stuck member>; udevadm settle` | (host 7.2.6) | 0 of 3; `systemctl start` exit 1 (`A dependency job for mnt-pool.mount failed.`, same for mnt-base and mnt-cache) | not active within 30 s in 3 of 3; member still `ID_BTRFS_READY=0`, `SYSTEMD_READY=0` |

- (a) The mount still waits for its `What=` link (Step 1, `core/mount.c:361-366`); `device=` only reaches the kernel
  at mount time, which never comes. Every boot logged the same timeout and three 'dependency' lines.
- (b) The re-add made the stuck member ready (its next udev event `ID_BTRFS_READY=1`, 3.05 s after its stuck add in
  5 of 5) and the label unit appeared inside the 10 s timeout; every mount and smbd were active. The sleeps of the
  reproduction (1 s + 2 s, and the 2 s RUN sleep again on each re-add event, both members being ready then) account
  for most of the 4.7 s; when the service itself ran was not recorded. `local-fs.target` is ordered after it.
- (c) The UUID link is claimed by both members exactly like the label link (both carry `/dev/disk/by-uuid/<fsid>` in
  DEVLINKS) and the stuck member owned it at the end in 5 of 5; same failure.
- Manual change trigger: a change event re-runs `64-btrfs.rules` for the stuck member; the ioctl now finds the
  volume complete, the member becomes ready and PID 1 sets the label unit up within 0.1 s. The failed `nofail` mounts
  are not retried by themselves (0 of 3 within 10 s); they had to be started.
- `btrfs device scan` registers the device in the kernel (BTRFS_IOC_SCAN_DEV) but sends no uevent, so udev and PID 1
  never re-evaluate the member; the label unit stays absent and starting the mounts fails on the dependency.
- Side observation, not part of the race: in the second `delay` run one shutdown took 91 s (`systemd-shutdown[1]:
  Waiting for process: 627 (mount.fuse.merg), 631 (mount.fuse.merg)`, SIGKILL after 90 s); cause UNVERIFIED.

## What this means

Established (measured here):
- The mechanism exists and produces exactly the S1.11 signature. Of the two pool members, the one whose
  `btrfs ready` ioctl runs first is marked `SYSTEMD_READY=0` in every boot (all 155 boots of this probe); it depends on a
  retrigger that only reaches it if its udev database is written before the retrigger enumerates. When no retrigger
  reaches it and it owns the label link when PID 1 handles the other member's ready event, the label device unit is
  never set up, its job times out after 10 s and mnt-pool, mnt-base and mnt-cache fail with result 'dependency'.
  Forced with `delay-both`: 11 of 11 boots. Unforced, with 4 vCPUs: 1 of 16 boots (boot 15), with the same lines.
- With 2 vCPUs (the S1 server size) 0 of 100 boots failed; at least two of the four retriggers reached the not-ready
  member in every boot. The three single-device archive disks run the same retrigger and are part of what saves the
  pool in most boots.
- How close the two scans are says nothing: failures and successes both had gaps below 0.1 ms.
- Nothing brings the unit back by itself: in 0 of 11 forced failures and in the unforced one did the pool device unit
  become active within a further 60 s.
- Mounting by UUID (c) or adding `device=` options (a) does not prevent it (0 of 5 mounted each). A oneshot
  `udevadm trigger --action=add` of both members after `udevadm settle` (b) mounted the pool in 5 of 5 forced boots,
  inside the 10 s device timeout. After a failed boot, `udevadm trigger --action=change` of the stuck member brings
  the device unit back within 0.1 s (3 of 3), but the failed `nofail` mounts stay down until started (`systemctl
  start` then succeeded, 3 of 3); `btrfs device scan` of the stuck member changes nothing (3 of 3, the mounts cannot
  be started).
- In the `delay` case (the member missed the retrigger but the unit had already appeared) the mounted pool's label
  unit went inactive/dead later while `/mnt/pool` stayed mounted (10 of 10): systemd then shows a mounted pool
  whose device unit is dead.

Hypothesis, not established:
- That the failed S1.11 boot of the shakedown was this mechanism. Its signature matches line for line, but its
  journal is gone; UNVERIFIED.
- The failure rate per boot. With 4 vCPUs one failure in 16 boots says only that it is not rare there; with 2 vCPUs
  it is below about 3 % per boot under these conditions (idle host, server alone), and nothing was measured under the
  load of a full S1 run, where the failure was seen.
- That more parallel udev workers (4 vCPUs) make the miss more likely: one failure is not a rate comparison.
- Whether (b) also holds without the reproduction's sleeps, when the re-add races the original events, and whether
  re-adding block devices at boot has side effects elsewhere. Whether (b) holds on a non-test host with the 90 s
  default device timeout is not measured either. Nothing here is adopted; where a fix belongs is the maintainer's
  decision.

## UNVERIFIED

- That the S1.11 shakedown failure was this race (journal gone).
- The per-boot failure rate under S1 load, with the client VM running, and on real disks.
- When each retrigger enumerated relative to the not-ready member's database write (only its kernel change events
  are logged); the "all before the write" step of the unforced failure is inferred.
- Mitigation (b) outside the forced reproduction and on a non-test host; side effects of re-adding the members.
- The cause of the one 91 s shutdown (mergerfs processes, SIGKILL after 90 s) in the second `delay` run.
- Whether the systemd upstream tracker has an open report of this race (the web search found none; `#1921` is a
  different, fixed problem).

## Criteria changelog note (for T052 `S1.yaml`)

> T051f (2026-10-18, systemd 260.5, kernel 6.18.56, server VM 1536 MiB alone, nodes.nix options): the two-device
> pool's label device unit can fail to appear at boot by a race in udev, not by slowness. The pool member whose
> `btrfs ready` runs first is marked SYSTEMD_READY=0 in every boot and is released only by the
> `udevadm trigger -p ID_BTRFS_READY=0` that 64-btrfs.rules runs for the other member (and for each single-device
> btrfs disk); that trigger matches the udev database, so a member whose database is written after the last
> trigger stays not ready, and if it owns /dev/disk/by-label/spikepool when PID 1 handles the ready member, the
> device job times out and mnt-pool, mnt-base and mnt-cache fail with 'dependency'. Forced by udev rule delays:
> 11 of 11 boots; unforced: 1 of 16 boots with 4 vCPUs, 0 of 100 with 2 vCPUs, with the S1.11 lines. Scan gaps
> do not predict it. Mounting by UUID or adding device= options did not help (0 of 5 each); a oneshot
> `udevadm trigger --action=add` of both members after `udevadm settle` mounted the pool in 5 of 5 forced boots.
> After a failed boot a `udevadm trigger --action=change` of the stuck member restores the device unit in 0.1 s
> but the nofail mounts must be started again; `btrfs device scan` does not help. That the shakedown failure was
> this race is UNVERIFIED; no mount setting is adopted and a reboot count for S1.11 is still open.
