# Probe result: retrigger variants and the shutdown difference (task T051i)

Measured on 2026-10-19 with the probe `retrigger_v2.nix` / `probe_retrigger_v2.py` (boot observation of T051g in
`retrigger_boot.py`, prelude `retrigger_prelude.py`, guest tool `shutdown_observe.py`, parsing, Fisher's test and
summaries in `retrigger_shutdown.py`, unit tests in `tests/test_probe_retrigger_shutdown.py`). One VM test at a time,
KVM, machine state on an ext4 disk under a short path (removed after each run), never tmpfs. A probe never writes a record; the full
output of each run is `probe-retrigger-v2.json` in the driver's output directory (evidence, not committed). Nothing
below changes `nix/nodes.nix` or `nix/tiered-cache.nix` or adopts a unit; the numbers are measurements.

Question: (A) is there a variant of the retrigger unit of T051f that does not hold the boot when udev does not
settle (T051g finding 1) and does not fail in a degraded boot (finding 2)? (B) What is the mergerfs process that
"remains running after unit stopped" at shutdown, why was it seen 17 of 31 times with the unit against 8 of 31
without (Fisher p = 0.037), why did some shutdowns wait 90 s for SIGKILL, and is any data at risk?

**Harm first.** No data check failed: in every cycle whose next boot mounted the pool (329 of 365 cycles over all
runs, 658 files; 122 of them after a leftover server, 25 after a 90 s kill wait), two files written through `/mnt/cache` before the shutdown (one fsynced, one not) read back
with their SHA-256 through `/mnt/cache` and on `/mnt/pool`, `btrfs scrub start -B` reported `no errors found`,
`btrfs device stats --check` was all zero, the kernel log had no btrfs error and both mergerfs pools were mounted
again. No reader error, no remount, no smbd restart with any variant. Measured costs:
- U1 and U2 (trigger by property) send no event in normal boots, one `change` to the not-ready member when the race
  happens or a member is missing, exit 0 in every normal and degraded boot. U1 keeps the original's 30 s settle and
  so its finding 1 (31 s forced delay: unit fails, `local-fs.target` at 35 s). U2 is bounded: unit 5.4 s, exit 0.
- The shutdown difference is a race in mergerfs 2.41.1, present without any retrigger unit: 19 of 60 and 24 of 60
  shutdowns without the unit, 16 of 60 with the original (Fisher p = 0.45 between the two baselines, 0.24 between
  with and without). 17 of 31 against 8 of 31 in T051g lies inside that spread; no effect of the unit was found.
  The 90 s waits (3, 5 and 3 of 60) are the same race; the server had no mount and no open file on the pool then.

## Step 1: what the sources say (no VM)

Pinned sources: systemd 260.5 (`/nix/store/s8lxzd3r71kn8f8gfj9vq4ghdzvlna7f-source`), mergerfs 2.41.1
(`/nix/store/c03j8r2jjc4z3a4kdma1b2r14aca0rbb-source`), kernel 6.18.56 (`linux-6.18.56.tar.xz` of T051f).

**Trigger by property (A).** `udevadm trigger` defaults to `--action=change` (`src/udev/udevadm-trigger.c:37`),
`-p KEY=VALUE` adds a property match to the device enumeration (`:277-286`), and with `--settle` it returns 0 at once
when nothing matched (`:604-605`, empty `settle_ids`). Line 15 of `64-btrfs.rules` runs exactly
`udevadm trigger -s block -p ID_BTRFS_READY=0` (change). A path that cannot be opened ends `udevadm trigger` before
anything is sent (`:208-209`, T051g finding 2); a property match never names a missing device.

The variants (all `probe-pool-retrigger.service`, oneshot, after `systemd-udev-trigger.service`, before
`local-fs.target`, in `pool_race_modules.nix`):

| | script | unit settings |
| --- | --- | --- |
| orig (T051f) | `udevadm settle --timeout=30`; `udevadm trigger --action=add --settle <both member paths>` | none |
| U1 (`retriggerByProperty <action>`) | `udevadm settle --timeout=30`; `udevadm trigger --settle --action=<action> --subsystem-match=block --property-match=ID_BTRFS_READY=0` | none |
| U2 (`retriggerBounded <action>`) | `udevadm settle --timeout=5 \|\| echo ...`; `timeout 4 udevadm trigger --settle --action=<action> --subsystem-match=block --property-match=ID_BTRFS_READY=0 \|\| echo ...` | `TimeoutStartSec=10s` |

U2 never fails its script (`|| echo`, the effect of `ExecStart=-`); only the 10 s backstop could fail the unit.

**The leftover process (B).** `mount.fuse.mergerfs` is a symlink to the mergerfs binary (`bin/` of the package).
`mount(8)`, run by systemd as the mount unit's control process, executes it; it mounts, forks, the parent `_exit`s
and the child calls `setsid` and serves the FUSE mount (`libfuse/lib/helper.c:144-194`). That child is the mergerfs
FUSE server itself (comm `mount.fuse.merg`), in the cgroup of `mnt-base.mount` / `mnt-cache.mount`, not a helper.
At stop, when the unmount succeeded, `mount_enter_dead` only logs the processes still in the cgroup
(`src/core/mount.c:900`, `unit_warn_leftover_processes(..., start=false)`, signal 0, `src/core/unit.c:6096-6128`):
nobody kills them; the server must exit by itself once the kernel aborts its connection (ENODEV on `/dev/fuse`).
Its exit path (`libfuse/lib/fuse_loop.cpp`): each read worker returns on ENODEV and runs its two `DEFER`s in reverse
order (`DEFER` is a scope-exit destructor, `scope_guard.hpp:366`), so `sem_post(_finished)` (`:81`) runs **before**
`fuse_session_exit` (`:80`); the main thread loops `while(!fuse_session_exited(se_)) sem_wait(&finished);`
(`:319-320`). If both workers post before either sets the exit flag, the main thread consumes both posts, sees the
flag still 0 and blocks in `sem_wait` for good: nothing posts again. (The threadpool threads block every signal,
`thread_pool.hpp:110-136`; the maintenance thread is a plain `pthread_create`, `maintenance_thread.cpp:48`; SIGTERM
has the libfuse `exit_handler`, `fuse_signals.c:52`.) At the end, systemd-shutdown's `broadcast_signal` does
`kill(-1, SIGSTOP)`, sends SIGTERM to every process, then `kill(-1, SIGCONT)` (`src/shared/killall.c:303-309`) and
waits up to 90 s, logging `Waiting for process` after 10 s. While a task is stopped the kernel picks no thread for the
signal (`wants_signal` returns false for a stopped task, `kernel/signal.c:981`, `complete_signal` `:987-1009`); after
SIGCONT the first unblocked thread to dequeue it runs the handler. A thread that ran no handler restarts its
interrupted system call (`arch/x86/kernel/signal.c:345-352`). So if the maintenance thread takes the SIGTERM, the
handler sets the flag but the main thread goes back into `sem_wait` and only SIGKILL ends the process.

## Step 2: environment and measurement

The S1 server of T051g (profile `large`, all mounts of `nix/nodes.nix` with `nofail,x-systemd.device-timeout=10s`,
the tiered-cache mounts, the S1 share, 1536 MiB, 2 vCPUs, the udev monitor of T051f and the reader and mount logger of
T051g). Guest: kernel 6.18.56, systemd 260.5, btrfs-progs 6.19.1, mergerfs 2.41.1. Host: 24 CPUs, the maintainer's
workstation with the desktop in use; before every run no other QEMU process and at least 10 GiB available memory
(checked by the run wrapper, which also logged host load and the commit). Added in every T051i variant:
`probe-shutdown-capture.service`, ordered before `mnt-base.mount` and `mnt-cache.mount` so its stop runs after both
unmounted (the console showed the capture after both `Unmounted` lines in 339 of 339 cycles with both mounted);
its ExecStop records every mergerfs server still running, per
thread state, wait channel, kernel stack, signal masks and system call, the FUSE connections and the mount namespaces
still holding a mergerfs mount, then polls 2 s. One cycle: two 1 MiB random files through `/mnt/cache` (fsync / no
fsync, SHA-256 kept), shutdown (timed, console kept), boot, the T051g boot measurement plus
`systemctl is-system-running` and failed units, the shutdown's leftover and kill-wait lines from console and previous
journal, the capture, then the data checks of the harm paragraph. "Outlived": PID 1 logged `<mount unit>: Unit
process <pid> (mount.fuse.merg) remains running after unit stopped.`; "kill wait": systemd-shutdown logged
`Waiting for process` or `Sending SIGKILL to PID` for a mergerfs server (every one also had a shutdown over 90 s).

Commits: 5569960 (db-u1-add, db-u1-change), 519f955 (db-u2-add, db-u2-change; adds signal masks), 01fc6ea (the
sweeps, n-u1-change, diag-sigterm; opt-in SIGTERM diagnostic), 26a0812 (all later runs; opt-in watcher). The later
commits only add fields or opt-in diagnostics to the capture; boot measurement and units are unchanged.

```sh
nix build ./spikes/phase0#driver-probe-retrigger-v2-<variant>
SPIKE_STATE_DIR=<dir> PROBE_BOOTS=30 PROBE_DEADLINE_S=1380 nix run ./spikes/phase0#probe-retrigger-v2-<u>
SPIKE_STATE_DIR=... PROBE_BOOTS=5 nix run ./spikes/phase0#probe-retrigger-v2-no-pool0-<u>   # and no-pool1-<u>
SPIKE_STATE_DIR=... PROBE_BOOTS=10 nix run ./spikes/phase0#probe-retrigger-v2-delay-both-<u>
SPIKE_STATE_DIR=... PROBE_SWEEP=1:2,3:4,4:5,31:32 PROBE_SWEEP_BOOTS=2 nix run ./spikes/phase0#probe-retrigger-v2-sweep-<u>
```

## Step 3 (A): the variants

### Forced race `delay-both` (rules of T051f), 10 boots each

| | U1 add | U1 change | U2 add | U2 change |
| --- | --- | --- | --- | --- |
| up (every mount and smbd) | 10 of 10 | 10 of 10 | 10 of 10 | 10 of 10 |
| unit result / run time (s) | success/0 in 10; 4.55-4.71 | success/0 in 10; 2.50-2.61 | success/0 in 10; 4.55-4.63 | success/0 in 10; 2.51-2.58 |
| label unit found after job start (s) | 4.69-4.89 | 2.66-2.75 | 4.68-4.78 | 2.65-2.74 |
| `local-fs.target` active (s) | 9.30-10.23 | 7.29-7.54 | 9.36-9.61 | 7.23-7.45 |
| member events | stuck member 1 extra `add`, other member none | stuck member 1 extra `change`, other none | as U1 add | as U1 change |
| `is-system-running` | running in 10 | running in 10 | running in 10 | running in 10 |

Without a unit this reproduction failed 11 of 11 (T051f); the original rescued 10 of 10 at 4.60-4.66 s (T051g).
The `add` variants are slower only because the reproduction's ready-member sleep (`RUN+="sleep 2"`) matches
`ACTION=="add"` and so delays their own re-add event again; a `change` event passes it.

### Delay sweep (D = not-ready member's PROGRAM sleep, R = ready member's RUN sleep), 2 boots per step

| D:R | U1 add | U1 change | U2 add | U2 change |
| --- | --- | --- | --- | --- |
| 1:2 | up 2/2, found 4.72, 4.69 s | up 2/2, 2.72, 2.69 s | up 2/2, 4.72, 4.78 s | up 2/2, 2.72, 2.70 s |
| 3:4 | up 2/2, 8.72, 8.72 s | up 2/2, 4.73, 4.72 s | up 2/2, 8.76, 8.71 s | up 2/2, 4.75, 4.73 s |
| 4:5 | **0/2** (job timed out), unit success | up 2/2, 5.69, 5.75 s | **0/2**, unit success (trigger hit its 4 s bound, exit 124) | up 2/2, 5.56, 5.55 s |
| 31:32 | 0/2; unit exit-code/1 after 30.3, 30.4 s; `local-fs.target` 35.2 s; queue empty 42.3, 44.6 s; `degraded` | 0/2; exit-code/1 after 30.3, 30.4 s; 35.1 s; 42.3, 43.3 s; `degraded` | 0/2; **success/0 after 5.38, 5.40 s**; `local-fs.target` 14.7, 14.8 s (device timeout); 22.0, 23.2 s; `running` | 0/2; **success/0 after 5.38, 5.39 s**; 14.4, 14.7 s; 22.8, 22.1 s; `running` |

The limit of a rescue is the 10 s device timeout. `change` reaches 4:5 only because the reproduction's R sleep is
not applied to a change event; in a real slow boot the cost of a re-sent event is UNVERIFIED. At 4:5 U2's settle hit
its 5 s bound (`udev queue not empty after 5 s (exit 1), triggering anyway`) and the trigger still rescued the pool
(change). At 31:32 U2 held `local-fs.target` no longer than the device timeout itself: its bound held.

### Normal boots, 30 each (U1 change, U2 change)

| | U1 change | U2 change | reference |
| --- | --- | --- | --- |
| up | 30 of 30 | 30 of 30 | |
| unit result / run time (s) | success/0 in 30; 0.82-0.97 | success/0 in 30; 0.81-0.98 | orig (batch C, 60): success/0 in 60; 0.85-0.95 |
| events sent by the unit | none in 30 (last member event 0.32-0.44 s before the unit's exit; when it does trigger, 13-27 ms) | none in 30 (0.29-0.45 s) | orig: one `add` to each member in 60 |
| not-ready member's change events (64-btrfs retriggers) | 4 in 29, 3 in 1 | 4 in 28, 3 in 1, 2 in 1 | T051g plain: 0-4 |
| `local-fs.target` minus last pool mount (s) | 0.000 in 27, up to 0.053 in 3 | 0.000 in 27, up to 0.050 in 3 | orig up to 0.043 in 4 of 60; none: at most 0.004 in 120 |
| `local-fs.target` / job queue empty (s, min/median/max) | 4.90/6.53/7.51; 11.13/13.32/14.94 | 4.92/6.54/6.72; 11.11/13.47/15.08 | |
| reader errors, mount table changes, smbd restarts | none; one mount per mount point; 0 | the same | |
| `is-system-running` | running in 30 | running in 30 | running in 60 (orig) and 120 (none) |

### Degraded boots: a pool member left out (5 boots per member and variant)

| | U1 change (pool0, pool1) | U2 change (pool0, pool1) | orig (pool0, 2 boots) | none (pool0, 2 boots) |
| --- | --- | --- | --- | --- |
| unit result / run time (s) | success/0 in 10; 0.78-0.85 | success/0 in 10; 0.78-0.83 | exit-code/1 in 2; 0.78, 0.79 | (absent) |
| `is-system-running`, failed units | running, none (10) | running, none (10) | **degraded**, `probe-pool-retrigger.service` (2) | running, none (2) |
| events to the present member | 1 `change` from the unit (5-11 ms before its exit) plus 2-3 from 64-btrfs (10) | the same, 64-btrfs 1-3 (10) | none from the unit | 3 `change` (64-btrfs) |
| `local-fs.target` (s) | 14.50-14.85 (the device timeout) | 14.50-14.85 | 14.66, 14.72 | 14.83, 14.76 |
| job queue empty (s) | 21.9-25.0 | 22.0-23.9 | 23.0, 23.0 | 23.3, 22.0 |
| hot-plugged member: label unit active after | 0.07-0.10 s | 0.07-0.14 s | 0.09, 0.11 s | 0.09, 0.10 s |

The extra `change` makes the present member's rules run once more; it stays `ID_BTRFS_READY=0` and the pool stays
unmounted, as without a unit. The original leaves the system `degraded` in a degraded pool.

## Step 4 (B): the shutdown difference

### What outlives its unit

In every shutdown where PID 1 logged the leftover line, the process was the mergerfs FUSE server of that mount
(`/run/current-system/sw/bin/mount.fuse.mergerfs <branches> /mnt/base|cache ...`, cgroup
`0::/system.slice/mnt-base.mount` or `mnt-cache.mount`, one `/dev/fuse` fd). The capture after both unmounts found
it in one of two states. Either it had already exited (the leftover line only lost a race of milliseconds against the
server's own exit: 15 of 43 leftover shutdowns of the two baselines, 4 of 16 with the original), or it was **stuck**
(28 servers in the baselines, 13 with the original), and every stuck server showed the same state: its FUSE
connection gone (`/sys/fs/fuse/connections` empty, so the kernel had aborted it), no mount namespace still holding a
mergerfs mount, no `exiting main loop` line before the capture, both `fuse.read` threads in `futex_do_wait` on the
threadpool queue (heap address), the maintenance thread in `clock_nanosleep`, and the main thread in `futex`
(`202`, op `0x189`, a **stack** address: the local `finished` semaphore of `fuse_session_loop_mt`), SIGTERM caught
(`SigCgt` bit 15), nothing blocked or pending. That is the lost wakeup of Step 1. Batch A, cycle 14, verbatim
(console, then the capture's thread table):

```text
[   15.307460] systemd[1]: Unmounting /mnt/base...
[   15.327407] mergerfs[585]: exiting main loop with return code 0
[   15.330346] systemd[1]: Unmounted /mnt/cache.
[   15.332879] systemd[1]: mnt-base.mount: Unit process 582 (mount.fuse.merg) remains running after unit stopped.
[   15.333856] systemd[1]: Unmounted /mnt/base.
[   15.380154] systemd[1]: Unmounted /mnt/pool.
[   17.612102] probe-t051i-observe[1216]: PROBE-T051I-CAPTURE servers=[(582, '0::/system.slice/mnt-base.mount')] gone_after_s={'582': None} sigterm=None connections={} holders=[]
[   17.794991] systemd-shutdown[1]: Sending SIGTERM to remaining processes...
[   27.797711] systemd-shutdown[1]: Waiting for process: 582 (mount.fuse.merg)
[  107.805273] systemd-shutdown[1]: Sending SIGKILL to PID 582 (mount.fuse.merg).
582 mount.fuse.merg S futex_do_wait ['202', '0x7ffc18fb9ea0', '0x189'] SigBlk 0 SigCgt 0x100004a06 SigPnd/ShdPnd 0
583 fuse.maint      S hrtimer_nanosleep ['230', '0x0', '0x0']
584 fuse.read       S futex_do_wait ['202', '0x612136bd14a8', '0x189'] SigBlk fffffffe7ffbfeff
586 fuse.read       S futex_do_wait ['202', '0x612136bd14a8', '0x189'] SigBlk fffffffe7ffbfeff
```

What happens to a stuck server at the end decides between a short shutdown and the 90 s wait. Of 41 stuck servers
in the batches, 28 logged `exiting main loop` about 0.1 s after the capture, when systemd-shutdown's SIGTERM came,
and the shutdown took about 3 s (the capture's 2 s poll included); 11 never exited, and systemd-shutdown waited 90 s
and sent SIGKILL; 2 had neither a line nor a wait (their line may have been lost with journald). Two diagnostic runs (not counted, flags off in every
counted run) showed why: with `PROBE_CAPTURE_SIGTERM=1` the capture sent SIGTERM itself (a plain `kill`, no stop):
the stuck server was gone after 0.02 s (1 of 1). With `PROBE_CAPTURE_WATCH=1` a watcher (`@probe-t051i-watch`,
spared by systemd-shutdown) reported the stuck server every 5 s during the 90 s wait (PID 1 `systemd-shutdow`):
unchanged `mount.fuse.merg:S:futex_do_wait:['202']:0000/0000 fuse.maint:S:hrtimer_nanosleep:['230']:0000/0000
fuse.read:S:futex_do_wait ... main=futex_do_wait<__futex_wait<futex_wait<do_futex` from 20.2 s to 106.0 s, then
`gone` at 110.6 s: the SIGTERM had been consumed (nothing pending) without waking the main thread, as Step 1 predicts
when the maintenance thread dequeues it after systemd-shutdown's SIGSTOP/SIGCONT. Which thread took the signal was not
observed directly (UNVERIFIED); 11 of 41 is the measured share of the long branch.

### With and without the unit: three batches of 60 shutdown cycles

Runs of 30 cycles, interleaved A1, C1, B1, A2, C2, B2 (03:59 to 05:15, host 1-minute load 1.2 to 1.6 at the starts),
commit 26a0812, each cycle with the writes, the capture and the data checks. A and B: no retrigger unit (`plain`);
C: the original unit of T051f (`orig`).

| | A (none) | B (none) | C (orig) | A+B |
| --- | --- | --- | --- | --- |
| shutdowns with a leftover mergerfs server | 19 of 60 | 24 of 60 | 16 of 60 | 43 of 120 |
| leftover units (mnt-base / mnt-cache) | 14 / 6 | 17 / 14 | 11 / 8 | 31 / 20 |
| a server stuck at the capture | 13 | 15 | 12 | 28 |
| 90 s kill wait (`Waiting for process`, SIGKILL) | 3 | 5 | 3 | 8 |
| kill waits with a stuck server at the capture | 3 of 3 | 5 of 5 | 3 of 3 | 8 of 8 |
| shutdown time (s), min / median / max | 1.10 / 1.29 / 93.34 | 1.16 / 1.26 / 93.44 | 1.13 / 1.27 / 93.43 | |
| data checks passed | 60 of 60 | 60 of 60 | 60 of 60 | |
| boots up, `is-system-running` running | 60, 60 | 60, 60 | 60, 60 | |

Fisher's exact test, two-sided (`fisher_two_sided`, checked against scipy):

| comparison | leftover | stuck | kill wait |
| --- | --- | --- | --- |
| A vs B (two baselines) | 19/60 vs 24/60, p = 0.447 | 13 vs 15, p = 0.829 | 3 vs 5, p = 0.717 |
| C vs A | 16/60 vs 19/60, p = 0.688 | 12 vs 13, p = 1.000 | 3 vs 3, p = 1.000 |
| C vs B | 16/60 vs 24/60, p = 0.175 | 12 vs 15, p = 0.662 | 3 vs 5, p = 0.717 |
| C vs A+B | 16/60 vs 43/120, p = 0.242 | 12 vs 28, p = 0.705 | 3 vs 8, p = 0.754 |

The other T051i runs agree (each a different unit or forced boot, so not pooled): U1 normal 11/30 leftover, 1 kill
wait; U2 normal 12/30, 2; the forced and sweep runs 3 to 6 of 8 to 10, 0 to 4. Over all 365 cycles (the two diagnostic
runs included): 124 leftover shutdowns, 26 kill waits, 0 failed data checks.

**17 of 31 against 8 of 31 (T051g).** The two identical baselines here differ by 5 of 60 (32 % against 40 %,
p = 0.45), and the unit's batch has the lowest rate (27 %). T051g's 26 % without the unit matches these rates; its
55 % with the unit is above every batch here and was not reproduced: with N = 60 per batch no effect of the unit is
visible (C vs A+B p = 0.24; for scale, 33 of 60 with the unit against A+B would give p = 0.017). T051g compared many
quantities, so one p near 0.04 among them is not unusual. The mechanism, a race between mergerfs's own threads at
unmount, involves nothing the unit touches. The conditions differ from T051g (files written before each shutdown,
the capture unit in the stop order), so this is not a repetition of that experiment; chance is the reading the data
supports, not a proof.

### Data safety around the leftover servers and the kill waits

- A stuck server has no FUSE connection and no mount (the kernel aborted it at the unmount, which succeeded), so no
  request can reach it, and `/mnt/pool` unmounted cleanly before every kill wait (the unmount of a busy filesystem
  fails without lazy unmount, which systemd does not use), so it held no open file there. It is killed with nothing
  to lose.
- After all 122 of the 124 leftover shutdowns and 25 of the 26 kill waits that were followed by a boot with the pool
  (the others: a degraded boot and a sweep 4:5 boot, pool not mounted), both files written just before the shutdown read back with the recorded
  SHA-256 through `/mnt/cache` and on `/mnt/pool`, including the unsynced one; scrub `no errors found`; device stats
  all 0; no `BTRFS error|warning|critical`, `csum failed` or `corrupt` in the kernel log; `/mnt/base` and `/mnt/cache`
  mounted `fuse.mergerfs`, `/mnt/pool` btrfs, all three units active. Not verified: the 20 files written before the
  shutdowns whose next boots never mounted the pool (degraded runs, sweep 4:5 and 31:32 boots).

## What this means

Established (systemd 260.5, kernel 6.18.56, mergerfs 2.41.1, 2 vCPUs, VM disks):
- Triggering by property (U1, U2) removes T051g finding 2: with a member missing the unit exits 0 (20 of 20), sends
  one `change` to the present member only, leaves the system `running` and does not delay the boot. The original leaves
  it `degraded` (2 of 2). In normal boots U1 and U2 sent no event at all (60 of 60) and changed nothing measurable.
- Bounding the waits (U2) removes finding 1: with udev forced unsettled the unit exits 0 after 5.4 s and
  `local-fs.target` is held only by the device timeout itself; U1 behaves like the original there (30 s, exit 1,
  system `degraded`).
- All four variants rescued the forced race 10 of 10. Rescue still ends at the 10 s device timeout (finding 4 stands);
  where it ends in the sweep depends on the reproduction's add-only rule.
- The leftover mergerfs server is mergerfs's own FUSE server stuck by a lost wakeup in its exit path; it occurs without
  any retrigger unit at about a third of shutdowns here, and with probability 11 in 41 it costs a 90 s SIGKILL wait.
  No data was lost or damaged in any of 329 checked cycles. By the data, U2 is the only variant that removed findings
  1 and 2 without a measured side effect; U1 removed finding 2 only.

## UNVERIFIED

- Which thread consumes systemd-shutdown's SIGTERM in the 90 s cases (inferred from kernel and libfuse sources plus
  the watcher's "nothing pending, main still in futex"; not observed per thread).
- That the lost wakeup is the only way a server gets stuck (41 of 41 stuck servers showed its state; others were not
  searched for), and whether mergerfs upstream knows it (no issue search was done).
- Whether the T051g difference was chance in T051g's own conditions (no writes before shutdown, no capture unit).
- U1/U2 on real disks, with the SMB client VM, under S1 load, with 4 vCPUs, other device timeouts; the cost of a
  re-sent `change` or `add` in a real slow boot (the sweep's R sleep is an artefact).
- The 20 files written before shutdowns whose next boot never mounted the pool.

## Criteria changelog note (for T052 `S1.yaml`)

> T051i (2026-10-19, systemd 260.5, kernel 6.18.56, mergerfs 2.41.1, server VM 1536 MiB, 2 vCPUs, nodes.nix options):
> variants of the T051f retrigger unit, all after systemd-udev-trigger and before local-fs.target. U1 = `udevadm
> settle --timeout=30; udevadm trigger --settle --action=change|add -s block -p ID_BTRFS_READY=0` (as 64-btrfs.rules);
> U2 = the same with settle bounded to 5 s, the trigger to 4 s (`timeout 4`), failures not failing the script,
> TimeoutStartSec=10s. Forced race: 10 of 10 rescued by each. Normal boots (30 each): all up, no event sent, no
> remount, restart or reader error. Missing member (10 each): exit 0, one change event to the present member, system
> running (the original: exit 1, system degraded). Udev forced unsettled 31 s: U2 exit 0 after 5.4 s, local-fs.target
> only at the device timeout; U1 as the original (30 s, exit 1). Rescue ends at the 10 s device timeout. Shutdown: the
> mergerfs FUSE server outlives its mount unit when its exit path loses a wakeup (sem_post before the exit flag,
> fuse_loop.cpp:80-81 and 319-320); without the unit 19/60 and 24/60, with the original 16/60 (Fisher p = 0.45 and
> 0.24), so the T051g difference (17/31 vs 8/31) is not reproduced; 11 of 41 stuck servers waited 90 s for SIGKILL;
> such a server has no mount and no open file; 329 data checks after the next boot (sha256 of files written with and
> without fsync, scrub, device stats, kernel log) all passed. Nothing is adopted.
