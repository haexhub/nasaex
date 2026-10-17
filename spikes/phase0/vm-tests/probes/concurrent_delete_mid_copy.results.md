# Probe result: client delete during the M1 copy (task T037f)

Measured on 2026-10-17 with the probe `concurrent_delete.nix` (`name = "probe-concurrent-delete-mid-copy"`)
/ `probe_concurrent_delete_mid_copy.py`, one VM test at a time, KVM, machine state on disk; each variant ran
twice with the same branch, listing and visibility results. A probe never writes a record; the full output
of each run is `probe-concurrent-delete-mid-copy.json` in the output directory of the driver (evidence, not
committed). Nothing below adopts an unlink setting or changes a contract.

Hypothesis tested (UNVERIFIED before this probe): under `epall` a client delete while the mover is held at
`mid:M1:tmp-data-synced` removes only the pool file, because the archive side holds the copy only under its
temporary name; the mover then renames the temporary copy into place, so the deleted file appears on the
archive branch and through the share, and M2/M5 would find "pool file missing, archive copy present".

## Environment

- Profile `small`; VMs `server` (1536 MiB, 2 vCPUs) and `client` (768 MiB, 1 vCPU): 2304 MiB of VM RAM per
  run, one run at a time.
- Guest: kernel 6.18.56, mergerfs v2.41.1, Samba 4.23.10; the client mounts the cache-pool share with
  `mount.cifs` twice (default options, and `actimeo=0`).
- Archive layout of task T028a: the data subvolume of every archive disk held exactly `branch` and `trash`.
- Setup and helpers are shared with the T037c probe (`delete_probe_setup.py`); after that refactor the T037c
  probe (default variant) was run once more and gave its recorded summary again (`epall`,
  `branches_after_delete` empty, S1 scenarios 4 and 5 as required).

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-concurrent-delete-mid-copy ./spikes/phase0#driver-probe-concurrent-delete-mid-copy-ff
SPIKE_STATE_DIR=<short directory on a disk> nix run ./spikes/phase0#probe-concurrent-delete-mid-copy
SPIKE_STATE_DIR=<short directory on a disk> nix run ./spikes/phase0#probe-concurrent-delete-mid-copy-ff
```

## Measurement (guest commands, verbatim from the probe output)

Two holds per run, one fresh 1 MiB item each: `probe/synced.bin` held at `mid:M1:tmp-data-synced`, then
`probe/partial.bin` held at `mid:M1:tmp-partial`. Shown for the first hold; the second is the same with
`partial` and `tmp-partial`:

```sh
server$ head -c 1048576 /dev/urandom > /mnt/pool/probe/synced.bin && chown spike /mnt/pool/probe/synced.bin
server$ sha256sum /mnt/pool/probe/synced.bin
server$ (rc=0; mover-effects mover_copy_item /mnt/pool/probe/synced.bin /mnt/base/probe/synced.bin 1 || rc=$?; echo $rc > /run/tmp-data-synced.rc) > /run/tmp-data-synced.log 2>&1 &
# held; then before the delete, after the delete and after the copy ended:
server$ test -e /mnt/pool/probe/synced.bin            # and every /mnt/archive/<disk>/branch/probe/synced.bin
server$ ls -A /mnt/pool/probe                         # and every /mnt/archive/<disk>/branch/probe
client$ stat /mnt/share/probe/synced.bin
client$ stat /mnt/share-actimeo0/probe/synced.bin
server$ stat /mnt/cache/probe/synced.bin
client$ ls -A /mnt/share/probe
client$ ls -A /mnt/share-actimeo0/probe
client$ s=$(date +%s%N); rm /mnt/share/probe/synced.bin; e=$(date +%s%N); echo $(((e - s) / 1000000))   # the delete
server$ python3 -c "import os; print(os.getxattr('/mnt/cache/.mergerfs', 'user.mergerfs.func.unlink').decode())"   # after the delete
server$ cat /run/tmp-data-synced.rc                   # after release, once the copy has ended
server$ sleep 3                                       # then stat and ls -A through both mounts again
server$ sleep 32                                      # 35 s after the copy ended: stat and ls -A again
server$ sha256sum /mnt/archive/arch1/branch/probe/synced.bin
server$ cat /run/tmp-data-synced.log
```

The driver runs every command under `set -e`; `|| rc=$?` keeps the exit status of a failing copy (without it
the background subshell ended before writing the status).

### Hold at `mid:M1:tmp-data-synced` (temporary copy complete and synced, not renamed)

| | default (no `func.unlink` option) | candidate `func.unlink=ff` |
| --- | --- | --- |
| effective `func.unlink` (runtime xattr, at setup and after the delete) | `epall` | `ff` |
| branches holding the path, before the delete | `pool` | `pool` |
| branch listings before the delete | `pool`: `synced.bin`; `arch1`: `.synced.bin.tmp-<pid>`; `arch0`, `arch2`: empty | same |
| branches holding the path, after the delete (held) | none | none |
| branch listings after the delete (held) | `pool`: empty; `arch1`: `.synced.bin.tmp-<pid>` | same |
| path visible after the delete: share / share `actimeo=0` / cache pool on the server | no / no / no | no / no / no |
| client delete duration (two runs) | 8 ms, 8 ms | 10 ms, 11 ms |
| exit status of `mover_copy_item` after release | 0 (both runs) | 0 (both runs) |
| branches holding the path, after the copy ended | `arch1` | `arch1` |
| branch listings after the copy ended | `pool`: empty; `arch1`: `synced.bin` (temporary name gone) | same |
| final archive copy byte-identical to the original item (sha256) | yes | yes |
| path visible right after the copy ended: share / share `actimeo=0` / cache pool on the server | no / **yes** / **yes** | no / **yes** / **yes** |
| the same 3 s and 35 s after the copy ended | no / yes / yes | no / yes / yes |
| directory listing through both mounts, right after, 3 s and 35 s after the copy ended | `.synced.bin.tmp-<pid>` (stale) | same |
| directory listing through both mounts after the next change through SMB in the directory (the delete of the second hold) | `synced.bin` listed | same |
| state M2/M5 would find (by branch listing; no M2 runs in the probe) | pool file missing, archive copy present (trash path) | same |

### Hold at `mid:M1:tmp-partial` (half of the bytes written, not synced)

| | default | candidate `func.unlink=ff` |
| --- | --- | --- |
| effective `func.unlink` after the delete | `epall` | `ff` |
| branches holding the path, before / after the delete / after the copy ended | `pool` / none / none | same |
| branch listings after the delete and after the copy ended | `pool`: empty; `arch1`: `.partial.bin.tmp-<pid>`, `synced.bin` | same |
| client delete duration (two runs) | 6 ms, 6 ms | 8 ms, 10 ms |
| exit status of `mover_copy_item` after release | 1 (both runs); log: `FileNotFoundError: [Errno 2] No such file or directory: '/mnt/pool/probe/partial.bin'` (the extent reader of the second half) | same |
| temporary name after the copy ended | `.partial.bin.tmp-<pid>` left on `arch1` (the partial copy is not removed) | same |
| final archive copy | none | none |
| path visible after the delete and after the copy ended (incl. 3 s and 35 s): share / `actimeo=0` / server | no / no / no | no / no / no |
| directory listing through both mounts after the copy ended (incl. 35 s) | `.partial.bin.tmp-<pid>`, `synced.bin` | same |
| state M2/M5 would find (by branch listing) | pool file missing, archive copy missing (`vanished-archive-gone`); the copy failed in M1 | same |

Probe duration inside the test script: 102.0 s and 100.9 s (default), 102.1 s and 101.5 s (`ff`).

## What this means for the trash and vanished cases

- The hypothesis holds, and it holds under both unlink policies: a client delete while the mover is held at
  `mid:M1:tmp-data-synced` removes only the pool file (the archive side holds no file under the client's
  path yet), the mover's rename then puts a complete, byte-identical archive copy under the final name, and
  the item reaches "pool file missing, archive copy present", the precondition of the trash path. Under
  `epall` the trash path is therefore reachable at a named point, which the T037c probe (delete at
  `post:M1`) could not show. `func.unlink=ff` makes no difference at this point.
- The deleted file reappears after the rename: it is visible through the cache pool on the server and through
  the `actimeo=0` mount at once and stays visible (until a trash move, which this probe does not run). The
  default mount did not find it by `stat` within 35 s, and its directory listing showed the stale temporary
  name until the next change through SMB in that directory; why the default mount stays stale is not
  measured (a cifs directory cache that Samba does not invalidate for changes made outside SMB is a guess).
  This is the visible window of known limit 1 of the mover contract.
- A delete at `mid:M1:tmp-partial` makes the copy primitive fail (exit 1) when it reads the second half of
  the deleted source; no final archive copy exists, the item is a vanished item without archive copy, and a
  partial temporary file `.<name>.tmp-<pid>` stays on the archive branch and is listed through the share.
  Under both policies.
- Not measured: an actual M2 to M5 run on the resulting state (trash rename, the comparison of the archive
  copy with its copy-time record), deletes at `mid:M1:tmp-created`, `mid:M1:renamed` and later points, a
  crash combined with the mid-copy delete, and how long the default mount stays stale.

## Contract statements this measurement contradicts (no contract edited, rule 6)

- `specs/001-phase0-storage-spikes/contracts/mover-steps.md:384-389` (known limit 4) says that under `epall`
  the only state with the pool file gone and the archive copy present is the window between the per-branch
  unlinks, "which no named point can hit". Measured: a delete at the named point `mid:M1:tmp-data-synced`
  reaches that state under `epall` (two runs).
- `contracts/fault-points.md:61` arms the delete injections only at `post:M1` and `post:M3`; a delete
  injection at `mid:M1:tmp-data-synced` would be an executable route to the trash outcome and the trash
  crash points under the mergerfs default.
- `contracts/mover-steps.md:106-110` and the vanished-item table (`mover-steps.md:336-344`) describe the
  vanished states only for a delete after M1. A delete during M1 is not covered: the temporary copy becomes
  the archive copy after the delete (`tmp-data-synced`), or the copy fails and leaves a partial temporary
  file that shows through the share (`tmp-partial`); the "before `post:M1`" row of the permitted-state table
  (`mover-steps.md:331`) expects the pool copy present.

## Criteria changelog note (for T091 `S3.yaml`)

> Probe T037f (2026-10-17, mergerfs 2.41.1, Samba 4.23.10): with the mover held at `mid:M1:tmp-data-synced`
> a client delete through the share removed only the pool file under both `epall` (default) and
> `func.unlink=ff`; after release the mover renamed the synced temporary copy into place (exit 0, archive
> copy byte-identical), leaving "pool file missing, archive copy present" (the trash precondition), with the
> file visible again through the cache pool and an `actimeo=0` mount. Held at `mid:M1:tmp-partial` the
> delete made the copy fail (exit 1), left a partial temporary file on the archive branch and no archive copy
> (`vanished-archive-gone`). No unlink setting and no point list change is adopted by this note.
