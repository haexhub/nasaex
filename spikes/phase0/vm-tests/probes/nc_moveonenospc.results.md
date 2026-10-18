# Probe result: NC archive branches and moveonenospc (task T051c, research F10)

Measured on 2026-10-18 with the probe `enospc.nix` (`name = "probe-nc-moveonenospc"`) /
`enospc_probe_setup.py` + `probe_nc_moveonenospc.py`, one VM test at a time, KVM, machine state on an ext4
disk; one run with two rounds (`PROBE_ROUNDS=2`), 28 writes, 581 s wall time. A probe never writes a record;
the full output is `probe-nc-moveonenospc.json` in the output directory of the driver (evidence, not
committed). Nothing below adopts a setting or edits the criteria.

Question (S1 criteria changelog, open since T051): with the pool branch full and the archive branches in mode
NC with free space, where does a write that hits ENOSPC on the pool end up, with `moveonenospc=false` (the S1
baseline) and with `moveonenospc=true`? Archive data must never receive unexpected writes.

## Documented expectation (before measuring)

From the pinned mergerfs 2.41.1 source (`/nix/store/...-source`, rev `2.41.1`, the `src` of the nixpkgs
package):

- `mkdocs/docs/config/moveonenospc.md`: "When enabled and a `write` fails with `ENOSPC` or `EDQUOT` mergerfs
  will: 1. Run the policy defined by `moveonenospc` to find a target branch. 2. "Move" the file from the
  source branch to the target branch. 3. Retry the `write` and continue on as normal. If at any point
  something fails the progress so far will be cleaned up as appropriate and the original error returned. If
  `moveonenospc` is disabled the underlying error will be returned." and "This feature has NO effect on
  policies. It ONLY applies to the literal write function."
- `src/config_moveonenospc.cpp`: `moveonenospc=true` selects the create policy `pfrd`.
- `mkdocs/docs/config/branches.md`: "NC: (no-create) - Will be excluded from `create` policies. You can't
  create on that branch but you can change or delete."
- `src/policy_pfrd.cpp` (`_get_branchinfo`): a branch is skipped when `branch.ro_or_nc()` (EROFS), when its
  filesystem is read-only, or when `info.spaceavail < branch.minfreespace()` (ENOSPC); `src/branch.cpp:104`:
  `ro_or_nc()` is true for mode RO and NC. Every other create policy file contains the same `ro_or_nc()` check
  (grep of `src/policy_*.cpp`) except four: `rand` and `eprand` delegate to `Policies::Create::all` and
  `epall`, which contain it; `erofs` always returns EROFS; `cache` (`src/policy_cache.cpp`) wraps another
  policy (`return policy_(branches_,fusepath_,paths_)`) and was not read further.
- `src/fuse_write.cpp` and `src/fs_movefile_and_open.cpp`: with no eligible branch the policy returns an
  error and the write returns the original error. The source branch is **not** excluded from the policy: if
  the pool is still eligible (more than `minfreespace` available by statvfs, and `fs::has_space` for the file
  size), the target is the pool itself; `fs::copyfile` then clones the file to a temporary name in the same
  directory (`FICLONE` first), renames it over the original path, opens it, and `fs::unlink(src_filepath)`
  removes that same path.

Expected therefore: with the pool full (less than `minfreespace` left after the failed write) and every
archive branch NC, `moveonenospc=true` finds no branch and behaves like `false`: the write fails, nothing
reaches an archive branch. Not expected from the documentation, but read from the code: when a write fails
with ENOSPC/EDQUOT while statvfs still shows the pool above `minfreespace`, the pool itself is the move
target, and the unlink after the rename would leave the file without a name. The probe adds two appends
under a btrfs qgroup limit (EDQUOT at the write while statvfs shows about 8.3 GB free) to look at that case.

## Environment

- As in `enospc_control.results.md`: profile `large`; `server` 1536 MiB, 2 vCPUs, `client` 768 MiB, 1 vCPU
  (2304 MiB); kernel 6.18.56, mergerfs v2.41.1, Samba 4.23.10, mount.cifs 7.5, btrfs-progs v6.19.1; the S1
  setup, data set without its file above 4 GiB, share `cache` and two client sessions; sizes of the S1
  criteria (100663296 left free, 268435456 written, 4096-byte append targets); seeded source.
- Effective options of the cache pool, read back through the runtime xattrs:
  - `false` (the S1 baseline unit): `moveonenospc=false`, `minfreespace=67108864`, `category.create=ff`,
    `branches=/mnt/pool=RW:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC:/mnt/archive/arch2/branch=NC`
  - `true` (the unit's options with `moveonenospc=true`, mounted by hand through `s1.context.Lab.variant`, the
    S1 mount-variant mechanism): `moveonenospc=pfrd`, the rest identical.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-nc-moveonenospc
SPIKE_STATE_DIR=<short directory on a disk> PROBE_ROUNDS=2 nix run ./spikes/phase0#probe-nc-moveonenospc
```

## Measurement (guest commands)

The writes, filler, branch, content, inotify and manifest commands are those of
`enospc_control.results.md` (same setup file), with the share paths `/mnt/share/enospc/r<n>/<mode>-...` and the
local paths `/mnt/cache/enospc/r<n>/<mode>-...`. The qgroup precondition instead of the filler:

```sh
server$ btrfs quota enable /mnt/pool
server$ btrfs quota rescan -w /mnt/pool
server$ btrfs inspect-internal rootid /mnt/pool
server$ btrfs qgroup show --raw /mnt/pool
server$ btrfs qgroup limit <referenced + 100663296> /mnt/pool
# after the write
server$ btrfs qgroup limit none /mnt/pool
server$ btrfs quota disable /mnt/pool
```

## Results

Per mode, round 0 / round 1. "Leftover" is what the pool branch holds at the path (for appends: after the
4096-byte head); every leftover was a byte-exact prefix of the source and every append head stayed intact.

| write | `moveonenospc=false` | `moveonenospc=true` (pfrd) |
| --- | --- | --- |
| mount.cifs create (filler) | `ENOSPC` at `fsync`; leftover 100659200 / 100659984 | `ENOSPC` at `fsync`; leftover 100660032 / 100662752 |
| smbclient put (filler) | `cli_push returned NT_STATUS_DISK_FULL`, `NT_STATUS_INVALID_NETWORK_RESPONSE closing remote file`, exit 1; leftover 100662688 / 100661680 | same output; leftover 100661360 / 100660320 |
| mount.cifs append (filler) | `ENOSPC` at `fsync`; leftover 100660432 / 100659760 | `ENOSPC` at `fsync`; leftover 100659808 / 100662752 |
| local writer create on `/mnt/cache` (filler) | `ENOSPC` at `write`, 100659520 accepted; leftover 100659520 / 100659520 | same, 100659520 / 100659520 |
| local writer append on `/mnt/cache` (filler) | `ENOSPC` at `write`; leftover 100659520 / 100659520 | same, 100659520 / 100659520 |
| local writer append, qgroup limit (statvfs about 8.3 GB free) | `EDQUOT` at `write`, 100646912 accepted; leftover 100646912 / 100646912 | `EDQUOT` at `write`, 100646912 accepted; leftover 100646912 / 100646912 |
| mount.cifs append, qgroup limit | `ENOSPC` at `fsync`; leftover 100646912 / 100646912 | `ENOSPC` at `fsync`; leftover 100646912 / 100646912 |

For all 28 writes: the path was held by the pool branch only (`holders: ["pool"]`), on no archive branch;
the tree hashes of the three archive branches and the three trash directories were unchanged; inotify on the
three archive data subvolumes saw no event; the rest of the pool branch was unchanged (no other file, no
temporary name left behind); with the filler the pool had 0 bytes free afterwards; with the qgroup limit
statvfs still showed 8211316736 to 8211337216 bytes free after the write. The writer's own mount.cifs session
showed the full written size, the second session and the server the leftover (as in T051b).

## What this means

- NC keeps the archive out of `moveonenospc`, as documented: with the pool full and every archive branch NC,
  `moveonenospc=true` (pfrd) behaved exactly like `false` in 20 filler writes. Every client got its error,
  no archive branch received data or any write event, and the leftover stayed on the pool. This answers
  research F10 for this setup: NC excludes a branch from `moveonenospc`, measured, not only documented.
- With `moveonenospc=true` the pool is not excluded as a target. In the 8 qgroup writes (EDQUOT while statvfs
  showed about 8.3 GB free, so the pool stays eligible), the file did not vanish: the leftover, the head and
  the error were the same as with `false`, and no temporary name remained. The probe cannot tell whether
  mergerfs did not attempt the move or attempted it and failed and cleaned up (mergerfs logs nothing about it).
  The code path read above, in which the move goes to the source branch itself, was therefore not shown to be
  harmless; it was only not triggered by this precondition.
- `moveonenospc=true` brought no benefit in this topology (the only non-NC branch is the pool itself). Keeping
  `moveonenospc=false` as a required setting has no measured cost here, and it avoids a code path whose
  behaviour, when the move target is the source branch, is not measured.

## UNVERIFIED

- Whether `moveonenospc=true` loses a file when a write fails with ENOSPC or EDQUOT while the pool still has
  more than `minfreespace` and the file size available, for example because space is freed concurrently (the
  mover's M5 unlinks pool copies). Read from the source (`fs_movefile_and_open.cpp`: target equals source,
  rename over the same path, then `fs::unlink(src_filepath)`), not measured; the qgroup case did not trigger
  it, the mechanism that prevented it is not known.
- Any `moveonenospc` policy other than `true` (pfrd): not measured; the policy `cache` not read.
- Other mount variants (`cache.files`, `passthrough.io`), an archive branch without NC, and an archive branch
  that is itself full during an in-place change of an archived file.

## Criteria changelog note (for T052 `S1.yaml`)

> Probe T051c (2026-10-18, mergerfs 2.41.1, Samba 4.23.10, kernel 6.18.56): with the pool full (S1.9
> precondition) and the archive branches NC, moveonenospc=true (effective pfrd) behaved like the baseline
> false in 20 writes (mount.cifs create and append, smbclient put, local create and append, two rounds): the
> same errors (ENOSPC, NT_STATUS_DISK_FULL), the leftover on the pool only, no archive branch or trash changed
> (tree hashes, no inotify event). NC excludes a branch from moveonenospc, as the mergerfs documentation and
> policy code say (research F10, measured). In 8 appends under a btrfs qgroup limit (EDQUOT while the pool
> showed 8.3 GB free) the file stayed with the same leftover in both modes. The source shows that
> moveonenospc can pick the source branch itself; whether that loses a file is UNVERIFIED. No benefit of
> moveonenospc=true was measured in this topology.
