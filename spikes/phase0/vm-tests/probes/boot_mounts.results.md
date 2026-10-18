# Probe result: boot of the S1 server, by-label devices and mounts (task T051a)

Measured on 2026-10-18 with the probe `boot_mounts.nix` / `probe_boot_mounts.py`, one VM test at a time,
KVM, machine state on an ext4 disk. A probe never writes a record; the full output of each run is
`probe-boot-mounts.json` in the output directory of the driver (evidence, not committed). Nothing below
changes `nix/nodes.nix` or adopts a mount setting.

Question (S1 shakedown T051, S1.11): one of three S1 reboots came up without its mounts: device jobs hit
`x-systemd.device-timeout=10s` (`nix/nodes.nix:48`, with `nofail`). Two descriptions of that boot exist and
differ: the S1 criteria changelog names `dev-disk-by\x2dlabel-spikepool.device` alone (pool members scanned
2.3 ms apart; mnt-pool, mnt-base and mnt-cache failed with result 'dependency'); the brief of this task names
every labelled device (arch0, arch1, arch2, parity0, spikepool) timing out at about 14.9 s in shakedown run 3.
The probe records every label, so it covers both. How often does it happen, and why?

## Environment

- Profile `large` (the S1 disks and mount definitions of `nix/nodes.nix`), the tiered-cache mounts and the
  S1 share; the `server` VM alone (1536 MiB, 2 vCPUs). Host: 24 CPUs, the maintainer's workstation with the
  desktop in use.
- Guest: kernel 6.18.56, systemd 260 (260.5), btrfs-progs v6.19.1, mergerfs v2.41.1.
- First boot with blank disks, `spike-mkfs all`, then N shutdowns (`poweroff` through the driver) and
  starts; nothing is mounted or started by hand.
- Variants:
  - A (`probe-boot-mounts`): the options of `nix/nodes.nix` as they are, `nofail,x-systemd.device-timeout=10s`
    (fstab read back from the guest: `/dev/disk/by-label/spikepool /mnt/pool btrfs subvol=pool,nofail,x-systemd.device-timeout=10s 0 0`,
    the same pattern for the other seven mounts). Device units: `JobRunningTimeoutUSec=10s`.
  - B (`probe-boot-mounts-no-device-timeout`): the same eight mounts without `x-systemd.device-timeout`
    (fstab read back: `/dev/disk/by-label/spikepool /mnt/pool btrfs subvol=pool,nofail 0 0`, ...), nothing
    else changed. Device units: `JobRunningTimeoutUSec=5min`. **Not 90 s**: the NixOS test
    instrumentation sets `DefaultDeviceTimeoutSec = 300` in every test VM
    (`nixos/modules/testing/test-instrumentation.nix:80` of the pinned nixpkgs), read back as
    `DefaultDeviceTimeoutUSec=5min`. On a non-test NixOS host the systemd default of 90 s
    (systemd-system.conf(5), `DefaultDeviceTimeoutSec=`) would apply instead.
  - C: not run. The task defines C only for the case that A or B still fails; neither did.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-boot-mounts ./spikes/phase0#driver-probe-boot-mounts-no-device-timeout
SPIKE_STATE_DIR=<short directory on a disk> PROBE_BOOTS=20 nix run ./spikes/phase0#probe-boot-mounts
SPIKE_STATE_DIR=<short directory on a disk> PROBE_BOOTS=20 nix run ./spikes/phase0#probe-boot-mounts-no-device-timeout
```

## Measurement (guest commands, verbatim from the probe output)

Once: `spike-mkfs all`, `sync`, `cat /etc/fstab`, and `systemd-escape -p --suffix=device /dev/disk/by-label/<label>`
and `systemd-escape -p --suffix=mount <mount point>` for the unit names. Per boot, after `multi-user.target`
and an empty job queue:

```sh
systemctl show -p InitRDTimestampMonotonic,UserspaceTimestampMonotonic,FinishTimestampMonotonic,DefaultDeviceTimeoutUSec
journalctl -b -o short-monotonic --no-pager
systemctl show -p ActiveState,SubState,Result,ActiveEnterTimestampMonotonic,InactiveExitTimestampMonotonic,JobRunningTimeoutUSec 'dev-disk-by\x2dlabel-spikepool.device'   # and arch0, arch1, arch2, parity0
readlink -f /dev/disk/by-label/<label>
udevadm info --query=property --name=/dev/disk/by-id/virtio-pool0   # and pool1, arch0, arch1, arch2, parity0
systemctl show -p ActiveState,SubState,Result,ActiveEnterTimestampMonotonic,InactiveExitTimestampMonotonic,JobRunningTimeoutUSec mnt-pool.mount   # and the other nine mount units
systemd-analyze
systemctl is-active samba-smbd.service
```

On the host, right before each start: `os.getloadavg()` and the first line of `/proc/pressure/{cpu,io,memory}`.
Guest times are CLOCK_MONOTONIC seconds since the kernel started. The device job start is the journal line
`Expecting device /dev/disk/by-label/<label>...`.

## Results

| | A (device-timeout 10 s) | B (no option, 5 min in the test VM) |
| --- | --- | --- |
| boots | 20 | 20 |
| boots with every mount active (`ActiveState=active`, `Result=success`) and smbd active | 20 | 20 |
| failure rate | 0/20 | 0/20 |
| device jobs started (`Expecting device`), median (min to max) | 4.76 s (3.93 to 4.97) | 4.74 s (3.97 to 4.89) |
| by-label device unit active, first label, median (min to max) | 5.06 s (4.22 to 5.32) | 5.06 s (4.26 to 5.20) |
| by-label device unit active, last label, median (min to max) | 5.46 s (4.73 to 5.69) | 5.41 s (4.58 to 5.58) |
| longest wait of a device job (unit active minus job start), median (max) | 0.66 s (0.86 s) | 0.67 s (0.79 s) |
| last mount unit active, median (max) | 6.67 s (6.87 s) | 6.66 s (6.81 s) |
| host start to empty job queue, median (min to max) | 14.2 s (12.9 to 16.4) | 15.0 s (13.9 to 16.2) |
| host 1-min load at start, median (min to max) | 2.99 (2.27 to 3.49) | 2.83 (1.93 to 3.92) |
| host PSI `io some avg10`, max | 5.38 | 0.39 |
| host PSI `cpu some avg10`, max | 0.63 | 0.32 |
| wall time of the run (incl. first boot) | 445 s | 742 s (the blank-disk first boot waits 5 min) |

Per label, time to the active by-label device unit (s, min / median / max, none missing):

| label | A | B |
| --- | --- | --- |
| spikepool | 4.732 / 5.458 / 5.688 | 4.576 / 5.402 / 5.578 |
| arch0 | 4.549 / 5.408 / 5.660 | 4.539 / 5.396 / 5.548 |
| arch1 | 4.587 / 5.400 / 5.678 | 4.562 / 5.398 / 5.534 |
| arch2 | 4.559 / 5.392 / 5.669 | 4.572 / 5.383 / 5.561 |
| parity0 | 4.225 / 5.063 / 5.318 | 4.256 / 5.057 / 5.202 |

The two pool members were scanned by two udev workers (`BTRFS: device label spikepool devid N ... scanned by
(udev-worker)`) between 0.08 and 19.98 ms apart (A) and 0.04 and 13.73 ms apart (B); 10 boots of A and 12 of
B had the two scans less than 3 ms apart, and all of them came up. In every boot both pool members ended with
`ID_BTRFS_READY=1` and no `SYSTEMD_READY=0`. Example (A, boot 8, verbatim):

```text
[4.674970] server systemd[1]: Expecting device /dev/disk/by-label/arch0...
[4.675122] server systemd[1]: Expecting device /dev/disk/by-label/spikepool...
[5.280569] server kernel: BTRFS: device label spikepool devid 1 transid 9 /dev/vdb (253:16) scanned by (udev-worker) (363)
[5.280653] server kernel: BTRFS: device label spikepool devid 2 transid 9 /dev/vdc (253:32) scanned by (udev-worker) (360)
[5.295518] server systemd[1]: Found device /dev/disk/by-label/arch2.
[5.316760] server systemd[1]: Found device /dev/disk/by-label/spikepool.
[5.331622] server systemd[1]: Found device /dev/disk/by-label/arch0.
```

## What this means

- The failure was not reproduced: 0 of 40 boots, 0 of 20 per variant. With 0/20 the failure rate under
  these conditions is below 13.9 % per boot at 95 % confidence (exact binomial bound, 1 - 0.05^(1/20)); it says nothing
  about conditions this probe did not create.
- Under these conditions every by-label device appears 0.6 to 0.9 s after systemd starts waiting for it, so
  the 10 s device timeout has a margin of more than 9 s. The jobs start at about 4.8 s, so a timeout fires at
  about 14.8 s, which matches the reported time of about 14.9 s. If all five labels timed out (the brief's
  description), every udev event of the boot was delayed by more than ten times what was measured here, and
  a btrfs multi-device race cannot explain it: `parity0` is ext4 and the archive disks are single-device
  btrfs.
- If only `spikepool` timed out (the changelog's description), a race of the two pool members is the
  candidate. It did not show here: scans less than 3 ms apart came up 22 times, so a gap of 2.3 ms is not a
  sufficient cause; whether it is a necessary one is not measured.
- Failure versus host load: no failure occurred, so no correlation can be computed. The host load during
  the probe (1-min load 1.9 to 3.9 on 24 CPUs, IO pressure at most 5.4 % avg10) was lower than in an S1 run,
  where the client VM runs as well and S1.8 writes several GiB of disk images shortly before S1.11. The
  probe measured nothing under that load.
- Where the fix belongs (harness or system under test) is the maintainer's decision. The evidence only
  shows that, without the condition of the failing boot, the current options came up 20 of 20 times and
  removing the device timeout changed nothing measurable.

## UNVERIFIED

- The cause of the S1 failure. The probe could not reproduce it; that the S1 run had higher host IO or CPU
  load at the reboot, and that this delayed udev, is a hypothesis, not measured.
- Behaviour under host load comparable to an S1 run (client VM, multi-GiB writes before the reboot). Not
  generated, to keep the shared workstation usable.
- Whether the 64-btrfs.rules retrigger (`udevadm trigger -s block -p ID_BTRFS_READY=0`) can miss a pool member
  whose first event is still in flight: not observed in 40 boots; final udev state only, not the
  intermediate state per event.
- Whether the systemd default of 90 s (outside the test instrumentation) behaves like the 5 min measured in B.

## Criteria changelog note (for T052 `S1.yaml`)

> Probe T051a (2026-10-18, systemd 260.5, kernel 6.18.56, server VM 1536 MiB alone, host 1-min load 1.9 to
> 3.9): 40 reboots of the S1 server, 20 with the options of nodes.nix (nofail,
> x-systemd.device-timeout=10s) and 20 without the device timeout (5 min in the test VM, set by the NixOS test
> instrumentation, not systemd's 90 s), came up 40 of 40 times with every mount and smbd active. The device
> jobs started at about 4.8 s and every by-label device was active 0.6 to 0.9 s later, pool members scanned
> as little as 0.04 ms apart included. The S1.11 failure (all five labels timed out at about 14.9 s) was not
> reproduced; its cause stays UNVERIFIED, and so does any mount setting that would prevent it. One reboot
> per run cannot show a race whose rate is below about 14 % (0/20, 95 %).
