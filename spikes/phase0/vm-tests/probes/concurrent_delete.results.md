# Probe result: concurrent-delete fault kind (task T037c)

Measured on 2026-10-17 with the probe `concurrent_delete.nix` / `probe_concurrent_delete.py`, one VM test
at a time, KVM, machine state on disk; each variant ran twice with the same branch and visibility results. A probe never writes a record; the full output of each run is
`probe-concurrent-delete.json` in the output directory of the driver (evidence, not committed). Nothing
below adopts an unlink setting: adopting one is a reviewed amendment (required setting, FR-013) before the
S1 and S3 criteria are frozen.

## Environment

- Profile `small`; VMs `server` (1536 MiB, 2 vCPUs) and `client` (768 MiB, 1 vCPU): 2304 MiB of VM RAM per
  run, one run at a time.
- Guest: kernel 6.18.56, mergerfs v2.41.1, Samba 4.23.10; the client mounts the cache-pool share with
  `mount.cifs` twice (default options, and `actimeo=0` as an attribute-uncached second view).
- Archive layout of task T028a: the data subvolume of every archive disk held exactly `branch` and `trash`.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-concurrent-delete ./spikes/phase0#driver-probe-concurrent-delete-ff
SPIKE_STATE_DIR=<short directory on a disk> nix run ./spikes/phase0#probe-concurrent-delete
SPIKE_STATE_DIR=<short directory on a disk> nix run ./spikes/phase0#probe-concurrent-delete-ff
```

## Measurement (guest commands, verbatim from the probe output)

Item 1 is a 1 MiB file on the pool branch; the mover primitive copies it and the mover is held at `post:M1`
(the hook of `inject:delete-pool-after:M1`):

```sh
server$ systemctl cat mnt-cache.mount | sed -n 's/^Options=//p'    # at setup
server$ python3 -c "import os; print(os.getxattr('/mnt/cache/.mergerfs', 'user.mergerfs.func.unlink').decode())"    # at setup and after the delete
server$ (mover-effects mover_copy_item /mnt/pool/probe/item1.bin /mnt/base/probe/item1.bin 1 && spike_hook post:M1; echo $? > /run/probe-mover.rc) > /run/probe-mover.log 2>&1 &
server$ test -e /mnt/pool/probe/item1.bin            # and every /mnt/archive/<disk>/branch/probe/item1.bin
client$ s=$(date +%s%N); rm /mnt/share/probe/item1.bin; e=$(date +%s%N); echo $(((e - s) / 1000000))
server$ test -e /mnt/pool/probe/item1.bin            # and every /mnt/archive/<disk>/branch/probe/item1.bin
client$ stat /mnt/share/probe/item1.bin
client$ stat /mnt/share-actimeo0/probe/item1.bin
server$ stat /mnt/cache/probe/item1.bin
```

| | default (no `func.unlink` option) | candidate `func.unlink=ff` |
| --- | --- | --- |
| configured option | none | `func.unlink=ff` |
| effective `func.unlink` (runtime xattr) | `epall` | `ff` |
| branches before the delete | `pool`, `arch1` (archive copy = item hash) | `pool`, `arch1` (archive copy = item hash) |
| branches after the delete (`branches_after_delete`) | none | `arch1` |
| archive copy survived the delete | no | yes |
| path visible after the delete (`visible_before_trash`): share / share `actimeo=0` / cache pool on the server | no / no / no | yes / yes / yes |
| client delete duration (two runs each) | 16 ms, 21 ms | 13 ms, 16 ms |
| mover stand-in exit after release | 0 | 0 |
| S1 scenario 4 (archive-only file deleted through the share: gone from the share and every branch, nothing on the pool branch) | as required | as required |
| S1 scenario 5 (deleted archive file and directory recreated through the share: new content, empty new directory, no old entries on any branch) | as required | as required |
| probe duration inside the test script (two runs each) | 29.3 s, 27.2 s | 27.3 s, 28.4 s |

## What this means for the trash cases (mover contract, known limit 4)

- Under the mergerfs default the measured unlink policy is `epall`, as the pinned source predicts: a client
  delete at `post:M1` removes the pool file and the archive copy, so the item becomes a vanished item
  without archive copy (`vanished-archive-gone`). The trash outcome of `inject:delete-pool-after:M1` is not
  reachable at the named point under the default.
- With `func.unlink=ff` the delete removes only the pool copy; the archive copy survives and shows through
  the share until the mover trashes it (`visible_before_trash` true). The trash outcome of the delete
  injection is then reachable. S1 scenarios 4 and 5 behaved as the spec requires with both settings.
- Not measured: the delete at `post:M3`, the four trash crash cases (`mid:M5:trash-renamed`,
  `mid:M5:trash-parent-synced`, both kill modes), the window between the per-branch unlinks under `epall`,
  and other files present on two branches outside a mover run (with `ff` any such file reappears after a
  client delete).

## Criteria changelog note (for T042 `S1.yaml` and T091 `S3.yaml`)

> Probe T037c (2026-10-17, mergerfs 2.41.1, Samba 4.23.10): with no `func.unlink` option the effective
> unlink policy of the cache pool is `epall` and a client delete through the share of a file held on the
> pool branch and an archive branch (mover held at `post:M1`) removed both copies (`branches_after_delete`
> empty, not visible afterwards); with `func.unlink=ff` it removed only the pool copy, the archive copy
> stayed and was visible through the share. S1 scenarios 4 and 5 behaved as required under both. No unlink
> setting is adopted by this note; the trash outcome of the delete injections is reachable at a named point
> only under `ff`.
