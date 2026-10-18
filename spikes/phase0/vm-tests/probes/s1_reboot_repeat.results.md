# Result: the S1.11 reboot inside the real S1 run, repeated (task T051e)

Measured on 2026-10-18 with the full S1 shakedown (`nix run ./spikes/phase0#s1 -- --mode shakedown`),
eight runs one after the other, one VM test at a time, KVM, machine state on an ext4 disk. A shakedown
never writes a record; the output of each run (driver.log, outcome.json, status/) stayed in the
scratch directory (evidence, not committed). Nothing below changes `nix/nodes.nix`, the criteria or
what a check judges.

Question: in one of three reboots of the S1 shakedown (T051) S1.11 came up without its mounts
(by-label device jobs timed out under `x-systemd.device-timeout=10s`, `nofail`). The probes T051a
(idle reboots) and T051d (reboots after the S1.8 IO, with and without host IO pressure) found 0
failures in 60 boots outside the S1 sequence. Does the failure happen inside the real S1 run, and
if so, what did the boot look like?

## What was added before the runs

`check_reboot_view` (`vm-tests/s1/checks_view.py`) now calls `vm-tests/s1/boot_diag.py` after the
reboot: when a judged unit (`mnt-base.mount`, `mnt-cache.mount`, `samba-smbd.service`) is not
active, a by-label mount of `/etc/fstab` is missing (`findmnt`), or the boot never settles, it writes
`status/S1.11-boot-diagnostics.json` before the clients mount again: failed units, jobs,
`systemctl status` and `show` of every by-label device and mount unit and every failed unit,
`journalctl -b -o short-monotonic` (whole for the first 60 s, then kernel, PID 1 and udev lines),
the last 200 lines of the previous boot, `ls -l --time-style=full-iso` and `stat` of
`/dev/disk/by-label/`, `udevadm info --query=all` of every existing link and every disk,
`udevadm settle --timeout=0`, `lsblk -f`, the btrfs lines of `dmesg`, `/proc/pressure/{io,cpu,memory}`
and `uptime` of the guest, and the host's `/proc/pressure/io` and `/proc/loadavg` right before the
start and at the capture. The capture only reads, never raises, and records each command failure.

Check of the capture itself: one extra run with `--only S1.11` and the capture forced on by an
uncommitted one-line edit (reverted, not counted below): every command ran with status 0, no
error, S1.11 passed.

## Environment

- Profile `large`, `server` 1536 MiB and `client` 768 MiB (2304 MiB VM RAM in total, read back from
  the preflight in `environment.json`), budget 1200 s.
- Host: 24 CPUs, the maintainer's workstation in use; `MemAvailable` 15434 to 19102 MiB and no
  other QEMU process before each run; 1-min load 3.0 to 3.9 and host `io some avg10` 0.00 to 0.16
  at the start of each run.
- Guest: kernel 6.18.56, systemd 260.5 (as in T051a/T051d).

## Results

| run | wall time | S1.1 | S1.2 to S1.8 | S1.9 | S1.10 | S1.11 | S1.11 boot: jobs start / last by-label device found | pool member scans apart | boot diagnostics |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 648 s | fail | pass | fail | pass | pass | 4.85 s / 5.63 s | 9.81 ms | not triggered |
| 2 | 657 s | fail | pass | fail | pass | pass | 5.00 s / 5.81 s | 86.37 ms | not triggered |
| 3 | 627 s | fail | pass | fail | pass | pass | 4.78 s / 5.65 s | 2.88 ms | not triggered |
| 4 | 609 s | fail | pass | fail | pass | pass | 3.96 s / 4.61 s | 1.23 ms | not triggered |
| 5 | 617 s | fail | pass | fail | pass | pass | 4.80 s / 5.47 s | 0.98 ms | not triggered |
| 6 | 593 s | fail | pass | fail | pass | pass | 4.84 s / 5.44 s | 9.81 ms | not triggered |
| 7 | 608 s | fail | pass | fail | pass | pass | 4.89 s / 5.58 s | 2.45 ms | not triggered |
| 8 | 614 s | fail | pass | fail | pass | pass | 4.81 s / 5.45 s | 4.98 ms | not triggered |

S1.1 and S1.9 fail as known from the shakedown; nothing was changed for them. Every run finished
within its budget. In every S1.11 view evidence all three judged units were active, 98 paths, no
difference. The boot times come from the server console in `driver.log` (`Expecting device
/dev/disk/by-label/...`, `Found device ...`, guest CLOCK_MONOTONIC); parity0 logs no `Found device`
line, as in T051d. No S1.11 boot logged a device job timeout.

The first server boot of every run (blank disks, before `spike-mkfs`) logged all five by-label
device jobs as timed out between 14.76 and 14.88 s, every time, because no label exists yet. Run 1,
verbatim (escape sequences removed):

```text
[   14.837581] systemd[1]: dev-disk-by\x2dlabel-arch0.device: Job dev-disk-by\x2dlabel-arch0.device/start timed out.
[   14.846079] systemd[1]: dev-disk-by\x2dlabel-arch2.device: Job dev-disk-by\x2dlabel-arch2.device/start timed out.
[   14.851588] systemd[1]: dev-disk-by\x2dlabel-parity0.device: Job dev-disk-by\x2dlabel-parity0.device/start timed out.
[   14.858337] systemd[1]: dev-disk-by\x2dlabel-spikepool.device: Job dev-disk-by\x2dlabel-spikepool.device/start timed out.
[   14.862991] systemd[1]: dev-disk-by\x2dlabel-arch1.device: Job dev-disk-by\x2dlabel-arch1.device/start timed out.
```

## What this means

- S1.11 failed 0 of 8 times inside the real S1 sequence. 0/8 bounds the failure rate per S1 run
  below 31.2 % at 95 % confidence (exact binomial, 1 - 0.05^(1/8)). A rare failure is not excluded.
- In every S1.11 boot each by-label device with a `Found device` line was found 0.57 to 0.87 s
  after the first device job started, against the 10 s timeout, in line with T051a and T051d. Pool member scans 0.98 to 2.88 ms apart (four
  runs under 3 ms) came up.
- Two descriptions of the shakedown failure existed. The criteria changelog (written while the shakedown
  output existed) names `dev-disk-by\x2dlabel-spikepool.device` alone, with mnt-pool, mnt-base and
  mnt-cache failed with result 'dependency'. The other (all five labels timing out at about 14.9 s) matches,
  line for line, what the first (blank-disk) boot of every S1 run logs; it came from reading the log of the
  shakedown run and taking the first boot's lines for the S1.11 boot. The changelog description is the record;
  the shakedown output itself is gone, so it cannot be re-checked. UNVERIFIED.
- If the failure happens again in an S1 run, `status/S1.11-boot-diagnostics.json` holds the evidence.

## UNVERIFIED

- The cause of the S1.11 failure of the shakedown; it was not reproduced in 8 runs here, nor in the
  60 boots of T051a and T051d.
- Whether the shakedown failure really was the spikepool-only boot of the changelog; the output that would
  show it is gone.
- The capture on a real failing boot: it was exercised on a passing boot only (forced) and on fakes
  in the unit tests.

## Criteria changelog note (for T052 `S1.yaml`)

> T051e (2026-10-18, systemd 260.5, kernel 6.18.56, server 1536 MiB and client 768 MiB, nofail and
> x-systemd.device-timeout=10s as in nodes.nix): 8 full S1 shakedown runs in a row; S1.11 passed
> 8 of 8 times (S1.1 and S1.9 failed as before), every by-label device of the S1.11 boot was found
> 0.57 to 0.87 s after the device jobs started, no device job timed out. The failure seen once in the
> shakedown was not reproduced; 0/8 bounds it below 31 % per run (95 %). The first boot of every run
> (blank disks) times out all five by-label jobs at about 14.8 s by design. S1.11 now writes boot
> diagnostics into the evidence when the reboot comes up incomplete.
