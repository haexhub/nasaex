# Probe result: SMB attribute commands and smb.conf keys (task T039)

Measured on 2026-10-17 with the probe `smb_attrs.nix` / `probe_smb_attrs.py` (plan open item O12; research D4,
3.6), one VM test at a time, KVM, machine state on disk; the probe ran twice and both runs gave the same summary.
A probe never writes a record; the full output of each run is `probe-smb-attrs.json` in the output directory of
the driver (evidence, not committed). The S1 criteria file does not exist yet (T042), so the changelog note and
the required-settings list are given below, ready to paste. Nothing here adopts a setting.

## Environment

- Profile `small`; VMs `server` (1536 MiB, 2 vCPUs) and `client` (768 MiB, 1 vCPU): 2304 MiB of VM RAM allocated
  per run, one run at a time; the QEMU processes peaked at about 490 MiB (server) and 390 MiB (client) resident
  in the second run.
- Guest: kernel 6.18.56 (server and client), mergerfs v2.41.1, Samba 4.23.10 (`smbd` and `smbclient`),
  `mount.cifs` 7.5.
- The server shares the cache pool `/mnt/cache` three times, each with `read only = no` and
  `valid users = spike`: `attrs-default` (no attribute key, Samba's defaults), `attrs-on`
  (`store dos attributes = yes`, `ea support = yes`) and `attrs-off` (both `no`). The client mounts each share
  with `mount.cifs` (`actimeo=0`, so that the attribute cache does not hide what a command exposes) and uses
  `smbclient` through the S1 client driver `testlib/smb.py` (T038).
- Per share, a file of each origin: `pool.txt` created through the share (lands on the pool branch) and
  `archive.txt` created on the archive branch `arch0` (owned by the share user). Both got `user.preset` set
  directly on their branch by the server.
- Duration of the test script: 36.7 s and 37.7 s.

## Host commands

```sh
nix build ./spikes/phase0#driver-probe-smb-attrs
SPIKE_STATE_DIR=<short directory on a disk> nix run ./spikes/phase0#probe-smb-attrs
```

## Measurement (guest commands, verbatim from the probe output; shown for the share `attrs-default`)

```sh
server$ testparm -s --section-name=attrs-default --parameter-name='store dos attributes' 2>/dev/null   # and the other keys and shares
client$ mkdir -p /mnt/attrs-default && mount -t cifs //server/attrs-default /mnt/attrs-default -o username=spike,password=probe,actimeo=0
client$ smbclient //server/attrs-default -U spike%probe -c 'allinfo "attrs-default/pool.txt"'                 # DOS attributes (line "attributes: ... (<hex>)")
client$ smbclient //server/attrs-default -U spike%probe -c 'setmode "attrs-default/pool.txt" +hs'             # set hidden and system
client$ python3 -c '... os.getxattr(sys.argv[1], sys.argv[2]) ...' /mnt/attrs-default/attrs-default/pool.txt user.cifs.dosattrib   # cifs pseudo-xattr, 4 bytes little endian
client$ python3 -c 'import os, sys; os.setxattr(sys.argv[1], sys.argv[2], bytes.fromhex(sys.argv[3]))' /mnt/attrs-default/attrs-default/pool.txt user.probe 61747472732d64656661756c74
client$ python3 -c '... os.listxattr ...' /mnt/attrs-default/attrs-default/pool.txt                           # user xattrs through the share
client$ python3 -c '... os.setxattr ...' /mnt/attrs-default/attrs-default/pool.txt user.cifs.dosattrib 27000000  # set readonly through the cifs pseudo-xattr
server$ python3 -c 'import json, os, sys; p = sys.argv[1]; print(json.dumps({n: os.getxattr(p, n).hex() for n in sorted(os.listxattr(p))}))' /mnt/pool/attrs-default/pool.txt   # what the branch stores
server$ systemctl restart samba-smbd.service            # then the client unmounts and mounts every share again and reads once more
client$ sha256sum < /mnt/attrs-default/attrs-default/pool.txt
client$ smbclient //server/attrs-default -U spike%probe -c 'get "attrs-default/pool.txt" -' | sha256sum
```

## Effective share parameters (`testparm`)

| key | `attrs-default` | `attrs-on` | `attrs-off` |
| --- | --- | --- | --- |
| `store dos attributes` | Yes | Yes | No |
| `ea support` | Yes | Yes | No |
| `map archive` | Yes | Yes | Yes |
| `map hidden` | No | No | No |
| `map system` | No | No | No |
| `map readonly` | no | no | no |

## Which command exposes which attribute

Same results for both origins (pool branch, archive branch) and both runs, except the one cell marked (1).

| measurement | `attrs-default` | `attrs-on` | `attrs-off` |
| --- | --- | --- | --- |
| `smbclient setmode +hs`, read back with `smbclient allinfo` | shown (0x26 pool, 0x06 archive) | shown | not kept (0x20 pool, 0x80 archive; `setmode` reports success) |
| the same, read with the cifs pseudo-xattr `user.cifs.dosattrib` | shown for the archive file; stale (0x20) for the pool file (1) | shown | not kept |
| DOS attributes after `smbd` restart and a fresh client mount (`allinfo` and `user.cifs.dosattrib`) | kept | kept | not kept |
| readonly set through `user.cifs.dosattrib` (setxattr succeeds), read with `allinfo` | shown (0x27, 0x07) | shown | not kept (setxattr still succeeds) |
| where the server stores DOS attributes | `user.DOSATTRIB` on the branch file (through mergerfs, on the branch that holds the file) | same | nothing |
| user xattr set through the cifs mount (`user.probe`), read back through it | round trip, kept after restart | same | setxattr fails with `EOPNOTSUPP` (errno 95) |
| where the server stores a user xattr of the client | `user.probe` under the same name on the branch file | same | nothing |
| user xattr already on the branch file (`user.preset`), read through the cifs mount | shown (also by `listxattr` and the driver's manifest) | shown | not shown (getxattr: no attribute) |
| `user.DOSATTRIB` listed through the cifs mount | no (`listxattr` shows only `user.preset`, `user.probe`) | no | no |
| SHA-256 through `mount.cifs`, through `smbclient get ... -` and on the branch | equal | equal | equal |

(1) The first file the probe measured (`attrs-default/pool.txt`, created through this mount) still showed its
initial value 0x20 through `user.cifs.dosattrib` right after the `smbclient setmode`, in both runs, although
`actimeo=0`; `allinfo` showed 0x26, and after the fresh mount the pseudo-xattr showed the right value (0x27 after
the readonly change). The pseudo-xattr is therefore not a fresh view of a change made by another client; S1
reads DOS attributes with `smbclient allinfo`.

Other observations: a file that the server created on a branch has no `user.DOSATTRIB` until Samba writes one,
and `allinfo` reports it as 0x80 (normal) while a file created through the share reports 0x20 (archive); after the
readonly bit was set, the cifs mount showed both files of `attrs-default` and `attrs-on` with mode `0555` and
those of `attrs-off`, where the bit was not kept, with `0755` (inferred, not measured: the client derives the
mode from the DOS readonly bit, so a mode read through the share is not the branch's mode).

## Criteria changelog note (for T042 `S1.yaml`) and required settings (FR-013)

> Probe T039 (2026-10-17, Samba 4.23.10, kernel cifs client of 6.18.56, mergerfs 2.41.1, cache-pool share):
> DOS attributes are read with `smbclient allinfo` and changed with `smbclient setmode`; the cifs pseudo-xattr
> `user.cifs.dosattrib` shows them and can set them, but it showed a stale value after a change by `smbclient`
> until the share was mounted again, so it is not used to judge. User xattrs are set and read through the
> `mount.cifs` mount as `user.*` and are stored under the same name on the branch that holds the file; xattrs
> already on a branch file are visible through the share. Samba stores DOS attributes in `user.DOSATTRIB` on the
> branch file (through mergerfs). With `store dos attributes = no` a `setmode` reports success but the change is
> lost, and with `ea support = no` a client cannot set user xattrs (`EOPNOTSUPP`) and does not see branch
> xattrs. Both keys are `yes` by default in 4.23.10, and the measurement with both on equals the default.
> Checksums read through `mount.cifs` and through `smbclient get ... -` equal the branch.

Required settings for the share (proposed for the `required_settings` of S1, measured as required, to be set
explicitly rather than relied on as defaults): `store dos attributes = yes`, `ea support = yes`; mergerfs keeps
its default `xattr=passthrough` (not varied by this probe).
