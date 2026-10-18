# Probe result: S1.11 reboot after the S1 heavy IO, with and without host IO pressure (task T051d)

Measured on 2026-10-18 with the probe `boot_mounts.nix` / `probe_boot_load.py` (setup and per-boot measurement
shared with T051a in `boot_probe_setup.py`), one VM test at a time, KVM, machine state on an ext4 disk. A probe
never writes a record; the full output of each run is `probe-boot-load.json` in the output directory of the
driver (evidence, not committed). Nothing below changes `nix/nodes.nix` or adopts a mount setting.

Question: in one of three S1 shakedown reboots (S1.11) the pool and its mounts did not come up:
`dev-disk-by\x2dlabel-spikepool.device` hit `x-systemd.device-timeout=10s` (`nix/nodes.nix:48`, with `nofail`), the two
pool members having been scanned 2.3 ms apart (S1 criteria changelog). Correction (2026-10-18): an earlier version of
this text described all five labels timing out at about 14.9 s; that is what the first boot of every S1 run logs on blank
disks (no label exists yet), not the S1.11 failure. T051a rebooted an idle server 40 times without a failure. The leading
hypothesis (UNVERIFIED before this probe): host IO and page-cache write-back pressure after the multi-GiB writes of S1.8
makes the guest's block-device probing (udev, blkid) slower than 10 s. This probe creates both conditions directly.

## Environment

- Profile `large`, the S1 disks and mounts of `nix/nodes.nix`, the tiered-cache mounts and the S1 share; the
  `server` VM alone (1536 MiB, 2 vCPUs; VM RAM 1536 MiB in total). Host: 24 CPUs, kernel 7.1.5, the
  maintainer's workstation with the desktop in use, host `MemAvailable` 15246 to 16625 MiB during the runs.
  Host write-back limits read back: `vm.dirty_bytes=268435456`, `vm.dirty_background_bytes=134217728`,
  `vm.dirty_expire_centisecs=1500`. The state directory's disk is NVMe under dm-crypt and LVM (ext4); the
  NVMe scheduler is `none`, so `ionice -c3` has no effect there (UNVERIFIED whether dm-crypt honours it).
- Guest: kernel 6.18.56, systemd 260 (260.5), btrfs-progs v6.19.1, mergerfs v2.41.1.
- The data disks are QEMU `emptyDiskImages` without a cache option (pinned nixpkgs
  `nixos/modules/virtualisation/qemu-vm.nix:1455-1464` sets only `werror=report`), so QEMU's default
  `cache=writeback` applies: guest writes go to the host page cache, a guest flush reaches the host as a flush.
- Setup per run: first boot with blank disks (its device jobs time out: no label exists yet), `spike-mkfs all`,
  the pool and archive data subvolumes mounted and `mnt-base.mount mnt-cache.mount` started as the S1 test
  script does, the S1 data set placed (`s1.fixture`, with `large/archived.bin`, 4 GiB + 1 MiB + 1 byte, on arch1).
- One cycle: the guest workload (below) on `/mnt/cache`, nothing synced; then `server.shutdown()`
  (`poweroff` through the driver) and `server.start()` at once; nothing is mounted or started by hand.
- Variants:
  - D1 (`probe-boot-load`): the options of `nix/nodes.nix` as they are, fstab read back
    `/dev/disk/by-label/arch0 /mnt/archive-content/arch0 btrfs subvol=content,nofail,x-systemd.device-timeout=10s 0 0`
    (and the same pattern for the other seven mounts); device units `JobRunningTimeoutUSec=10s`.
  - D2 (`PROBE_HOST_IO=1 probe-boot-load`): D1 plus a host
    `nice -n 19 ionice -c3 dd if=/dev/zero of=<state dir>/host-io-load.bin bs=1M count=8192 conv=fdatasync`
    in a loop, started 2 s before `server.start()` and stopped when the guest job queue is empty, the file
    deleted after each cycle; the loop stops (and the variant after that boot) below 6144 MiB `MemAvailable`.
  - D3 (`probe-boot-load-no-device-timeout`): not run. The task defines D3 only for the case that D1 or D2
    fails; neither did. Without the option the device timeout would be 5 min in the test VM
    (`DefaultDeviceTimeoutUSec=5min`, read back: the NixOS test instrumentation sets
    `DefaultDeviceTimeoutSec = 300`), not systemd's 90 s.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-boot-load ./spikes/phase0#driver-probe-boot-load-no-device-timeout
SPIKE_STATE_DIR=<short directory on an ext4 disk> PROBE_BOOTS=10 nix run ./spikes/phase0#probe-boot-load                  # D1
SPIKE_STATE_DIR=<short directory on an ext4 disk> PROBE_BOOTS=10 PROBE_HOST_IO=1 nix run ./spikes/phase0#probe-boot-load  # D2
```

Before each run: `pgrep -c qemu-system` (0) and `free -m` (available 15837 MiB before D1, 16144 MiB before D2).
On the host, by the probe: before each start `os.getloadavg()`, the first line of `/proc/pressure/{cpu,io,memory}`
and `MemAvailable`/`Dirty`/`Writeback` of `/proc/meminfo`; from the start to the empty guest job queue
`/proc/pressure/io` and `/proc/meminfo` every 0.5 s. The IO stall share of a window is the growth of
`/proc/pressure/io` `some total` (or `full total`) divided by the window length.

## Guest commands (verbatim from the probe output)

Workload of one cycle, in this order (S1.8 baseline case through the share, redone locally on the cache pool,
then the read of every file that the S1.11 manifest does; the flip offset is 2^32 + 1 + cycle):

```sh
sha256sum < /mnt/cache/large/archived.bin        # twice (S1.8 reads it through mount.cifs and smbclient)
cat -- /mnt/cache/large/archived.bin > /mnt/cache/large/copy-probe.bin
sha256sum < /mnt/cache/large/copy-probe.bin
rm -f -- /mnt/cache/large/copy-probe.bin
python3 -c '<testlib.fixtures content_chunks writer, seed S1>' /mnt/cache/large/new-through-share.bin large/new-through-share.bin 4297064451
sha256sum < /mnt/cache/large/new-through-share.bin
python3 -c '<invert the byte at the offset>' /mnt/cache/large/archived.bin 4294967297
sha256sum < /mnt/cache/large/archived.bin
sha256sum < /mnt/archive/arch1/branch/large/archived.bin
rm -f -- /mnt/cache/large/new-through-share.bin
find /mnt/cache -type f -exec sha256sum -- {} + > /dev/null
```

Then `grep -E '^(Dirty|Writeback):' /proc/meminfo` (guest, right before the shutdown). After each boot, after
`multi-user.target` and an empty job queue: the commands of T051a (`boot_mounts.results.md`, "Measurement"),
and a guest Python that reads `os.lstat("/dev/disk/by-label/<label>").st_ctime` as CLOCK_MONOTONIC seconds
(the last time udev wrote the link). For a boot that did not come up the probe would also poll for the links
up to 120 s, run `udevadm info --query=all` on every label and disk, `journalctl -b -u systemd-udevd`,
`udevadm settle --timeout=0`, `systemctl list-jobs` and start the mount units; no boot needed it.

## Results

| | D1 (heavy IO, idle host) | D2 (heavy IO + host dd during the boot) |
| --- | --- | --- |
| boots | 10 | 10 |
| boots with every mount active (`Result=success`) and smbd active | 10 | 10 |
| failure rate | 0/10 | 0/10 |
| device jobs started (`Expecting device`), median (min to max) | 4.62 s (4.57 to 4.69) | 6.77 s (5.21 to 9.87) |
| by-label device unit active, first label, median (min to max) | 4.90 s (4.82 to 4.98) | 7.07 s (5.54 to 10.55) |
| by-label device unit active, last label, median (min to max) | 5.27 s (5.16 to 5.48) | 7.47 s (5.88 to 10.93) |
| longest wait of a device job (unit active minus job start), median (max) | 0.62 s (0.79 s) | 0.66 s (1.06 s) |
| last mount unit active, median (max) | 6.58 s (6.64 s) | 9.33 s (11.87 s) |
| guest userspace start (`UserspaceTimestampMonotonic`), min to max | 3.57 to 3.69 s | 3.99 to 8.60 s |
| host start to empty job queue, median (min to max) | 15.1 s (14.9 to 16.0) | 21.4 s (18.4 to 30.3) |
| host IO stall share, start to empty job queue, `some`, median (min to max) | 0.6 % (0.2 to 0.8) | 41 % (30 to 47) |
| the same, `full`, median (min to max) | 0.6 % (0.2 to 0.8) | 39 % (28 to 44) |
| host PSI `io some avg10` during the boot, max over boots | 0.68 | 47.97 |
| host `Dirty` / `Writeback` during the boot, max over boots | 3 MiB / 4 MiB | 173 MiB / 241 MiB |
| host `Dirty` right before the start (KiB), min to max | 632 to 2960 | (taken before the dd starts) |
| guest `Dirty` right before the shutdown | 0 to 64 kB | 0 to 64 kB |
| host 1-min load at start, median (min to max) | 3.69 (2.79 to 6.50) | 5.33 (3.41 to 7.76) |
| host `MemAvailable` during the boot, min | 15246 MiB | 15432 MiB |
| dd runs per boot (started / completed), memory guard tripped | n/a | 1/0 in 9 boots, 2/1 in one; never |
| workload per cycle, median (min to max) | 150 s (136 to 216) | 168 s (139 to 196) |
| wall time of the run (incl. first boot and setup) | 29 min 51 s (probe 1787 s) | 32 min 57 s (probe 1969 s) |

D2's `shutdown_s` (3.1 to 3.3 s) includes the 2 s dd lead; D1's is 1.18 to 1.30 s.

Per label, time to the active by-label device unit (s, min / median / max, none missing):

| label | D1 | D2 |
| --- | --- | --- |
| spikepool | 5.147 / 5.264 / 5.478 | 5.874 / 7.467 / 10.906 |
| arch0 | 5.151 / 5.221 / 5.304 | 5.881 / 7.409 / 10.890 |
| arch1 | 5.141 / 5.271 / 5.332 | 5.883 / 7.415 / 10.916 |
| arch2 | 5.125 / 5.261 / 5.327 | 5.872 / 7.412 / 10.930 |
| parity0 | 4.824 / 4.897 / 4.981 | 5.540 / 7.071 / 10.555 |

The last change time of the by-label links lay between 0.20 s before and 1.18 s after the device unit became
active (D1), as expected of a link that udev rewrites on later change events. The two pool members were scanned
by two udev workers 0.06 to 28.94 ms apart in D1 (7 of 10 boots under 3 ms) and 0.87 to 8.22 ms apart in D2
(4 of 10 under 3 ms). parity0 logs no `Found device` line; its device unit is active like the others.

Slowest D2 boot (boot 3, IO stall share 43 %, verbatim):

```text
[9.867483] server systemd[1]: Expecting device /dev/disk/by-label/arch0...
[9.867613] server systemd[1]: Expecting device /dev/disk/by-label/spikepool...
[10.868158] server kernel: BTRFS: device label spikepool devid 1 transid 28 /dev/vdb (253:16) scanned by (udev-worker) (388)
[10.873052] server kernel: BTRFS: device label spikepool devid 2 transid 28 /dev/vdc (253:32) scanned by (udev-worker) (392)
[10.890029] server systemd[1]: Found device /dev/disk/by-label/arch0.
[10.905831] server systemd[1]: Found device /dev/disk/by-label/spikepool.
Startup finished in 590ms (kernel) + 8.010s (initrd) + 20.862s (userspace) = 29.463s
```

A one-cycle trial of D2 before the runs (not counted, same code apart from one line wrapped for the linter)
had an IO stall share of 61 % (`io some avg10` up to 63.8): the device jobs started at 20.04 s and the
devices were active 0.29 to 0.64 s later; every mount came up.

## What this means

- The failure was not reproduced: 0 of 20 boots, 0 of 10 per variant. 0/10 bounds the failure rate under each
  condition below 25.9 % per boot at 95 % confidence (exact binomial, 1 - 0.05^(1/10)); D1 and D2 together
  (0/20) below 13.9 %. A rare failure is not excluded. Together with T051a's 40 idle boots it is 0 of 60
  reboots, but the conditions differ, so the runs are not one sample.
- Write-back pressure after the S1.8 writes (the first part of the hypothesis) was not present at the reboot:
  in D1 the host held 0.6 to 2.9 MiB of dirty data right before the start, the guest 0 to 64 kB, and the host
  IO stall during the boot was below 1 %. The S1 order itself explains it: after the last write of S1.8 come
  minutes of reads, ending with the S1.11 manifest read (17 s here), so the data is written back before the
  shutdown. This holds for the order and the host measured here, not necessarily for the S1 run.
- Deliberate host IO pressure (D2, 30 to 47 % of the boot window with some tasks stalled on IO, up to 61 % in the trial)
  slowed the whole guest boot (userspace starting up to 8.6 s instead of 3.6 s, device jobs starting up to
  20 s), but not the device jobs themselves: systemd starts the 10 s timer when it queues the job, and every
  device was active at most 1.06 s after that (shortest wait per boot 0.24 to 0.69 s, longest 0.59 to 1.06 s). Across the D2 boots the stall share correlates with the start of
  the jobs (Pearson 0.45) and not with the wait (−0.04). For the timeout to fire, udev must take more than
  10 s for a device after its job starts, about ten times the slowest wait measured under the strongest load
  produced here.
- So the hypothesis "host IO pressure makes the device probing slower than 10 s" is not supported under the
  conditions created here: a host IO stall of up to about 50 % does not come close. Not refuted: a stall near
  100 % for more than 10 s, memory pressure on the host (swap), or a guest-side cause (a lost or late udev
  event, the 64-btrfs.rules retrigger, a stuck udev worker) were not produced.
- Failure versus host IO pressure: no failure occurred, so no correlation with failure can be computed; the
  failed S1.11 boot has no host pressure reading.

## UNVERIFIED

- The cause of the S1.11 failure. Neither idle reboots (T051a) nor reboots after the S1 heavy IO with or
  without host IO pressure (this probe) reproduced it.
- Whether the S1 run had conditions this probe did not create: the client VM running, the two S1.8 cases
  (baseline and `passthrough.io=rw,cache.files=auto-full`, which remounts the cache pool), S1.9 (filler, three
  failing 256 MiB writes, its `sync -f`) and S1.10 before the reboot, host swap activity, or a host IO stall
  near 100 %. The probe reproduces the S1.8 baseline case and the S1.11 read, server-local instead of through
  SMB.
- Whether `ionice -c3` lowered the dd's priority at all (scheduler `none` on the NVMe, dm-crypt in between);
  the desktop stayed in use, the pressure figures are what it got.
- The time each by-label link first appeared: the link's ctime is its last rewrite, so only the device unit
  activation and the `Found device` line time the first appearance.
- Whether the systemd default of 90 s (outside the test instrumentation) behaves like the 5 min of the test VM.

## Criteria changelog note (for T052 `S1.yaml`)

> Probe T051d (2026-10-18, systemd 260.5, kernel 6.18.56, server VM 1536 MiB alone, nofail and
> x-systemd.device-timeout=10s as in nodes.nix): 20 reboots of the S1 server, each right after the IO of the
> S1.8 baseline case and the S1.11 manifest read on the cache pool (about 8 GiB written, 4 GiB files read
> eight times, nothing synced), 10 with an idle host and 10 with a host dd ... conv=fdatasync load during the
> boot (host IO stall 30 to 47 % of the boot window), came up 20 of 20 times with every mount and smbd active.
> Right before the reboot the host held under 3 MiB of dirty data. Host IO pressure delayed the start of the
> device jobs (up to 9.9 s, 20 s in a trial at 61 % stall) but every by-label device was active at most 1.06 s
> after its job started, against the 10 s timeout. Host IO pressure as the cause of the S1.11 failure is not
> supported; the cause stays UNVERIFIED, and so does any mount setting that would prevent it. 0/10 per
> condition bounds the failure rate below 26 % (95 %).
