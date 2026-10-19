# Probe result: when the S1.9 retry can succeed (task T051h)

Measured on 2026-10-19 with the probe `enospc.nix` / `enospc_probe_setup.py` + `probe_retry_timeline.py`
(summary helpers in `retry_timeline.py`), one VM test at a time, KVM, machine state on an ext4 disk. Run 1:
`PROBE_ROUNDS=3`, all five variants interleaved (15 repetitions, 1642 s). Run 2, after run 1 showed a wrong
append read back: `PROBE_ROUNDS=3 PROBE_VARIANTS=V0`, with the file size and the bytes after the leftover
recorded (3 repetitions, 332 s). Run 3, whether mergerfs causes the zero gap of the append retry:
`probe_append_retry.py`, variants Vr and V5 to V8 (32 repetitions, 214 s). A probe never writes a record; the
full output is `probe-retry-timeline.json` / `probe-append-retry.json` in the output directory of the driver
(evidence, not committed). Nothing below edits the spec, the criteria or the S1.9 check.

Question: the amended S1.9 check (T047a, branch `001-phase0-s1-checks-amend`, commit `0d546c6`) passed every
point except its retry (S1.9 observable, last sentence): after the out-of-space writes the server removes the
filler, the client deletes the leftovers of the create and the upload through the share and repeats each
write; in the shakedown the create retry failed with `EROFS at open`, the upload retry with
`NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file`, and both "lie on []". Hypotheses before the probe
(UNVERIFIED): H1, btrfs releases the space of the deleted files only at the next transaction commit, so the
pool branch is still below `minfreespace` (64M) and mergerfs refuses the create; H2, something else (mergerfs
or cifs caches, leftovers still present).

## Environment

- Profile `large`; VMs `server` (1536 MiB, 2 vCPUs) and `client` (768 MiB, 1 vCPU): 2304 MiB of VM RAM.
- Guest: kernel 6.18.56 (server and client), mergerfs v2.41.1, Samba 4.23.10, smbclient 4.23.10, mount.cifs
  7.5, btrfs-progs v6.19.1.
- Setup of the T051b probe (S1 server, S1 data set without its file above 4 GiB, share `cache` on the
  mergerfs cache pool, the two S1 client sessions, the control share `poolctl` served directly from
  `/mnt/pool`, mounted at `/mnt/ctl`). Effective cache pool options read back: `category.create=ff`,
  `moveonenospc=false`, `minfreespace=67108864`, `cache.files=off`; branches read back after every repetition:
  `/mnt/pool=RW:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC:/mnt/archive/arch2/branch=NC`
  (no branch was switched to RO).
- The pool branch mount, from `/proc/mounts`: `/dev/vdb /mnt/pool btrfs
  rw,relatime,discard=async,space_cache=v2,subvolid=256,subvol=/pool 0 0`. No `commit=` option is shown;
  btrfs prints it only when it differs from the default (kernel 6.18.56 `fs/btrfs/super.c:1139-1140`), and
  the default is `BTRFS_DEFAULT_COMMIT_INTERVAL` 30 s (`fs/btrfs/fs.h:319`).
- Sizes from the S1 criteria: `leave_bytes` 100663296, `write_bytes` 268435456, append target 4096 bytes. The
  seeded source (SHA-256 `4bd4e965...389d`) is generated on both VMs and compared.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-retry-timeline
SPIKE_STATE_DIR=<short directory on an ext4 disk> PROBE_ROUNDS=3 nix run ./spikes/phase0#probe-retry-timeline
SPIKE_STATE_DIR=<short directory on an ext4 disk> PROBE_ROUNDS=3 PROBE_VARIANTS=V0 nix run ./spikes/phase0#probe-retry-timeline
nix build ./spikes/phase0#driver-probe-append-retry
SPIKE_STATE_DIR=<short directory on an ext4 disk> PROBE_REPEATS=5 nix run ./spikes/phase0#probe-append-retry
```

## What one repetition does (guest commands, from the probe output and the probe code)

```sh
# the S1.9 sequence of the check, each write from its own filler (testlib.faults.enospc)
server$ sync -f /mnt/pool; df --output=avail -B1 /mnt/pool | tail -n1
server$ fallocate -o 0 -l <avail - 100663296> /mnt/pool/.spike-filler; sync -f /mnt/pool; df ...
client$ python3 -c '<WRITER>' /mnt/share/t051h/V0-r0/create.bin wb /root/enospc-source.bin 268435456
server$ rm -f /mnt/pool/.spike-filler                         # after each write, as the check does
client$ smbclient //server/cache -U 'spike%spike' -c 'put "/root/enospc-source.bin" "t051h/V0-r0/put.bin"'
client$ python3 -c '<WRITER>' /mnt/share/t051h/V0-r0/append.bin ab /root/enospc-source.bin 268435456
client$ rm -f -- /mnt/share/t051h/V0-r0/create.bin          # the leftovers, through the share
client$ rm -f -- /mnt/share/t051h/V0-r0/put.bin             # t0 = end of this delete
# variant step
server$ sync -f /mnt/pool                                   # V1
server$ btrfs filesystem sync /mnt/pool                     # V2
# the retries (the three writes again, same paths), each with a sample before and after
# the timeline: for 90 s, every 0.5 s, a sample and a 1 MiB write to a new name
server$ python3 -c '<SAMPLER>' <pool fsid> /mnt/pool /mnt/cache
client$ python3 -c '<WRITER>' /mnt/share/t051h/V0-r0/tick-NNN.bin wb /root/enospc-source.bin 1048576
# read back of every retry that reported no error, on the pool branch
server$ dd if=/mnt/pool/t051h/V0-r0/append.bin iflag=skip_bytes,count_bytes skip=4096 count=<leftover> bs=1M status=none | sha256sum
server$ head -c <leftover> /root/enospc-source.bin | sha256sum
server$ dd if=/mnt/pool/t051h/V0-r0/append.bin iflag=skip_bytes,count_bytes skip=<4096 + leftover> bs=1M status=none | sha256sum
```

`<SAMPLER>` (`retry_timeline.SAMPLER`) reads `os.statvfs` of `/mnt/pool` and `/mnt/cache` (`f_bavail *
f_frsize`, the value mergerfs compares with `minfreespace`, `src/statvfs_util.hpp:38-41`),
`/sys/fs/btrfs/<fsid>/allocation/data/{disk_used,disk_total,bytes_used,bytes_pinned,bytes_may_use,bytes_reserved}`,
the commit counter of `/sys/fs/btrfs/<fsid>/commit_stats`, `btrfs filesystem df -b /mnt/pool` and `btrfs
filesystem usage -b /mnt/pool`. V3 runs the timeline first (from t0) and the retries after it; V0 to V2 and V4
run the retries first. V4 does V0 through `poolctl` (`/mnt/ctl`, smbclient `//server/poolctl`).

## Results, run 1 (verbatim from `probe-retry-timeline.json`)

Leftovers of the failed writes (create / put / append, bytes on the pool branch; the failed writes reported
`ENOSPC` at `fsync` for mount.cifs and `cli_push returned NT_STATUS_DISK_FULL`, then
`NT_STATUS_INVALID_NETWORK_RESPONSE closing remote file` for smbclient, in all 15 repetitions); the filler of
the append write was 8009994240 bytes in every repetition. "Before the create retry" is the sample taken
right before it (seconds after t0). t0 came 0.044 to 0.069 s after the last filler was removed.

| var | r | leftovers create / put / append | before the create retry | create retry | put retry | append retry (commits before -> after) |
| --- | --- | --- | --- | --- | --- | --- |
| V0 | 0 | 100564992 / 100638720 / 100662656 | 0.101 s: avail 8103038976, disk_used 418045952, pinned 8103038976, commits 95 | ok | ok | ok (96 -> 96) |
| V0 | 1 | 100662656 / 100662592 / 100662656 | 0.101 s: avail 0, disk_used 16624123904, pinned 0, commits 576 | `EROFS` at `open`, 0 written | `NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file \t051h\V0-r1\put.bin`, exit 1 | ok (576 -> 577) |
| V0 | 2 | 100661248 / 100662080 / 100661472 | 0.081 s: avail 0, disk_used 16624123904, pinned 0, commits 1056 | `EROFS` at `open`, 0 written | `NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file \t051h\V0-r2\put.bin`, exit 1 | ok (1056 -> 1057) |
| V1 | 0 | 100662656 / 100662592 / 100662656 | `sync -f` 0.094-0.112 s (before: avail 0, commits 192); 0.202 s: avail 8211320832, disk_used 201482240, commits 193 | ok | ok | ok |
| V1 | 1 | 100662656 / 100661680 / 100662656 | `sync -f` 0.102-0.135 s (before: avail 0, commits 673); 0.247 s: avail 8211320832, commits 674 | ok | ok | ok |
| V1 | 2 | 100661472 / 100659296 / 100661472 | `sync -f` 0.101-0.137 s (before: avail 0, commits 1153); 0.242 s: avail 8211320832, commits 1154 | ok | ok | ok |
| V2 | 0 | 100662656 / 100661792 / 100662656 | `btrfs filesystem sync` 0.103-0.123 s (before: avail 0, commits 289); 0.236 s: avail 8211320832, commits 290 | ok | ok | ok |
| V2 | 1 | 100662656 / 100662592 / 100662656 | `btrfs filesystem sync` 0.103-0.128 s (before: avail 0, commits 770); 0.231 s: avail 8211320832, commits 771 | ok | ok | ok |
| V2 | 2 | 100661472 / 100662080 / 100661472 | `btrfs filesystem sync` 0.127-0.155 s (before: avail 0, commits 1250); 0.265 s: avail 8211320832, commits 1251 | ok | ok | ok |
| V3 | 0 | 100659200 / 100661680 / 100662656 | retries at 90.255 s: avail 8144211968 | ok | ok | ok |
| V3 | 1 | 100662656 / 100661680 / 100662656 | retries at 90.287 s: avail 8147357696 | ok | ok | ok |
| V3 | 2 | 100661472 / 100659296 / 100661472 | retries at 90.262 s: avail 8147357696 | ok | ok | ok |
| V4 | 0 | 100663296 / 100663296 / 100663296 | 0.096 s: avail 0, disk_used 16624123904, pinned 0, commits 479 | ok (commits 479 -> 480 during it) | ok | ok |
| V4 | 1 | 100663296 / 100663296 / 100663296 | 0.14 s: avail 0, disk_used 16624123904, commits 959 | ok (959 -> 960) | ok | ok |
| V4 | 2 | 100663296 / 100663296 / 100663296 | 0.097 s: avail 0, disk_used 16624123904, commits 1439 | ok (1439 -> 1440) | ok | ok |

Every retry that reported no error lies on the pool branch only (`holders` `['pool']`; the repetition's
directory existed on the pool branch only), and the create and put retries read back with the source. The
append retry read back as head, leftover, source in 13 of 15 repetitions; in **V0 r1 and V0 r2** (the two
repetitions in which the create and put retries failed and the append retry followed 0.805 s and 0.727 s
after t0) the read back gave `{'head': True, 'leftover': True, 'source': False}`: the original head and the
leftover are intact, but the bytes after the leftover are not the source. Run 1 did not record the file size
or what those bytes are; run 2 measures it (below).

V3 timelines (1 MiB writes through `/mnt/share` from t0, a sample every 0.5 s, 181 samples each):

| r | samples with `EROFS` at `open` | first successful 1 MiB write | first commit after t0 | sample before the first success | sample after it |
| --- | --- | --- | --- | --- | --- |
| 0 | 62 (t = 0.0 to 30.5 s) | t = 31.0 s (sampled 31.000, written 31.104) | 31.0 s | t = 30.5: avail 0, free 271286272, disk_used 16624123904, pinned 0, commits 387 | t = 31.0: avail 8211320832, disk_used 201482240, commits 388 |
| 1 | 63 (t = 0.0 to 31.0 s) | t = 31.5 s (sampled 31.500 with avail 0 and commits 867, written 31.610) | 32.0 s | t = 31.5: avail 0, commits 867 | t = 32.0: avail 8211320832, disk_used 201482240, commits 868 |
| 2 | 61 (t = 0.0 to 30.0 s) | t = 30.5 s (sampled 30.500 with avail 0 and commits 1347, written 30.585) | 31.0 s | t = 30.5: avail 0, commits 1347 | t = 31.0: avail 8211320832, disk_used 201482240, commits 1348 |

In every V3 sample before the first success: pool branch avail 0, `f_bfree` 271286272, `disk_used`
16624123904, `bytes_pinned` 0, `bytes_may_use` 0, the commit counter unchanged since t0, cache pool avail
19808083968, `btrfs filesystem df`: `Data, RAID1: total=8312061952, used=8312061952`, `btrfs filesystem
usage`: `Used: 16625238016`, `Free (estimated): 0 (min: 0)`, `Free (statfs, df): 0`. In the first sample after
the commit: avail 8211320832, `Data, RAID1: total=8312061952, used=100741120`, `Free (statfs, df):
8211320832`. Every write before the commit failed with `EROFS` at `open` (0 bytes written); every write
after it succeeded; no write failed after the first success. In r1 and r2 the write succeeded about 0.1 s
after a sample that still showed avail 0, and the next sample showed the incremented commit counter: the
commit fell between the sample and the write.

The other timelines (after the retries) show no failed 1 MiB write in V0, V1, V2, V4; the first commit after t0
in their timelines was at 30.9 to 33.8 s (V0 31.767, 33.312, 33.751; V1 31.576, 31.034, 31.988; V2 30.91,
32.509, 31.259; V4 33.728, 33.755, 32.429).

## Results, run 2: the append retry of V0

Same sequence; in addition the time from the end of each failed write to t0, the size of each retried file
on the pool branch, and for an appended file longer than head + leftover + source the bytes between the
leftover and the appended source:

```sh
server$ stat -c %s /mnt/pool/t051h/V0-r1/append.bin
server$ dd if=/mnt/pool/t051h/V0-r1/append.bin iflag=skip_bytes,count_bytes skip=100663520 count=167776032 bs=1M status=none | tr -d '\000' | wc -c
server$ dd if=/mnt/pool/t051h/V0-r1/append.bin iflag=skip_bytes,count_bytes skip=100663520 count=167776032 bs=1M status=none | sha256sum
server$ dd if=/root/enospc-source.bin iflag=skip_bytes,count_bytes skip=100659424 count=167776032 bs=1M status=none | sha256sum
server$ dd if=/mnt/pool/t051h/V0-r1/append.bin iflag=skip_bytes,count_bytes skip=268439552 bs=1M status=none | sha256sum
```

The pool branch mount line of this run: `/dev/disk/by-id/virtio-pool0 /mnt/pool btrfs
rw,relatime,discard=async,space_cache=v2,subvolid=256,subvol=/pool 0 0` (no `commit=`).

| r | before the create retry | create retry | put retry | append retry starts (after t0; failed append ended before t0) | append file on the pool branch | read back |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.102 s: avail 8174702592, disk_used 274718720, pinned 8174702592, commits 96 | ok | ok | 3.671 s (0.097 s) | 369102704 bytes = 4096 + 100663152 + 268435456 | head, leftover, source: True |
| 1 | 0.084 s: avail 0, disk_used 16624123904, pinned 0, commits 193 | `EROFS` at `open` | `NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file \t051h\V0-r1\put.bin` | 0.721 s (0.087 s); commits 193 -> 194 during it | 536875008 bytes, expected 369098976 | head True, leftover True, source False; bytes 100663520 to 268439551 (167776032): `nonzero_bytes` 0, not the source continuation; from 268439552 to the end: the source (`tail_is_source` True) |
| 2 | 0.095 s: avail 0, disk_used 16624123904, pinned 0, commits 290 | `EROFS` at `open` | `NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file \t051h\V0-r2\put.bin` | 0.772 s (0.092 s); commits 290 -> 291 during it | 536875008 bytes, expected 369102704 | head True, leftover True, source False; bytes 100667248 to 268439551 (167772304): `nonzero_bytes` 0, not the source continuation; from 268439552 to the end: the source |

In r1 and r2 the retried append wrote the source at offset 268439552 = 4096 + 268435456, the size the writer's
mount.cifs session showed after the failed append (T051b measured that the writer session reports the full
written size after a failed write, the second session and the server the leftover); the 167.8 MB between the
end of the leftover and that offset read as zeros on the pool branch. The append writer reported no error
(`{'errno': None, 'step': None, 'written': 268435456}`). In r0, where the retry started 3.8 s after the
failed append, the file is head + leftover + source. Run 1 shows the same split: the two append retries that
read back wrong started 0.805 s and 0.727 s after t0 (V0 r1, r2), the 13 that read back right started 3.7 s
or later after t0.

## Results, run 3: the append retry with and without mergerfs (Vr, V5 to V8)

One repetition of `probe_append_retry.py`: a 4096-byte append target on the pool branch, the S1.9 filler
(100663296 bytes free), a failed append of 268435456 bytes through the writer's mount, the leftover measured
on the pool branch, the filler removed, then (after the variant's delay) the client's `stat` of the file
through the retrying mount and the append again; then the file read on the pool branch. This probe omits the
check's create and upload steps. Here, "at once" means retrying 0.04 to 0.12 s after the failed append ended
(V0 above: about 0.8 s).

```sh
client$ python3 -c '<WRITER>' /mnt/share/t051h-append/Vr-0/append.bin ab /root/enospc-source.bin 268435456   # failed, then the retry
server$ stat -c %s /mnt/pool/t051h-append/Vr-0/append.bin                                                      # the leftover
server$ rm -f /mnt/pool/.spike-filler
client$ stat -c '%s %d' /mnt/share/t051h-append/Vr-0/append.bin 2>&1                                           # the client's size, right before the retry
server$ stat -c '%s %b %B' /mnt/pool/t051h-append/Vr-0/append.bin
server$ dd if=/mnt/pool/t051h-append/Vr-0/append.bin iflag=skip_bytes,count_bytes skip=<4096 + leftover> count=<gap> bs=1M status=none | tr -d '\000' | wc -c
server$ dd if=/mnt/pool/t051h-append/Vr-0/append.bin iflag=skip_bytes,count_bytes skip=<size - 268435456> bs=1M status=none | sha256sum
# V6: mount -t cifs //server/cache /mnt/share0 -o username=spike,password=...,actimeo=0
# V8: mount -t cifs //server/cache /mnt/fresh -o username=spike,password=...,nosharesock (after the failed append), umount after the retry
```

Mounts (from the client's `/proc/mounts`): all `vers=3.1.1,cache=strict`; `/mnt/share` (writer, default
options), `/mnt/share2` (`nosharesock,actimeo=0`), `/mnt/ctl` (`//server/poolctl`), `/mnt/share0`
(`actimeo=0`). Device numbers (`stat -c %d` of the mount points): writer 54, control 56, `actimeo=0` 57, the
new V8 session 58 (a superblock of its own).

Every failed append: `{'errno': 'ENOSPC', 'step': 'fsync', 'written': 268435456}`; every retry: `{'errno':
None, 'step': None, 'written': 268435456}`, holders `['pool']`, head and leftover intact, the source ends the
file. "gap" is size - 4096 - leftover - 268435456: the bytes between the leftover and the retry's source.

| variant | n | after the failed append | client size before the retry | file size | gap (bytes), all zero | unallocated (size - blocks x 512) | read back as head, leftover, source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Vr: mergerfs, writer's session, at once | 5 | 0.040 to 0.056 s | 268439552 (5 of 5) | 536875008 | 167775952 (4), 167775728 (1); 5 of 5 | 167772160 | 0 of 5 |
| V5: control share, no mergerfs, at once | 5 | 0.047 to 0.063 s | 268439552 (5 of 5) | 536875008 | 167772160; 5 of 5 | 167772160 | 0 of 5 |
| V6: mergerfs, writer at `actimeo=0`, at once | 5 | 0.045 to 0.067 s | 268439552 (5 of 5) | 536875008 | 167775952 (3), 167775728 (2); 5 of 5 | 167772160 | 0 of 5 |
| V8: mergerfs, new session, at once | 5 | 0.093 to 0.117 s | 100663824 (2), 100663600 (3) | 369099280 / 369099056 | 0 | 0 | 5 of 5 |
| V7: mergerfs, writer's session, delay 0.2 s | 2 | 0.223, 0.228 s | 268439552 | 536875008 | 167775952; 2 of 2 | 167772160 | 0 of 2 |
| V7, delay 0.5 s | 2 | 0.539, 0.553 s | 268439552 | 536875008 | 167775952; 2 of 2 | 167772160 | 0 of 2 |
| V7, delay 1.0 s | 2 | 1.027, 1.035 s | 268439552 | 369099056 | 0 | 0 | 2 of 2 |
| V7, delay 1.5 s | 2 | 1.523, 1.532 s | 268439552 | 369099056 | 0 | 0 | 2 of 2 |
| V7, delay 2.0 s | 2 | 2.033, 2.028 s | 268439552 | 369099056 | 0 | 0 | 2 of 2 |
| V7, delay 3.0 s | 2 | 3.037, 3.036 s | 100663600 | 369099056 | 0 | 0 | 2 of 2 |

In every file with a gap the retry's source starts at offset 268439552 = 4096 + 268435456, the size the
writing session reported, and 167772160 bytes (160 MiB) of the gap are not allocated on the pool branch (a
sparse hole: allocated 369102848 bytes for a size of 536875008); the rest of the gap (3792 or 3568 bytes
through mergerfs, 0 through the control share, whose leftovers were 100663296 bytes, 4096-aligned) is
allocated and zero. In V7 at 1.0, 1.5 and 2.0 s the client's `stat` still showed 268439552, yet the append
went to the real end of file.

## What this means

- **H1 holds in the form measured here, as a race with the btrfs commit.** Right after the last filler and the
  two leftovers are deleted, statvfs of the pool branch shows `f_bavail` 0 (`btrfs filesystem usage`:
  `Free (statfs, df): 0`, `Data, RAID1: used=8312061952` of 8312061952) and the btrfs data counter
  `disk_used` still counts the deleted files (16624123904). The freed space appears in one step, together
  with a transaction commit (the commit counter of `/sys/fs/btrfs/<fsid>/commit_stats` increments in the same
  sample interval): 30.5 to 31.5 s after t0 in V3 (3 of 3), at the periodic commit of the 30 s default
  interval. btrfs computes `f_bavail` from the data space info's `disk_used` (kernel 6.18.56
  `fs/btrfs/super.c` `btrfs_statfs`, lines 1729-1793), which `btrfs_update_block_group` lowers when the freed
  extents are accounted (`fs/btrfs/block-group.c:3759`); `btrfs_commit_transaction` runs the delayed refs
  (`fs/btrfs/transaction.c:2240`), and the transaction kthread commits once the running transaction is
  `commit_interval` old (`fs/btrfs/disk-io.c:1546-1577`). In the first repetition of both runs the space was
  already visible 0.1 s after t0, with `bytes_pinned` equal to the freed bytes and the commit counter
  incrementing during the create retry: a commit was already under way at that moment.
- **mergerfs turns the not-yet-visible space into EROFS, and Samba into MEDIA_WRITE_PROTECTED.** With the pool
  branch below `minfreespace` (0 < 67108864) and the archive branches `NC`, the `ff` create policy finds no
  branch: the pool branch gives ENOSPC (`src/policy_ff.cpp:50-51`), each NC branch EROFS
  (`src/policy_ff.cpp:43-44`), and `policy::calc_error` keeps EROFS over ENOSPC whatever the order
  (`src/policy_error.hpp:32-49`: from ENOSPC it takes any error but ENOENT, from EROFS it keeps EROFS against
  ENOENT and ENOSPC), so the create returns EROFS (`src/policy_ff.cpp:58`). The documentation says "Typically
  `EROFS` (read-only filesystem) or `ENOSPC` (no space left on device) depending on the most recent reason for
  filtering a branch" (`mkdocs/docs/config/functions_categories_policies.md:76-79`); the code ranks the errors
  rather than taking the most recent one. mergerfs then checks whether a branch is really read-only
  (`src/fuse_create.cpp:243-253`, `src/branches.cpp:441-456`, `src/fs_is_rofs.hpp:47-76`); the branch modes
  read back after every repetition were unchanged (`/mnt/pool=RW`, archive `NC`). Samba 4.23.10 maps EROFS to
  `NT_STATUS_MEDIA_WRITE_PROTECTED` (`libcli/util/errmap_unix.c:81-82`) and the cifs client maps it back to
  EROFS (kernel `fs/smb/client/smb2maperror.c:455`): that is the `EROFS at open` of the create and the
  `NT_STATUS_MEDIA_WRITE_PROTECTED opening remote file` of the upload, in the check and here (V0: 4 of 6
  repetitions over both runs; every 1 MiB write of V3 before the commit, 186 writes).
- **Without mergerfs the retry succeeds at once.** Through the control share (V4, Samba directly on the pool
  branch) all nine retries succeeded although the sample right before the create showed `f_bavail` 0; the
  commit counter incremented during the create retry, so btrfs itself made the freed space usable for the write.
  The refusal comes from the statvfs-based `minfreespace` filter of the mergerfs create policy, not from btrfs.
- **The append retry succeeds in V0** (6 of 6 over both runs, and in every other variant) because it opens an
  existing file: no create policy runs (create policies filter by `minfreespace`, action policies do not,
  `mkdocs/docs/config/functions_categories_policies.md:66-71`), and btrfs commits during the write (counter
  +1 during each V0 append retry that followed a failed create). **But an append retried through the writer's
  own mount.cifs session within about half a second of the failed append writes the data at the wrong
  offset**: the file becomes head + leftover + a 160 MiB sparse hole of zeros + source, and the writer gets no
  error (V0: 4 of 6 at 0.81 to 0.86 s after the failed append; run 3: 19 of 32 between 0.040 and 0.553 s).
  - **mergerfs does not cause it**: through the control share served by Samba directly from the plain btrfs
    pool the same retry left the same gap in 5 of 5 (V5).
  - **`actimeo=0` does not avoid it** (V6, 5 of 5 with the gap).
  - **A new mount.cifs session avoids it** (V8, `nosharesock`, its own superblock: 0 of 5), and so does
    waiting: with a delay of 1.03 s or more no gap in 8 of 8, with 0.55 s or less a gap in 4 of 4 (V7); the
    threshold lies between 0.55 and 1.03 s.
  - The offset is the size the writing client believed in after the failed append (268439552, its `stat`), so
    the append position comes from the client: the kernel places an O_APPEND write at the client inode's
    `i_size` (`fs/read_write.c:1747-1748`) and an SMB2 write carries its offset. This is a Linux cifs client
    artefact as far as measured, present on plain btrfs through Samba too. Since `actimeo=0` does not help and
    the threshold is about 1 s, the client's attribute cache (`CIFS_DEF_ACTIMEO` 1 s,
    `fs/smb/client/cifsglob.h:52`) does not explain it; consistent with the data, but not measured, is the
    deferred close of the cifs client: within `closetimeo` (default `SMB3_DEF_DCLOSETIMEO` 1 s,
    `fs/smb/client/fs_context.h:381`) a reopen with the same flags reuses the cached handle without a new SMB2
    CREATE (`fs/smb/client/file.c:1068-1088`, closes deferred only with a lease, `file.c:1426-1434`), and the
    client keeps its larger `i_size` while the inode is open for writing (`fs/smb/client/file.c:3120-3141`).
  - It is not the retry failure the check saw (the check reported only the create and the upload); the S1.9
    read back of the append (head, leftover, source) catches it.
- **When the retry becomes possible**: as soon as `f_bavail` of the pool branch reaches `minfreespace` again,
  which here happened only at a transaction commit: on its own at the next periodic commit (30.5 to 31.5 s
  after t0, 3 of 3), or at once after `sync -f /mnt/pool` (V1, 18 to 36 ms, 3 of 3) or `btrfs filesystem sync
  /mnt/pool` (V2, 20 to 28 ms, 3 of 3); every retry after either sync succeeded and read back.
- **A harness precondition for "the space is freed"**: when the space appeared, `f_bavail` jumped from 0 to
  8211320832, which is the freed bytes (append filler 8009994240 + the create and upload leftovers) plus 0 to
  200704 bytes, in every repetition. So "statvfs `f_bavail` of the pool branch >= filler + deleted leftovers
  - epsilon" became true in the same sample in which a 1 MiB create became possible (V3: the
  `first_free_need_s` and `first_write_ok_s` of the summary coincide or the write raced the sample by 0.1 s);
  epsilon 0 would have sufficed here, 16 MiB was used. Polled with an upper bound, it observes the commit
  instead of forcing it; the slowest case measured was 31.5 s after t0, so a bound of at least one commit
  interval plus slack is needed (60 s would have covered every case here; one sample per 0.5 s). `sync -f`
  forces the commit and made the space visible in under 40 ms in 3 of 3 (and `testlib.faults.enospc` already
  uses it before sizing a filler); it changes the state under test (a commit the client did not cause), while
  the poll leaves btrfs to its own schedule. Which one the check uses, and whether the 30 s window in which a
  client create through the share fails with EROFS / MEDIA_WRITE_PROTECTED after space was freed is itself
  an S1 finding, are the maintainer's decision.

## UNVERIFIED

- How often the immediate retry fails: 4 of 6 V0 repetitions over two runs, the first repetition of each run
  passed because a commit was under way. The rate depends on where t0 falls in the 30 s commit cycle and is not
  measured beyond these six.
- That the drop of `disk_used` at the commit comes from running the delayed refs of the freed extents is read
  from the kernel source and the counters (`disk_used`, `bytes_pinned`, commit count), not traced.
- That btrfs commits during the control-share and append writes to reclaim the freed space is inferred from
  the commit counter incrementing during those writes; the flush path is not traced.
- The cause of the stale append offset: client-side by the measured offset, the fresh-session and the
  no-mergerfs results, but the SMB2 WRITE offsets were not captured, and the deferred-close explanation is
  inferred from the kernel source and the 0.55 to 1.03 s threshold; not measured: `closetimeo=0`, the lease
  state, other cifs versions or `cache=` modes, other clients (smbclient, Windows), and a new session with
  options identical to the writer's (which the cifs client may give the writer's superblock).
- Whether the S1.9 check's own append retry can hit it depends on its timing after the failed append, which
  this probe did not measure (the shakedown reported no append read-back failure).
- Why the client's `stat` showed the stale size at 1.0 to 2.0 s while the append went to the right offset.
- Other commit intervals (`commit=`), other loads and other sizes.

## Criteria changelog note (for T052 `S1.yaml`)

> Probe T051h (2026-10-19, mergerfs 2.41.1, Samba 4.23.10, mount.cifs 7.5, kernel 6.18.56, btrfs-progs
> 6.19.1, pool mounted without `commit=`, so the 30 s default): after the S1.9 sequence (each write from its
> own filler, the filler removed, the create and upload leftovers deleted through the share), statvfs of the
> pool branch shows 0 bytes available until the next btrfs transaction commit; the freed space (filler +
> leftovers, 8211320832 bytes) appeared with the periodic commit 30.5 to 31.5 s later (3 of 3), or at once
> after `sync -f` or `btrfs filesystem sync` on the pool branch (3 of 3 each). Until then mergerfs (pool below
> minfreespace, archive branches NC) refuses every create with EROFS (policy_ff, policy::calc_error ranks EROFS
> over ENOSPC), which Samba reports as NT_STATUS_MEDIA_WRITE_PROTECTED; a share served directly from the pool
> branch accepted the same retries at once (3 of 3). The immediate create and upload retries of the check
> failed in 4 of 6 repetitions (EROFS at open, NT_STATUS_MEDIA_WRITE_PROTECTED at open), the append retry
> reported success in 6 of 6, but in the 4 cases it followed the failed retries within about 0.8 s of the
> failed append it wrote the source at the writer session's stale end of file, leaving 167.8 MB of zeros
> between the leftover and the source (head and leftover intact, no error reported). A follow-up (32 retries)
> shows it without mergerfs too (Samba directly on the pool branch, 5 of 5), with `actimeo=0` (5 of 5), and at
> 0.2 and 0.5 s after the failed append (4 of 4), but not through a new mount.cifs session (0 of 5) or at 1.0 s
> and later (0 of 8): a Linux cifs client behaviour (the append offset is the client's stale size; the gap is
> a 160 MiB sparse hole), not a mergerfs one. Not decided by this
> note: whether the check waits for the freed space by polling statvfs of the pool branch (>= filler +
> deleted leftovers, observed equal within 200704 bytes) with an upper bound, or forces it with `sync -f`,
> and whether the up to 30 s of EROFS / MEDIA_WRITE_PROTECTED for creates after space is freed, and the
> zero-filled append, are findings of S1. The S1.9 expected values are not changed by this note.
