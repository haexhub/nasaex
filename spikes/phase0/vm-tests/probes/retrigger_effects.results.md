# Probe result: side effects of the pool retrigger unit (task T051g)

Measured on 2026-10-19 with the probe `retrigger_effects.nix` / `probe_retrigger_effects.py` (prelude
`retrigger_prelude.py`, first boot and helpers of `boot_probe_setup.py`, guest observers `retrigger_observe.py`,
parsing and summary in `retrigger_effects.py`) and, for (5a), the unchanged driver `delay-both-retrigger` of T051f.
One VM test at a time, KVM, machine state on an ext4 disk, removed after each run. A probe never writes a record; the
full output of each run is `probe-retrigger-effects.json` (`probe-pool-race.json` for 5a) in the output directory of
the driver (evidence, not committed). Nothing below changes `nix/nodes.nix` or `nix/tiered-cache.nix` or adopts the
unit; the numbers are measurements, not a recommendation.

Question: T051f found that a oneshot unit re-adding both pool members after `udevadm settle` rescued the forced pool
race (`delay-both`, 5 of 5). Before it can become a required setting its side effects must be known: does it lengthen
or reorder the boot, does re-adding mounted members disturb the mounts, smbd or a reader of the share, what does it do
when a member is missing or appears late, is running it again harmless, and where are its limits?

**Harm first.** No boot hang beyond the unit's own 30 s settle limit, no read error through the share, no restart of a
mount or of smbd, no write error was measured in any run. Measured costs and one unexplained difference:
- When udev does not settle within 30 s (forced: a 31 s rule delay), the unit holds `local-fs.target` until
  `udevadm settle` gives up: empty job queue 42.3 s after start in 2 of 2 boots against 21.9 s without the unit, and
  the unit fails (`Timed out while waiting for udev queue to empty.`, exit 1) without re-adding anything.
- With a pool member missing, the unit fails in 15 of 15 boots (`Failed to open the device
  '/dev/disk/by-id/virtio-pool0': No such device`, exit 1, unit state `failed`) and re-adds **neither** member; it
  does not delay or block those boots.
- At shutdown a mergerfs mount helper outlived its mount unit in 17 of 31 shutdowns with the unit against 8 of 31
  without it (Fisher exact, two-sided p = 0.037); 2 against 1 of these waited 90 s for SIGKILL. The pool was
  unmounted before the wait in the inspected cases. Why this differs is UNVERIFIED (Step 2, side observations).
- In the first idempotence run two of five 20 s writes had one write+fsync of 1.9 s and 1.3 s, not timed; a second run
  with timing and controls had none over 100 ms in 11 repetitions. Cause UNVERIFIED.

## Step 1: environment and what is measured

- The S1 server exactly as T051f boots it: profile `large` with every labelled disk of `nix/nodes.nix`, all eight
  mounts with `nofail,x-systemd.device-timeout=10s`, the tiered-cache mounts (`/mnt/base`, `/mnt/cache`, mergerfs)
  and the S1 share (smbd), 1536 MiB, 2 vCPUs (`nproc` read back 2), plus the udev monitor of T051f. Guest: kernel
  6.18.56, systemd 260 (260.5), btrfs-progs 6.19.1, mergerfs 2.41.1. Host: 24 CPUs, the maintainer's workstation with
  the desktop in use; before every run no other QEMU process and at least 10 GiB available memory (checked by the run
  wrapper, logged with the host load).
- The unit is `retrigger` of T051f, moved without change into `pool_race_modules.nix`; the store path of its unit
  file is the same in every variant here and in `delay-both-retrigger` (`...-unit-probe-pool-retrigger.service`
  `hpxxxg1q...`), and the server systems of all T051f and T051a variants evaluate to the same derivations as before.
  `systemctl cat` in the guest:

  ```text
  [Unit]
  After=systemd-udev-trigger.service
  Before=local-fs.target
  DefaultDependencies=false
  Description=Probe T051f: re-add the pool members after udev settled
  [Service]
  ExecStart=.../probe-pool-retrigger-start      # udevadm settle --timeout=30
  RemainAfterExit=true                           # udevadm trigger --action=add --settle \
  Type=oneshot                                   #   /dev/disk/by-id/virtio-pool0 /dev/disk/by-id/virtio-pool1
  [Install]
  WantedBy=local-fs.target
  ```
- Added for this probe only, in every variant: `probe-reader.service` and `probe-mountlog.service`, started at
  `sysinit.target` without default dependencies. The reader reads `/mnt/cache/probe-t051g/reader.bin` (through
  mergerfs, the share path) and `/mnt/pool/probe-t051g/reader.bin` every 20 ms whole by path and through one file
  descriptor kept open after the first good open (keys `fd:...`), compares with the expected 1 MiB, and logs every
  change of outcome (`ok`, `mismatch`, errno name). The mount logger logs every change of the mount table under
  `/mnt` (poll on `/proc/self/mountinfo`). The reader file is written once through `/mnt/cache` in a boot before the
  measured ones (it lands on `/mnt/pool`, create policy `ff`, checked).
- Per boot, after `multi-user.target` and an empty job queue: host time from start to both; state, result and
  monotonic activation time of every mount, the pool label device unit, `local-fs.target`, `multi-user.target`,
  smbd (also `NRestarts`, `MainPID`) and the unit (exit status, start and exit time, `journalctl -u`); the activation
  order; per block device the kernel and udev events of the monitor; journal line counts (`Found device`,
  `Mounting /mnt/`, `Mounted /mnt/`, `Unmount`, SMB start/stop, device time-outs, dependency failures) with the lines;
  the reader's changes and the mount events; the race data of T051f. "Up": every mount active with result success and
  smbd active.
- Variants without and with the unit: `plain` / `retrigger` (normal boots), `no-pool0[-retrigger]`,
  `no-pool1[-retrigger]` (the member is left out of `virtualisation.emptyDiskImages`; the probe attaches it through
  `QEMU_OPTS` only for the formatting boot and the boot that writes the reader file, then boots without it and after
  each measured boot hot-plugs the same image with `drive_add` / `device_add` on the QEMU monitor), `sweep` /
  `sweep-retrigger` (the `delay-both` reproduction with the rule delays read per boot from
  `/var/lib/probe-t051g-delay`: `PROGRAM` sleep D on the not-ready member's add event, `RUN+` sleep R on every ready
  add event, as `delayNotReady` / `delayReady` of T051f).
- Code: runs (1), the first idempotence run and `no-pool0` on `aa7a335`; the later runs on the working tree committed
  as `4f8cdf2` (adds the timing of slow writes and the control repetitions; the boot measurement is the same code).

Host commands (state directory on ext4, removed afterwards; `SPIKE_STATE_DIR` a short path):

```sh
nix build ./spikes/phase0#driver-probe-retrigger-effects-<variant>
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=30 nix run ./spikes/phase0#probe-retrigger-effects-plain        # and -retrigger
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=5 nix run ./spikes/phase0#probe-retrigger-effects-no-pool0       # -no-pool0-retrigger, -no-pool1[-retrigger]
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=1 PROBE_IDEMPOTENCE=control,hand,control,hand,control,hand,control,restart,control,restart \
  nix run ./spikes/phase0#probe-retrigger-effects-retrigger
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=10 PROBE_STOP_AT_FAILURE=0 nix run ./spikes/phase0#probe-pool-race-delay-both-retrigger
SPIKE_STATE_DIR=<dir> PROBE_SWEEP=1:2,2:3,3:4,4:5,6:7,31:32 PROBE_SWEEP_BOOTS=2 nix run ./spikes/phase0#probe-retrigger-effects-sweep-retrigger
SPIKE_STATE_DIR=<dir> PROBE_SWEEP=1:2,2:3,3:4,4:5,6:7,31:32 PROBE_SWEEP_BOOTS=1 nix run ./spikes/phase0#probe-retrigger-effects-sweep
```

Guest commands per boot (besides the unit's own): `systemctl show -p LoadState,ActiveState,SubState,Result,
ActiveEnterTimestampMonotonic,InactiveEnterTimestampMonotonic,ExecMainStatus,ExecMainStartTimestampMonotonic,
ExecMainExitTimestampMonotonic,NRestarts,MainPID,InvocationID <unit>`, `systemctl show -p FinishTimestampMonotonic`,
`journalctl -b -o short-monotonic`, `journalctl -b -u probe-pool-retrigger.service -o short-monotonic`,
`cat /run/probe-udev-monitor.log /run/probe-reader.log /run/probe-mounts.log`, `readlink -e /dev/disk/by-id/virtio-*`,
`readlink -f /dev/disk/by-label/spikepool`, `systemd-analyze`.

## Step 2: results

### (1) Unforced normal boots, 30 without and 30 with the unit (2 vCPUs)

Host load at run start 2.20 (without) and 4.02 (with). Times are guest monotonic seconds since the kernel started,
min / median / max over 30 boots.

| | without the unit (`plain`) | with the unit (`retrigger`) |
| --- | --- | --- |
| boots up (every mount and smbd) | 30 of 30 | 30 of 30 |
| unit result / exit status | (absent) | success / 0 in 30 of 30 |
| unit journal | (none) | `Starting Probe T051f: re-add the pool members after udev settled...`, `Finished ...` in 30 of 30, no other line |
| unit start / exit | | 4.072 / 4.895 / 5.436; 5.000 / 5.863 / 9.922 |
| unit run time | | 0.872 / 0.952 / 4.832 |
| pool label device unit active | 4.624 / 5.375 / 7.207 | 4.688 / 5.459 / 6.294 |
| mnt-pool active | 4.921 / 5.679 / 7.852 | 4.909 / 5.713 / 9.839 |
| mnt-base active | 5.711 / 6.586 / 7.903 | 5.052 / 6.548 / 9.988 |
| mnt-cache active | 5.709 / 6.586 / 7.904 | 5.052 / 6.548 / 9.988 |
| local-fs.target active | 5.713 / 6.587 / 7.905 | 5.103 / 6.548 / 9.988 |
| local-fs.target minus the last mount | 0.000 to 0.003 s in 30 | 0.023 to 0.137 s in 7 boots (the unit ended after the last mount, by 0.021 to 0.135 s), otherwise as without |
| smbd active | 12.483 / 13.844 / 15.297 | 11.855 / 13.712 / 17.478 |
| multi-user.target active | 12.485 / 13.845 / 15.298 | 11.856 / 13.713 / 17.479 |
| startup finished (job queue empty) | 12.485 / 13.846 / 15.299 | 11.857 / 13.713 / 17.479 |
| host: start to empty job queue (s) | 13.890 / 14.965 / 16.460 | 13.130 / 14.975 / 18.680 |
| label job start to `Found device .../spikepool` (s) | 0.610 / 0.697 / 1.187 | 0.655 / 0.738 / 1.249 |
| `Found device` lines per boot | 5 in 30 (nixos, arch0-2, spikepool once) | 5 in 30, the same five |
| `Mounting /mnt/` / `Mounted /mnt/` / `Unmount` lines | 10 / 10 / 0 in 30 | 10 / 10 / 0 in 30 |
| mount table events under /mnt | one `mount` per mount point, no `umount`, in 30 | the same in 30 |
| smbd starts / `NRestarts` | one start, 0 in 30 | one start, 0 in 30 |
| reader errors after its first good read (4 keys) | 0 in 30 | 0 in 30 |
| pool label unit inactive after it was active | 0 | 0 |
| uevents per pool member | 1 kernel and 1 udev `add`; `change` 0 to 4 (the 64-btrfs retrigger) | 2 kernel and 2 udev `add` in 30 of 30; `change` 0 to 4 |
| uevents of every other block device | vda, vdd-vdk, sr0, fd0: 1 add; loop0-7: 2 add; vdg (ext4 parity): 1 change | the same, except one boot (15) whose monitor missed the first kernel add of loop0-7 |
| 64-btrfs retriggers reaching the not-ready member (of 4) | 4: 23, 3: 5, 2: 1, 1: 1 boots | 4: 29, 3: 1 |

Activation order (pool label unit, pool/base/cache mounts, unit, local-fs, smbd, multi-user): without the unit
`pool < base < cache` in 19 and `pool < cache < base` in 11, always followed by `local-fs < smbd < multi-user`. With
the unit the mounts kept that order and the unit ended between them: after mnt-pool and before base/cache in 21,
after all three in 7, before mnt-pool in 2. In 28 of 30 boots the unit's re-add events (`KERNEL add` of both members)
came 0.006 to 0.289 s **after** mnt-pool was mounted, in 6 of them also after mnt-cache was mounted and the reader was
reading through it; nothing in the table changed. Boot 0 (with the unit), verbatim:

```text
[4.810464] server systemd[1]: Expecting device /dev/disk/by-label/spikepool...
[    4.969592] server systemd[1]: Starting Probe T051f: re-add the pool members after udev settled...
[5.465868] server systemd[1]: Found device /dev/disk/by-label/spikepool.
[5.787254] server systemd[1]: Mounted /mnt/pool.
[5.849292] server systemd[1]: Mounted /mnt/cache.
[    5.898299] server systemd[1]: Finished Probe T051f: re-add the pool members after udev settled.
monitor: KERNEL [5.884203] add vdb, KERNEL [5.884386] add vdc, UDEV [5.895687] add vdb ID_BTRFS_READY=1,
         UDEV [5.895885] add vdc ID_BTRFS_READY=1 (no change of either member, no other event follows)
```

Boot 15 (with the unit) is the outlier of every maximum: all mounts started only at 9.75 s although the label units
were found at 5.65 s, the reader process logged its first line at 10.01 s (it starts at sysinit, normally before 5 s),
and the shutdown before it took 7.72 s; the unit's re-add came at 9.90 s, after the mounts had started. A stall of the
whole guest, cause UNVERIFIED (host load at that moment not recorded); it is not attributable to the unit.

### (2) Degraded boot: one pool member left out of the VM definition (5 boots per variant)

| | `no-pool0` | `no-pool0-retrigger` (quiet rerun) | `no-pool1` | `no-pool1-retrigger` |
| --- | --- | --- | --- | --- |
| pool label job | timed out in 5 (14.76 to 16.49 s) | timed out in 5 | timed out in 5 | timed out in 5 |
| mnt-pool, mnt-base, mnt-cache | inactive (`Dependency failed`) in 5 | the same | the same | the same |
| other mounts, smbd | active in 5 | active in 5 | active in 5 | active in 5 |
| unit result / exit status | (absent) | exit-code / 1 in 5, state `failed` | (absent) | exit-code / 1 in 5, state `failed` |
| unit run time (s) | | 0.749 / 0.779 / 0.983 | | 0.779 / 0.830 / 0.861 |
| local-fs.target active | 14.755 / 14.902 / 16.486 | 14.538 / 14.733 / 15.025 | 13.840 / 14.682 / 14.764 | 13.951 / 14.640 / 14.802 |
| startup finished (job queue empty) | 21.633 / 22.521 / 23.296 | 20.557 / 21.410 / 22.309 | 19.481 / 21.754 / 22.251 | 21.025 / 21.447 / 21.955 |
| host: start to empty job queue (s) | 22.40 / 23.22 / 24.32 | 21.95 / 22.87 / 23.10 | 20.09 / 22.95 / 23.10 | 21.86 / 22.16 / 23.05 |
| add events of the present member | 1 kernel, 1 udev in 5 | 1 kernel, 1 udev in 5 (no re-add) | 1, 1 in 5 | 1, 1 in 5 (no re-add) |
| events of other block devices | as in the normal boots | the same counts as without the unit | as normal | the same as without |

The unit's journal in every degraded boot (verbatim, `no-pool0-retrigger` boot 0):

```text
[    4.823709] server systemd[1]: Starting Probe T051f: re-add the pool members after udev settled...
[    5.566948] server udevadm[485]: Failed to open the device '/dev/disk/by-id/virtio-pool0': No such device
[    5.572138] server systemd[1]: probe-pool-retrigger.service: Main process exited, code=exited, status=1/FAILURE
[    5.572920] server systemd[1]: probe-pool-retrigger.service: Failed with result 'exit-code'.
[    5.574909] server systemd[1]: Failed to start Probe T051f: re-add the pool members after udev settled.
[14.756093] server systemd[1]: Timed out waiting for device /dev/disk/by-label/spikepool.
[14.756168] server systemd[1]: Dependency failed for /mnt/pool.
```

`udevadm trigger` stops at the first path it cannot open, before sending anything: the present member got no extra
add event and no other device got one (counts equal to the boots without the unit). The unit neither hung nor delayed
these boots; the times to an empty job queue are those of the boots without it. The first `no-pool0-retrigger` run
(host load 17.3 at start, 24.2 five-minute average shortly after) had boots with the whole guest late (label job
started at 12.0 and 29.7 s, job queue empty up to 73.8 s, in one boot five other label jobs timed out as well; unit
run time up to 15.7 s, all spent in `udevadm settle`); it was repeated on a quiet host, the table shows the repeat.
Its unit result was the same (exit-code / 1 in 5), and its hot-plug results agree with the table below.

### (3) A member that appears late: hot-plug after the degraded boot (5 per variant, 25 with the loaded run)

After each degraded boot the absent member was attached through the monitor (`drive_add 0 werror=report,
format=qcow2,id=probe-<serial>,if=none,file=<image>` answered `OK`; `device_add virtio-blk-pci,drive=probe-<serial>,
serial=<serial>,id=disk-<serial>` answered nothing, i.e. no error), then watched for 30 s without any step by hand.

| | without the unit (10) | with the unit (10 quiet + 5 loaded) |
| --- | --- | --- |
| member device unit active after the attach | 0.06 to 0.09 s | 0.05 to 0.08 s quiet; 0.06 to 0.83 s loaded |
| pool label device unit active | in 10 of 10, 0.02 to 0.05 s after the member | in 15 of 15, 0.02 to 0.04 s after the member quiet, up to 0.27 s loaded |
| events | new member (vdk): 1 kernel and 1 udev add; present member: 1 kernel and 1 udev change (the 64-btrfs retrigger of the new member) | the same in 15 |
| mnt-pool mounted by itself within 15 s | 0 of 10 | 0 of 15 |
| mnt-pool / mnt-base / mnt-cache at the end | inactive, result success (never started), in 10 | the same in 15 |
| journal lines matched after the attach | only `BTRFS: device label spikepool devid <n> ... /dev/vdk ... scanned by (udev-worker)` | the same |

So the late member makes the label unit appear in every case, with and without the unit (the unit had already run and
failed); the pool stays unmounted until the mounts are started. Recorded, not judged.

### (4) Idempotence: the unit's commands again after boot, while a file is written through /mnt/cache

Each repetition: a 20 s writer (`write` of `retrigger_observe.py`: one open file, 64 KiB chunks, write+fsync every
10 ms, then read back) through `/mnt/cache`; after 5 s the command; before and after: `systemctl show` of every mount,
the label unit and smbd (`ActiveEnterTimestampMonotonic,InvocationID,MainPID,NRestarts,ActiveState`), the `/mnt` lines
of `/proc/self/mountinfo` (mount id, mount point, type, source); in between the monitor, reader, mount and journal logs.
`hand` = `set -e; udevadm settle --timeout=30; udevadm trigger --action=add --settle <both members>`, `restart` =
`systemctl restart probe-pool-retrigger.service`, `control` = `true`.

| | first run (3 hand, 2 restart) | second run (5 control, 3 hand, 2 restart) |
| --- | --- | --- |
| exit status | 0 in 5 | 0 in 10 |
| command duration (s) | hand 0.07 to 0.09, restart 0.12 to 0.14 | hand 0.08 to 0.09, restart 0.13, control 0.02 to 0.03 |
| uevents | per command: vdb and vdc 1 kernel and 1 udev `add`, nothing else | the same for hand/restart; none for control |
| changed unit properties (mounts, label unit, smbd) | none in 5 | none in 10 |
| mountinfo changed | no in 5 | no in 10 |
| mount table events, reader changes | none | none |
| journal lines | restart only: `probe-pool-retrigger.service: Deactivated successfully.` | the same |
| writer errors / read back equal | none / equal in 5 (53 to 90 MB) | none / equal in 10 |
| longest write+fsync (s) | hand 0.062, 1.887, 1.275; restart 0.028, 0.027 (no times kept) | control 0.020 to 0.037, hand 0.021 to 0.023, restart 0.016 to 0.018; 0 writes over 100 ms |

The 1.9 s and 1.3 s writes of the first run could not be placed in time (the writer kept only the maximum then); in the
second run, with every write over 100 ms timed and with controls, there were none. Whether the first two were caused by
the re-add is UNVERIFIED.

### (5) The forced reproduction with the unit, and its limits

(5a) `delay-both-retrigger` of T051f (unchanged driver, 1 s / 2 s delays), 10 boots, host load 2.10 at start:
**10 of 10 up**, every mount and smbd active; the 64-btrfs retrigger reached the not-ready member in 0 of 10 (the race
happened every time) and no member was stuck at the end; `Found device /dev/disk/by-label/spikepool` 4.60 to 4.66 s after
the job started (timeout 10 s). PROBE-RESULT verbatim:

```text
{"boots": 10, "failed_boots": [], "no_not_ready_member": [], "retrigger_changes": {"0": 10}, "scan_gap_ms": {"max": 79.944, "median": 2.998, "min": 0.056, "n": 10}, "stuck_boots": [], "zero_failure_bound": 0.2588655508930522}
```

(5b) Delay sweep. D = not-ready member's `PROGRAM` sleep, R = `RUN+` sleep of every ready add event (also of the
unit's re-add events, both members being ready then). D = R - 1 keeps the shape of `delay-both` (the not-ready member
updates the link after the ready member's retrigger and before its broadcast). The applied delays were read back from
the monitor (udev add minus kernel add): D or R plus 0.46 to 0.58 s for the original add events (about 0.46 s is the
normal processing time at that point of the boot) and R plus 0.02 to 0.08 s for the unit's re-add events.

| D:R (s) | without the unit | with the unit: up | with the unit: label unit found after job start | unit |
| --- | --- | --- | --- | --- |
| 1:2 | 0 of 1 up (stuck, job timed out) | 2 of 2 | 4.70, 4.78 s | success / 0 |
| 2:3 | 0 of 1 | 2 of 2 | 6.72, 6.76 s | success / 0 |
| 3:4 | 0 of 1 | 2 of 2 | 8.67, 8.67 s | success / 0 |
| 4:5 | 0 of 1 | **0 of 2**: job timed out at 10.12 / 9.95 s, label unit active later (14.59, 15.24 s), mounts not retried | (timed out) | success / 0 |
| 6:7 | 0 of 1 | **0 of 2**, the same (label unit active at 19.32, 19.40 s) | (timed out) | success / 0 |
| 31:32 | 0 of 1 | **0 of 2**, the same; label unit never active | (timed out) | exit-code / 1 after 30.1 s: `Timed out while waiting for udev queue to empty.` |

In every boot with the unit the 64-btrfs retrigger missed the not-ready member (0 change events), so each rescue is the
unit's. The label unit appears about 2R + 0.7 s after the job starts: the unit's settle waits for the original events
(about R), then its re-add events pass the R sleep again. The unit therefore rescues only while that stays inside
`x-systemd.device-timeout=10s`: here up to D:R = 3:4, not from 4:5 on. The maintainer's example "4 s PROGRAM sleep"
(4:5) is past that limit: 0 of 2. Beyond 30 s of unsettled udev the unit gives up without re-adding and holds
`local-fs.target` until then: empty job queue 42.29 and 42.37 s after start with the unit against 21.89 s without.
Journal of 4:5, boot 6 (verbatim):

```text
[3.901885] server systemd[1]: Expecting device /dev/disk/by-label/spikepool...
[14.017788] server systemd[1]: Timed out waiting for device /dev/disk/by-label/spikepool.
[14.017862] server systemd[1]: Dependency failed for /mnt/pool.
[   14.583076] server systemd[1]: Finished Probe T051f: re-add the pool members after udev settled.
```

That the delays scale like this in a real slow boot (the R sleep repeating on the re-add is an artefact of the rule
that forces the race) is UNVERIFIED.

### Side observations

- Shutdown: `mnt-cache.mount` or `mnt-base.mount: Unit process <pid> (mount.fuse.merg) remains running after unit
  stopped` in 8 of 31 shutdowns without the unit and 17 of 31 with it (normal-boot runs; two-sided Fisher exact
  p = 0.037); in 1 and 2 of them `systemd-shutdown[1]: Waiting for process: <pid> (mount.fuse.merg)` and SIGKILL after
  80 s (shutdown 91 s). In the inspected cases (plain boot 25, retrigger boot 7) `/mnt/pool` was unmounted cleanly before
  the wait. T051f saw the same 91 s shutdown once. Whether the unit is related, and what the helper waits for, is
  UNVERIFIED; the degraded, sweep and idempotence runs (fewer shutdowns, different boots) had 0 to 5 such lines and no
  90 s wait.
- Host load varied with the desktop (1-minute load 0.6 to 17.3 at run starts); runs and their loads are kept in the
  evidence. Only the first `no-pool0-retrigger` run was visibly affected and was repeated.

## What this means

Established (measured here, systemd 260.5, kernel 6.18.56, 2 vCPUs, VM disks):
- In 30 normal boots the unit always succeeded, added exactly one `add` event per pool member and nothing to any other
  device, and in 28 of 30 re-added the members while `/mnt/pool` was already mounted, in 6 also while `/mnt/cache`
  was mounted and read: no mount or umount event, no unit or smbd restart, no extra `Found device`, no read error, the
  label unit never went inactive. Running its commands again after boot (10 times: 6 by hand, 4 as a unit restart)
  changed no mount, unit or smbd property and gave the writer no error.
- It lengthened `local-fs.target` by 0.02 to 0.14 s in 7 of 30 boots; multi-user and the empty job queue showed no
  difference (medians 13.71 against 13.85 s).
- With a member missing it fails (exit 1, unit `failed`) in every boot and re-adds nothing; it does not block or delay
  the degraded boot. A member hot-plugged later makes the label unit appear with and without the unit; the nofail
  mounts are not started by themselves in either case.
- Under the forced race it rescued 10 of 10 (`delay-both`) and 6 of 6 sweep boots up to D:R = 3:4; it fails from
  4:5 on because the label unit appears after the 10 s device timeout, and with udev unsettled for more than 30 s it
  fails after holding `local-fs.target` for those 30 s.

Not established:
- The difference in mergerfs helpers outliving their mount unit at shutdown (17 against 8 of 31), its mechanism and
  whether it can matter for data; the two untimed slow writes of the first idempotence run.
- Any side effect on real disks, with the SMB client VM reading through the share (only a local reader on the server
  was used), under S1 load, with 4 vCPUs, or with the 90 s default device timeout.

## UNVERIFIED

- Cause of the leftover mergerfs mount helpers at shutdown and why they were more frequent with the unit (8/31 vs
  17/31, one run each); cause of the 90 s shutdowns (1 vs 2).
- Cause of the 1.9 s and 1.3 s write+fsync in the first idempotence run (not reproduced in 11 timed repetitions).
- Cause of the guest-wide stall in boot 15 of the run with the unit.
- Behaviour through SMB from the client VM, on real disks, under load, with 4 vCPUs, with other device timeouts.
- That the time to the label unit scales as 2R + 0.7 s outside the forced reproduction.
- Whether the failed unit state in degraded boots has consequences elsewhere (`systemctl is-system-running` was not
  read).

## Criteria changelog note (for T052 `S1.yaml`)

> T051g (2026-10-19, systemd 260.5, kernel 6.18.56, server VM 1536 MiB, 2 vCPUs, nodes.nix options): side effects of
> the oneshot retrigger unit of T051f (after systemd-udev-trigger, before local-fs.target: `udevadm settle
> --timeout=30; udevadm trigger --action=add --settle` of both pool members). 30 normal boots with it and 30 without:
> all up, unit success in 30; one extra add event per member, none elsewhere; in 28 boots the re-add hit a mounted
> pool without any mount, smbd or label-unit change and without a read error through /mnt/cache; local-fs.target
> later by up to 0.14 s in 7 boots. Repeating its commands after boot during a write (10 times): no effect. With a
> pool member missing it fails (exit 1) and re-adds nothing, without delaying the degraded boot; a member hot-plugged
> later makes the label unit appear with or without it, the nofail mounts stay down. Forced race: 10 of 10 rescued;
> the rescue fails once the label unit comes after the 10 s device timeout (sweep: up to D:R = 3:4 s ok, 4:5 not), and
> beyond 30 s of unsettled udev it fails after holding local-fs.target 30 s. Unexplained: mergerfs mount helpers
> outlived their unit at shutdown in 17 of 31 shutdowns with it against 8 of 31 without (two 90 s SIGKILL waits
> against one). Nothing is adopted.
