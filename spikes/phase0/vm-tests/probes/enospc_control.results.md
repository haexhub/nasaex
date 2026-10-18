# Probe result: ENOSPC control, with and without mergerfs (task T051b)

Measured on 2026-10-18 with the probe `enospc.nix` / `enospc_probe_setup.py` + `probe_enospc_control.py`,
one VM test at a time, KVM, machine state on an ext4 disk; one run with two rounds (`PROBE_ROUNDS=2`), 28
writes. A probe never writes a record; the full output is `probe-enospc-control.json` in the output directory
of the driver (evidence, not committed). Nothing below edits the spec or the criteria.

Question (S1 shakedown T051, S1.9): when the pool is full, a write through the share fails with ENOSPC /
`NT_STATUS_DISK_FULL` and the archive is unchanged, but a partial file stays visible (S1: 100660272 of
268435456 bytes), so the observable "no file at either new path is visible with fewer bytes than written"
fails. Is that normal filesystem behaviour, or does mergerfs add it?

## Environment

- Profile `large`; VMs `server` (1536 MiB, 2 vCPUs) and `client` (768 MiB, 1 vCPU): 2304 MiB of VM RAM.
- Guest: kernel 6.18.56 (server and client), mergerfs v2.41.1, Samba 4.23.10, smbclient 4.23.10,
  mount.cifs 7.5, btrfs-progs v6.19.1.
- Setup of the S1 test script: `spike-mkfs all`, the pool (btrfs RAID1 of two 8 GiB disks, `subvol=pool`) and
  the archive data subvolumes mounted, base and cache pool from their units (effective `category.create=ff`,
  `moveonenospc=false`, `minfreespace=67108864`, `cache.files=off`), the S1 data set without its file above
  4 GiB, the S1 share `cache` and the two S1 client sessions (default, and `nosharesock,actimeo=0`).
- The control: a second share `poolctl` of the same smbd with the same keys, served directly from `/mnt/pool`,
  the pool branch. It is the same btrfs RAID1 filesystem with the same mount options and the same free space;
  the only difference is that mergerfs is not in the path. Mounted by the client with the same two session
  options at `/mnt/ctl` and `/mnt/ctl2`.
- Sizes from the S1 criteria (`tables.out_of_space`): `leave_bytes` 100663296, `write_bytes` 268435456; every
  append target is a 4096-byte pool file (as in S1.9).
- Every write starts from the S1.9 precondition: the filler of `testlib.faults.enospc` (`sync -f`, `df`,
  `fallocate` on `/mnt/pool/.spike-filler`) leaves 100663296 bytes free (read back each time:
  `100663296 bytes free after filling`); afterwards the written path is removed from every branch that holds
  it, then the filler.
- Source: a 268435456-byte seeded file (`/root/enospc-source.bin`, SHA-256 `4bd4e965...389d`), generated
  identically on server and client (checked by SHA-256). **Difference to S1.9**: S1.9 writes the constant
  byte 0xa5 (mount.cifs) and a sparse zero file from `truncate -s` (smbclient); the probe writes seeded bytes
  so that a leftover's content can be compared with the source prefix. Sizes, clients, session options and the
  write pattern (1 MiB writes, then fsync, close) are the same; the writer is unbuffered so that the bytes it
  reports are the bytes the write calls accepted.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-enospc-control
SPIKE_STATE_DIR=<short directory on a disk> PROBE_ROUNDS=2 nix run ./spikes/phase0#probe-enospc-control
```

## Measurement (guest commands, verbatim from the probe output and the probe code)

```sh
# writes (WRITER of probes/enospc_writes.py: open, 1 MiB writes from the source, fsync, close; reports errno, step, bytes)
client$ python3 -c '<WRITER>' /mnt/share/enospc/r0/cache-share-create.bin wb /root/enospc-source.bin 268435456   # cache-share create
client$ python3 -c '<WRITER>' /mnt/share/enospc/r0/cache-share-append.bin ab /root/enospc-source.bin 268435456   # cache-share append
client$ smbclient //server/cache -U 'spike%spike' -c 'put "/root/enospc-source.bin" "enospc/r0/cache-share-put.bin"' 2>&1
client$ python3 -c '<WRITER>' /mnt/ctl/enospc/r0/pool-share-create.bin wb ...    # and append; smbclient //server/poolctl ... put
server$ python3 -c '<WRITER>' /mnt/cache/enospc/r0/local-cache-create.bin wb ...  # and append; /mnt/pool/... for local-pool
server$ dd if=/root/enospc-source.bin of=/mnt/cache/enospc/r0/local-cache-dd.bin bs=1M conv=fsync
server$ cp /root/enospc-source.bin /mnt/cache/enospc/r0/local-cache-cp.bin
server$ dd if=/root/enospc-source.bin of=/mnt/pool/enospc/r0/local-pool-dd.bin bs=1M conv=fsync
server$ cp /root/enospc-source.bin /mnt/pool/enospc/r0/local-pool-cp.bin
# per write, before and after
server$ (inotifywait -m -r -e modify,attrib,close_write,create,delete,moved_to,moved_from --format '%e %w%f' /mnt/archive/arch0 /mnt/archive/arch1 /mnt/archive/arch2 > /run/inotify.log 2> /run/inotify.err &)
server$ df --output=avail -B1 /mnt/pool | tail -n1
client$ stat -c %s /mnt/share/<path>      # writer session; /mnt/share2/<path> second session (/mnt/ctl, /mnt/ctl2 for the control)
server$ stat -c %s /mnt/cache/<path>      # or /mnt/pool/<path>
server$ stat -c %s /mnt/pool/enospc/r0/cache-share-create.bin
server$ tail -c +1 /mnt/pool/enospc/r0/cache-share-create.bin | sha256sum      # appends: tail -c +4097, and head -c 4096 for the head
server$ head -c 100663280 /root/enospc-source.bin | sha256sum                   # the source prefix of the same length
server$ pkill -x inotifywait; sleep 0.5; true
server$ cat /run/inotify.log
```

The archive tree hashes (`testlib.manifest`, every path with type, size, mode, mtime, user xattrs and
SHA-256) of the three branch and three trash directories, the branches that hold the path (`os.path.lexists`
on each branch) and the pool manifest without the written path and the filler (`ls -A /mnt/pool`, then the
manifest below each top-level directory) are read on the server before and after every write.

## Results

Bytes the leftover holds on the pool branch (for appends: after the 4096-byte head); "intact" means the
SHA-256 of the leftover equals the SHA-256 of the source prefix of the same length (for appends: and the head
equals the placed head). Round 0 / round 1:

| target | write | client error | leftover on the pool branch | sizes the views report |
| --- | --- | --- | --- | --- |
| cache-share (Samba, mergerfs) | mount.cifs create | `ENOSPC` at `fsync`, 268435456 bytes accepted by write | 100663280 / 100661408, intact | writer session 268435456; second session and server = leftover |
| | smbclient put | `cli_push returned NT_STATUS_DISK_FULL`, then `NT_STATUS_INVALID_NETWORK_RESPONSE closing remote file`, exit 1 | 100662688 / 100661760, intact | all three = leftover |
| | mount.cifs append | `ENOSPC` at `fsync`, 268435456 accepted | 100661760 / 100661408, intact, head intact | writer session 268439552; others = 4096 + leftover |
| pool-share (Samba, no mergerfs) | mount.cifs create | `ENOSPC` at `fsync`, 268435456 accepted | 100663296 / 100663296, intact | writer session 268435456; others = leftover |
| | smbclient put | same two lines as above, exit 1 | 100663296 / 100663296, intact | all three = leftover |
| | mount.cifs append | `ENOSPC` at `fsync`, 268435456 accepted | 100663296 / 100663296, intact, head intact | writer session 268439552; others = 4096 + leftover |
| local-cache (mergerfs, no Samba) | writer create | `ENOSPC` at `write`, 100659200 / 100659520 accepted | 100659200 / 100659520, intact | = leftover |
| | writer append | `ENOSPC` at `write`, 100659520 / 100659520 accepted | 100659520 / 100659520, intact, head intact | 4096 + leftover |
| | dd | `dd: error writing '...': No space left on device`, `96+0 records out`, `100663296 bytes (101 MB, 96 MiB) copied`, exit 1 | 100663296 / 100663296, intact | = leftover |
| | cp | `cp: error writing '...': No space left on device`, exit 1 | 100663296 / 100663296, intact | = leftover |
| local-pool (plain btrfs) | writer create | `ENOSPC` at `write`, 100663296 accepted | 100663296 / 100663296, intact | = leftover |
| | writer append | `ENOSPC` at `write`, 100663296 accepted | 100663296 / 100663296, intact, head intact | 4096 + leftover |
| | dd | `No space left on device`, `100663296 bytes (101 MB, 96 MiB) copied`, exit 1 | 100663296 / 100663296, intact | = leftover |
| | cp | `No space left on device`, exit 1 | 100663296 / 100663296, intact | = leftover |

For all 28 writes: the free space after the write was 0 bytes; the written path was held by the pool branch
only; no archive branch or trash directory changed its tree hash; inotify saw no event on the three archive
data subvolumes; the rest of the pool branch (every other path, with content) was unchanged. The client
lines contain one more line in every smbclient output, `Can't load /etc/samba/smb.conf - run testparm to debug
it` (the client VM has no smb.conf).

## What this means

- The partial file is normal filesystem behaviour. A plain btrfs RAID1 filesystem, written locally with the
  probe writer, `dd` or `cp`, keeps exactly the bytes that fit (100663296, the free space) and returns ENOSPC;
  `dd` and `cp` leave the partial file in place. Samba without mergerfs (the control share) leaves the same
  100663296-byte file for mount.cifs and smbclient. None of these removes a partial file on ENOSPC. write(2) (Linux man-pages)
  documents this: "The number of bytes written may be less than count if, for example, there is insufficient
  space on the underlying physical medium", and ENOSPC means "The device containing the file referred to by fd
  has no room for the data"; nothing in it removes the bytes already written.
- mergerfs adds no visible file and removes none; it changes the leftover's size slightly: through mergerfs the
  probe writer (local) and the three share writes left 100659200 to 100663280 bytes (16 to 4096 bytes short of
  the free space), while `dd` and `cp` through mergerfs and every write without mergerfs left the full
  100663296. In all 28 writes the leftover is a byte-exact prefix of the source: no zeroed or
  misplaced block, and the head of every appended file is intact.
- The mount.cifs writer's own session shows the full written size (268435456) after the failed write, because
  the kernel client accepted all writes into its cache and reported ENOSPC only at `fsync`; the second session
  (`actimeo=0`) and the server show the real leftover. This is the same with and without mergerfs.
- A defensible S1.9 observable is therefore not "no file visible with fewer bytes than written": that fails
  for every filesystem measured here. What the measurement supports, and what distinguishes harm from normal
  behaviour: every client reports the failure (ENOSPC from mount.cifs at the latest at `fsync`/`close`,
  `NT_STATUS_DISK_FULL` from smbclient); the archive branches and trash are unchanged (tree hashes, and no
  write event on the archive filesystems); the written path exists on the pool branch only; the leftover, if
  any, is a prefix of what was written, no larger than what was accepted; no other file of the pool changes;
  an append target keeps its original head. Whether a leftover may stay visible, or a cleanup procedure is
  required, is a decision for the maintainer (criteria are not edited by a probe).

## UNVERIFIED

- The S1.9 shakedown upload left 201322496 bytes with 100663296 free; the probe's seeded upload left about
  100662688. The S1 upload source was a sparse file of zeros (`truncate -s`); whether zero content explains
  the larger leftover (for example through holes or zero handling somewhere in the path) is not measured.
- Other mergerfs settings (`cache.files`, `passthrough.io`, `moveonenospc=true`; the last one is probe T051c).
- Whether the leftover is stable after a reboot or remount; read once, right after each write.

## Criteria changelog note (for T052 `S1.yaml`)

> Probe T051b (2026-10-18, mergerfs 2.41.1, Samba 4.23.10, mount.cifs 7.5, kernel 6.18.56): from the S1.9
> precondition (100663296 bytes free, 268435456 written), every write in 28 (two rounds) left a partial file
> that is an exact prefix of the source, with mergerfs (S1 share, and locally through the cache pool) and
> without it (a Samba share served directly from the pool branch, and local writer, dd and cp on the pool
> branch). Plain btrfs and Samba without mergerfs kept exactly the 100663296 free bytes; through mergerfs the
> writer and the share writes kept 16 to 4096 bytes fewer (dd and cp the full amount). mount.cifs reported ENOSPC at fsync, smbclient NT_STATUS_DISK_FULL, local tools ENOSPC; no
> archive branch or trash changed (tree hashes, no inotify event), no other pool file changed, every append
> head stayed intact. The observable "no file at a new path visible with fewer bytes than written" fails for
> every filesystem measured, so it does not separate the tiered cache from a plain filesystem; the S1.9
> expected values are not changed by this note.
