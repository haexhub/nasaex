# Research: Phase 0 Storage Model Spikes

**Feature**: `001-phase0-storage-spikes` | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

This document resolves every open "how" of the plan. Each decision lists the decision,
the rationale and the alternatives considered. Every fact carries a source. Anything not
verified is marked **UNVERIFIED** and is collected in section 6.

Update 2026-10-15 (maintainer decision): the established tiered-cache pattern of mergerfs is the
storage baseline ("variant A") and the overlayfs stack is only the optional, non-gating
experiment X1. Section 7 documents the decision and its sources; findings F1, F4 and F6 apply to
X1 only. Where a decision below still speaks of overlay, whiteout or the former S1 stack, it has
been rewritten for variant A or marked as X1.

Research method: read-only only. No VM image was built and no VM was run. Package
versions came from `nix eval` against the candidate nixpkgs revisions. Upstream sources
were read through nix source fetches (`builtins.fetchTree` of the upstream tag) or as raw
files at an upstream tag. No installs.

Confidence tags: `[Certain]` = read in the cited source at the cited revision,
`[Likely]` = strong inference from the cited source, `[Guessing]` = estimate.

---

## 0. Findings that contradict or sharpen an assumption in the spec or an ADR

Findings F1 to F9 were made against the first plan. On 2026-10-15 the maintainer reacted to
F1, F4 and F6 by changing the storage baseline (section 7) and then edited the design, ADR-0002 and
the spec accordingly. The scenario numbers in the "Affects" column use the **current** spec; "X1"
means the optional experiment.

| ID | Finding | Affects | Kind |
| --- | --- | --- | --- |
| F1 | **Applies to experiment X1 only.** `passthrough.max-stack-depth` already defaults to `1` in mergerfs 2.41.1 and 2.42.0. Removing the option from the mount line therefore changes nothing. The kernel treats the value only as an upper bound for the FUSE stack depth; overlayfs on FUSE works with `1` and with `0`, and is refused with `2`. "The setting is required" is probably false; the real constraint is "must not be `2`". | X1 scenario 1 (formerly S1 scenario 2) | Contradiction (probable); X1 only |
| F2 | **Stays valid.** The SnapRAID manual states that changed or deleted files reduce recoverability "even if the changed or deleted files are not on the failed disk". Parity blocks are shared across disks at the same block position. The S2.2 prediction row ("files on the failed disk unchanged since the last sync are recoverable") is therefore too optimistic at block granularity. | S2 scenarios 2, 4 and 9, prediction table, SC-006 | Contradiction (probable); built into the spec |
| F3 | **Stays valid.** `snapraid sync` refuses by default when all files originally present on a disk are missing or rewritten (`-E`/`--force-empty` overrides). A sync onto an empty replacement disk is refused unless `-E` is given. S2.5 ("a sync is run before the recovery step") must say how the hazard is reached (`-E`, or a replacement that still contains some files). | S2 scenario 5, ADR-0002 context | Sharpening; built into the spec |
| F4 | **Applies to experiment X1 only.** The kernel overlayfs documentation says changes to the underlying (upper or lower) filesystems while the overlay is mounted are not allowed and give undefined behaviour (no crash, no deadlock). The mover changes the lower layer (archive writes and deletions) and the upper layer (pool copy removal, whiteout cleanup) under a mounted share. Even offline changes to the lower tree are undefined once `redirect_dir`, `index`, `metacopy` or `xino` have been used, and the rename scenario will probably need `redirect_dir`. The former S1 scenario 10 covered only the archive side. | X1 scenario 9 | Gap; X1 only |
| F5 | **Stays valid.** `nixos-rebuild switch` sets the system profile to the new generation before it activates and does not roll back by itself when activation fails. ADR-0005 says the previous generation is restored; that restoration is therefore backend behaviour that the S5 harness must supply and test. | S5 scenarios 3-5, ADR-0005 | Sharpening; built into the spec |
| F6 | **Applies to experiment X1 only.** mergerfs upstream documents the opposite stacking direction (an overlayfs mount as a branch inside a mergerfs pool). Overlayfs on top of mergerfs is not documented upstream, so nothing upstream vouches for it. This is the evidence behind the baseline decision (section 7). | ADR-0002 alternatives, X1 | Information; decisive |
| F7 | Process: PR #3 (`docs(spec): specify phase 0 storage spikes`) is already **merged** (rebase merge, head `f256c54`). PR #4 is the delivery vehicle for the branch's remaining commits: the clarification commit is rebased as `c4a5947`, followed by the plan commit; no separate PR is needed. | Delivery | Process |
| F8 | Process: `.specify/memory/constitution.md` is still the unfilled Spec Kit template (placeholders only). The Constitution Check below was done against `.spaex/constitution.md`. | Plan | Process |
| F9 | Process: the root `flake.nix` (spaex devShell) tracks `nixos-unstable` and has no committed `flake.lock`, so it is not a pin. The spikes need their own pinned flake. | Plan D1 | Process |
| F10 | mergerfs options `moveonenospc` (default `pfrd`: on `ENOSPC` the file is moved to another branch chosen by that policy and the original is unlinked) and `minfreespace` (default `4G`: create policies skip branches with less free space) work against the baseline if left at their defaults. A full pool could push a file onto an archive disk, unprotected and outside the mover. Whether no-create branch mode also excludes a branch from the `moveonenospc` policy is not documented in the pages read. The `small` profile pool (1 GiB) is below the default `minfreespace`. | S1 scenarios 2 and 9, D3 | New risk of the baseline |
| F11 | The stock mover scripts shipped by mergerfs delete their source immediately after the copy (`rsync --remove-source-files`). The project rule (verified sync before the pool copy is removed) is stricter, so the documented pattern cannot be used as is. | S3, ADR-0002 alternatives | Sharpening |
| F12 | The snapshot-supported recovery depends on external tooling: `snapraid-btrfs` is not packaged in nixpkgs `a033e66` (the attribute does not exist; `snapper` 0.13.1 is), and the native `snapshot` option exists only in the SnapRAID 15.0 release candidate, while the pin carries 14.4. The recovery procedure of `snapraid-btrfs fix` uses the live filesystem for the disk being fixed and the retained snapshot for the others (section 7.3). | S2 scenario 9, plan open item O15 | New dependency |

---

## 1. Pinned environment and tool versions

Candidate pin chosen in D1: nixpkgs branch `nixos-26.05` at revision
`a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf` (commit date 2026-10-13).
Source: `nix flake metadata github:NixOS/nixpkgs/nixos-26.05 --json` and
`git ls-remote`-equivalent `gh api repos/NixOS/nixpkgs/git/ref/heads/nixos-26.05`
`[Certain]`. The revision is a candidate: the committed `flake.lock` of the implementation
is the actual pin, and the first result record names it (FR-005).

Versions obtained by `nix eval --impure --raw --expr` with an expression that reads
`(builtins.getFlake "github:NixOS/nixpkgs/<rev>").legacyPackages.x86_64-linux.<attr>.version`
(`[Certain]`, evaluated 2026-10-15):

| Component | nixos-26.05 `a033e66` (chosen) | nixos-unstable `55bfb4d` (2026-10-14, compared) |
| --- | --- | --- |
| mergerfs | **2.41.1** | 2.42.0 |
| snapraid | **14.4** | 14.10 |
| btrfs-progs | **6.19.1** | 7.1 |
| samba | **4.23.10** | 4.23.10 |
| kernel, default `linuxPackages` | **6.18.56** | 6.18.56 |
| kernel, `linuxPackages_latest` | 7.2.10 | 7.2.10 |
| kernel, `linuxPackages_6_12` | 6.12.112 | 6.12.112 |
| xfsprogs | 6.19.0 | 7.1.1 |
| e2fsprogs | 1.47.4 | 1.47.4 |
| fuse3 (userspace lib, not used by mergerfs) | 3.17.4 | 3.18.2 |
| qemu_kvm | 10.2.4 | 11.1.2 |
| nix | 2.34.8 | 2.34.9 |
| util-linux | 2.42.3 | 2.42.3 |
| cifs-utils | 7.5 | 7.5 |

Other facts at the chosen revision:

- `nixos-26.05` and `nixos-25.11` (`b6018f87da91d19d0ab4cf979885689b469cdd41`, 2026-06-30)
  both exist as branches. `[Certain]` (`nix flake metadata`).
- The nixpkgs mergerfs package builds mergerfs from `github.com/trapexit/mergerfs` at tag
  `2.41.1` and installs `mount.fuse.mergerfs` and `mount.mergerfs` helper links, so
  `fileSystems.<m>.fsType = "fuse.mergerfs"` has a mount helper. `[Certain]`
  (`pkgs/by-name/me/mergerfs/package.nix`, rev `a033e66`).
- nixpkgs ships `services.snapraid` (data disks, parity files, content files, exclude,
  sync/scrub timers). No NixOS module for mergerfs exists in the tree
  (`grep -rl "mergerfs" nixos` finds only the module list and release notes). `[Certain]`
- `snapper` 0.13.1 is packaged at `a033e66`; `snapraid-btrfs` and `snapraid-btrfs-runner` are
  not (`nix eval` of `legacyPackages.x86_64-linux ? snapraid-btrfs` is false). The snapshot
  tooling therefore has to be fetched by pinned revision (flake input with `narHash`) or
  re-implemented as scaffolding (plan open item O15). `[Certain]` (evaluated 2026-10-15)
- The default kernel 6.18.56 is >= 6.9 (FUSE passthrough, see 3.1; only relevant to X1). `[Certain]`
- `linux` 7.0 was removed from nixpkgs as end-of-life at both revisions (the evaluation of
  the alias set fails with "linux 7.0 was removed because it has reached its end of life
  upstream"). `[Certain]`

Immutable upstream references used below (constitution: `repository + full commit SHA +
path`; annotated tags are named with the tag object SHA):

| Upstream | Ref | SHA | Resolved with |
| --- | --- | --- | --- |
| trapexit/mergerfs | tag `2.41.1` | `960f5fdbba5e053eb4a328b409cda0d9dd2e0d48` (commit) | `gh api repos/trapexit/mergerfs/git/ref/tags/2.41.1` |
| trapexit/mergerfs | tag `2.42.0` | `ce3737ff3314ee0eb5edc38720b1a3f1fcb0e153` (commit) | same, tag `2.42.0` |
| amadvance/snapraid | tag `v14.4` | `addfc93cb0d0e05aaeff2ee733fc1020b136e06f` (tag object) | `gh api repos/amadvance/snapraid/git/ref/tags/v14.4` |
| torvalds/linux | tag `v6.18` | `f7b88edb52c8dd01b7e576390d658ae6eef0e134` (tag object) | `gh api repos/torvalds/linux/git/ref/tags/v6.18` |
| NixOS/nixpkgs | `nixos-26.05` | `a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf` (commit) | `gh api repos/NixOS/nixpkgs/git/ref/heads/nixos-26.05` |

The pinned kernel is 6.18.56, a stable point release of the analysed `v6.18` sources;
that no stable patch changed the analysed code paths is **UNVERIFIED**.

---

## 2. Decisions

### D1 - nixpkgs pin

- **Decision**: One dedicated flake for the spikes (`spikes/phase0/flake.nix`) with a
  committed `flake.lock`. The input is `github:NixOS/nixpkgs/<full commit SHA>` taken from
  branch `nixos-26.05` (candidate `a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf`). The lock
  entry (`narHash`) is the reproducibility anchor. The pin is bumped only through a
  reviewed PR that also states which result records it invalidates. The whole of Phase 0
  runs on one pin.
- **Rationale**: ADR-0001 chose NixOS for a data-holding appliance and rejected a rolling
  distribution for that reason; a stable release branch fits. It provides everything the
  spikes name (mergerfs 2.41.1 documents the tiered-cache pattern and branch modes, btrfs
  6.19.1 has snapshots, snapraid 14.4 documents `fix`/`sync`; for X1 the kernel has FUSE
  passthrough and overlayfs). Pinning by SHA plus lock means the
  branch can move without changing the environment. A separate flake leaves the
  spaex-delivered root `flake.nix` untouched (finding F9) and gives the spikes their own
  lock.
- **Alternatives**: (a) `nixos-unstable`: newer mergerfs/snapraid/btrfs-progs, but churn
  inside a phase and it is what the root devShell flake already drifts on. (b) `nixos-25.11`:
  older, no benefit. (c) Reuse the root flake: it is generated by spaex, unpinned, and
  mixes devShell tooling with the system under test. (d) Let the lock float with
  `nix flake update` per run: breaks FR-005.
- **Status**: `[Certain]` for versions; the choice of branch is a recommendation (open
  item O7).

### D2 - VM test harness

- **Decision**: `pkgs.testers.runNixOSTest` (the `nixos/lib/testing` module system) with
  Python test scripts, one test per spike (`s1` to `s5`), defined in `spikes/phase0/vm-tests/`.
  The tests are built (cached) as nix derivations but are **executed outside the nix
  sandbox** by running the test driver (`config.driver`, binary `nixos-test-driver`) from
  the `spikectl` runner. `forceAccel` stays on, so a missing `/dev/kvm` is a hard error,
  never a silent TCG fallback. Each run uses a fresh machine state directory (the driver
  resets machine state unless `--keep-machine-state` is passed). Results come from a
  scenario recorder in the test script plus `--junit-xml`.
- **Rationale**: (1) A sandbox build is cached by input hash: a second `nix build` of the
  same test does nothing, which makes "3 consecutive clean runs" (SC-004) impossible without
  tricks such as an impure run id. Running the driver directly always executes. (2) A
  failing nix build produces no output; a spike FAIL is a legitimate result with evidence
  that must be kept. (3) Outside the sandbox the state directory (which holds multi-GiB
  qcow2 files) can live on a disk path of our choosing, not in the nix build directory.
  (4) `system-features = kvm nixos-test` is not needed on builders, only a working
  `/dev/kvm` for the user running the suite. (5) The driver offers `--output_directory`,
  `--junit-xml` and `--keep-machine-state`, and the scripts can `import` our own Python
  library.
- **Alternatives**: (a) `nix build .#checks.<system>.sN`: sandboxed and cached, but see
  the three reasons above; kept for the cheap non-VM checks only. (b) `--rebuild`: fails on
  any output difference and still has no evidence on failure. (c) A bespoke QEMU/shell
  harness: reimplements machine control, serial console handling and networking that the
  test driver already provides. (d) `nixos-container`/systemd-nspawn: no real block devices,
  no power-off, no device hot-unplug.
- **Sources**: `nixos/lib/testing/run.nix` (the result exposes `passthru.config`, so
  `config.driver` is reachable; `requiredSystemFeatures` default `kvm`/`nixos-test`),
  `nixos/lib/test-driver/src/test_driver/__init__.py` (`--output_directory`, `--junit-xml`,
  `--keep-machine-state`; "Machine state will be reset" by default),
  `nixos/lib/testing/driver.nix` (`qemu.forceAccel`, `globalTimeout` default 3600 s),
  all at nixpkgs `a033e66`. `[Certain]`
- **Note on `subtest`**: the driver's `subtest` re-raises after logging, so one failed
  scenario would abort the script. Scenario isolation (every scenario runs, outcomes are
  recorded, a scenario that cannot run counts as failed) is therefore implemented by the
  project's own scenario recorder, not by `subtest`. `[Certain]`
  (`driver.py` `subtest`).
- **Global timeout**: the default is 1 h; every spike sets `globalTimeout` explicitly
  from its budget in the plan.

### D3 - Virtual disk layout and VM topology

- **Decision**: Two topology profiles for S1 to S4 (plus the `x1` profile for the optional
  experiment, with the same disks as `large`), all built with `virtualisation.emptyDiskImages`
  and a stable disk identity per disk (`deviceExtraOpts.serial`, giving
  `/dev/disk/by-id/virtio-<serial>`, so removing a disk never renumbers the others).

  | Disk (serial) | Role | `large` profile (S1, X1) | `small` profile (S2-S4) | Filesystem |
  | --- | --- | --- | --- | --- |
  | `pool0`, `pool1` | btrfs RAID1 pool | 8 GiB each | 1 GiB each | btrfs, data and metadata RAID1 |
  | `arch0` | archive data | 6 GiB | 640 MiB | btrfs (own filesystem; data subvolume plus a separate content subvolume) |
  | `arch1` | archive data | 10 GiB | 1 GiB | btrfs (same layout) |
  | `arch2` | archive data | 4 GiB | 512 MiB | btrfs (same layout) |
  | `parity0` | single parity | 12 GiB | 1280 MiB | ext4 (parity file) |

  All sizes are initial proposals; they are sparse qcow2 files, so the host cost is the bytes
  actually written. The archive disks differ in size (FR-009). The baseline needs snapshots of
  the data disks, so all of them are btrfs and the first plan's mix of XFS, XFS and ext4 is gone.
  The `small` archive sizes were raised against the first plan because btrfs and the retained
  snapshots (deleted or rewritten data stays allocated until the snapshot is cleaned up,
  snapraid-btrfs FAQ) need more headroom than XFS or ext4 on tiny devices (`[Guessing]`, to be
  settled in shakedown). The parity disk is at least as large as the largest data disk. Its
  filesystem is ext4 because the `snapraid-btrfs` FAQ recommends it for parity files
  (negligible metadata overhead, nothing to snapshot). The `small` pool (1 GiB) is below
  mergerfs' default `minfreespace` of 4G, so every profile sets `minfreespace` explicitly
  (finding F10). A `server` VM carries all disks; for S1 a second VM `client` on the test vlan
  mounts the share over SMB. S5 uses a separate single-machine topology (D10). Replacement
  disks for S2/S4 are extra empty images of the same or a different size, attached from the
  start and left unused until the scenario needs them.
- **Rationale**: Disk size is dictated by the largest scenario: S1 scenario 8 needs a file larger
  than 4 GiB written new to the pool (RAID1, so roughly the file size on each pool device plus
  metadata) and another above 4 GiB on an archive disk that is modified in place. Unlike the
  overlay variant there is no copy-up of the large file. The other spikes use small data and the
  `small` profile to keep boots and syncs fast. Different sizes exercise the "mixed disks" claim
  of ADR-0002. SnapRAID parity is a **file** on a filesystem (parity files are configured as
  `parity /path/snapraid.parity`), not a raw device. `[Certain]` (SnapRAID manual, section
  "parity"; `services.snapraid.parityFiles`). SnapRAID content files that live on a data disk
  must sit in a dedicated subvolume that is not snapshotted. `[Certain]` (snapraid-btrfs README,
  see 7.3). Superseded for S2: no content copy lives on an archive data disk, because one there
  deadlocks a full disk with retained snapshots; the copies live on the system and the parity
  disk (measured by probe T057, spec Clarifications 2026-10-20, S2 SnapRAID probe).
- **Alternatives**: (a) One profile for everything: S2-S4 would pay large-disk costs per boot and
  per sync. (b) Real loop devices inside the VM instead of virtio disks: no host-side device
  failure. (c) Raw instead of qcow2 images: full allocation, no benefit. (d) Keep XFS or ext4
  archive disks: impossible, the snapshot workflow needs btrfs on the data disks.
- **Host disk need**: the `large` profile can write more than 10 GiB per run (large-file tests and
  copies); the reference runner needs tens of GiB free in the state directory, and the state
  directory must not be a tmpfs. `[Guessing]` for the size, hence the "reference runner" sizing in
  plan open item O1.
- **Sources**: `virtualisation.emptyDiskImages` (size in MiB, per-disk `driveConfig`) and
  `virtualisation.qemu.drives` in `nixos/modules/virtualisation/qemu-vm.nix`;
  `nixos/modules/services/backup/snapraid.nix`; all at `a033e66`. `[Certain]`

### D4 - SMB client VM

- **Decision**: a second NixOS VM `client` on the test vlan with `cifs-utils` and the
  Samba client tools. Bulk and tree operations (list, read, checksum, copy, rename, delete,
  xattr) go through a `mount.cifs` mount; SMB-specific attributes (DOS attributes,
  alternate behaviours that a kernel mount does not expose) are driven by `smbclient`.
  The server shares the cache pool mount point with a minimal `smb.conf` (one share, one
  test user, `store dos attributes` and extended attributes enabled, nothing else).
- **Rationale**: FR-009/FR-012 require a separate SMB client; two different client
  implementations avoid verdicts that depend on the quirks of one. Production SMB tuning
  is out of scope, so the configuration is only what proves access.
- **Alternatives**: (a) `smbclient` only: weak for rename/xattr/large-file behaviour that real
  clients exercise through a mounted filesystem. (b) `mount.cifs` only: DOS attributes are
  hard to set and read. (c) A Windows/macOS client: not reproducible in a NixOS VM test.
- **Prior art**: `nixos/tests/samba.nix` runs a server and a client VM with `mount.cifs`
  (nixpkgs `a033e66`). `[Certain]`. The exact smb.conf keys and which client commands expose
  which attribute are **UNVERIFIED** (open item O12).

### D5 - Fault injection

All injection is driven from the host side by the Python test script (the test driver
process). Named injection points are defined in `contracts/fault-points.md`; the mover step
hooks are in `contracts/mover-steps.md`.

- **QEMU process termination**: `machine.crash()` of the test driver. It sends `quit` to the QEMU
  monitor and waits for the process to exit; a following `machine.start()` (or the
  driver's implicit start) boots the same machine state, so disks persist.
  `[Certain]` (`machine/__init__.py`, `crash()`; nixpkgs tests `zfs.nix` use
  `crash()` followed by a start). Effect: guest RAM, including the guest page cache,
  is lost. Writes already submitted to the virtual disk may remain visible because the
  qcow2 file lives in the host page cache. This does not emulate host power loss or loss
  of un-flushed writes in host or device caches. Record that limitation in every result
  record. `[Likely]`
- **Process kill**: `kill -9` of the named process inside the guest through the driver
  shell (`machine.succeed("kill -9 ...")`), triggered while the mover is blocked at a hook
  (see D7). A mid-sync kill for S2.4 additionally uses SnapRAID's own progress log (see 3.4).
- **Device failure**: two QEMU monitor flavours, selected per scenario in the criteria:
  - `unplug`: `device_del <id>` (the disk disappears like a pulled drive). Prior art for
    `device_del` in the driver: `nixos/tests/udisks2.nix` (USB stick). `[Certain]` that the
    command is used there; that it works for `virtio-blk-pci` disks in this machine
    type, and that btrfs and the guest cope as assumed, is **UNVERIFIED**.
  - `ioerror`: `drive_del <drive>` (the host backend is removed; guest I/O fails but the
    device node stays). **UNVERIFIED** for the pinned QEMU 10.2.4.
  - Both are issued with `machine.send_monitor_command(...)` or the QMP session of the
    driver (`qmp_client.send`). `[Certain]` that both interfaces exist
    (`machine/__init__.py`, `machine/qmp.py`).
  - Failure that must survive a restart (S4 scenario 7): the harness swaps the backing qcow2
    of the failed disk for an empty image before the next start (the same step as
    "replacement disk"), because the start command of a machine is fixed and would
    re-attach the old image. Alternative to evaluate in the probe: a `blkdebug` backing
    configuration toggled by the host between starts.
- **Targeted write/read faults** (failed parity write, S3 scenario 6): by failing the
  parity disk with `unplug`/`ioerror` at the hook, or, if a single-block fault is needed, a
  `blkdebug:` backing definition. `blkdebug` is **UNVERIFIED** in this harness.
- **Injected data faults** (corrupted archive copy, S3 scenario 6): the hook action
  overwrites bytes of the archive copy through the guest shell at the named point.
- **Out-of-space** (S1 scenario 9, S3 scenario 9): a pre-created filler file that leaves a
  defined number of free bytes; deterministic, no special device. For S1 scenario 9 the
  filler goes on the pool branch with `minfreespace` set below the remaining space, so that
  the policy still selects the pool and the write itself hits `ENOSPC` (finding F10).
- **Why not in-guest `dm-flakey`/`dm-error` as primary**: spec assumption says failures are
  injected from the host side; host-side faults also keep the guest ignorant of the fault.
  `dm-error` stays available as an evaluation fallback if `drive_del` is unusable.
- **Determinism (FR-010)**: every injection happens when the guest process is blocked at a
  named hook or when a scenario step has completed, never after a sleep.
- **Alternatives**: timing-based kills (rejected by FR-010); `strace`/`ptrace` based
  syscall-level injection (heavy, changes timing); fuzzing with random kill points (not
  deterministic, not enumerable for SC-005).

### D6 - Mover step hooks

- **Decision**: the mover under test is a throwaway **reference mover** (a shell script in
  `spikes/phase0/guest/`) that implements the step list of `contracts/mover-steps.md`.
  Between and inside steps it calls `spike_hook <point>`. A hook is a no-op unless the host
  has armed that point; an armed hook writes a `reached` marker and then blocks until
  released. The host test script waits for the marker (`wait_for_file`), performs the kill
  (process kill, QEMU process termination, or device failure), and either releases the hook
  (no kill) or lets the kill take the blocked process down. The hook itself never calls `sync`,
  so a QEMU termination sees exactly the on-disk effects of the steps before the point, subject
  to the host-page-cache limitation described above.
- **Rationale**: the spec's assumption is that the step list and the recovery procedure are
  the artifacts the product will later implement, so the step list is a contract and the
  script is disposable. A blocking cooperative hook is deterministic (the process cannot
  advance) and needs no tracing. Each durable effect of a step is its own named point
  (temporary file created, data fsynced, renamed, directory fsynced), which covers "kill
  lands between two individually crash-safe effects".
- **Alternatives**: `ptrace`/`gdb` breakpoints (heavy, perturbs timing, not portable to a
  future Go mover), `LD_PRELOAD` fault injection (still cooperative but tied to libc
  calls), kill after a fixed delay (forbidden by FR-010).
- **Independence caveat**: the hook is cooperative, so the *oracle* must not read anything
  the mover wrote about itself (see D7).

### D7 - Independent oracle

The oracle is host-side Python (`spikes/phase0/testlib`) that determines expected state
**without** reading mover output, mover logs or SnapRAID's own verdicts as truth.

- **Expected content (S1, S3)**: a deterministic fixture generator turns a seed and a
  fixture description (paths, sizes, modes, xattrs, which branch each file starts on) into
  bytes. The host computes the expected tree manifest (names, sizes, mtimes, modes, xattrs,
  SHA-256) directly from the description. The guest runs the same generator to populate the
  branches. Expected values therefore never come from guest storage. Large files (S1) are
  streamed from the generator, not stored on the host.
- **Observed state**: read through three routes that do not involve the mover: the share
  as a client sees it (SMB client VM, or the local cache pool mount for S3), the raw branches
  (the pool branch directory and each archive branch directory read directly, never through
  the mover), and a tree hash of each branch. The oracle compares observed
  manifests with expected manifests and reports differences; it never "repairs" them.
- **Invariants (FR-019)**: (a) every live file reads back complete through the share with
  the expected hash; (b) a pool copy that is missing implies the archive copy exists with
  the expected hash **and** is parity-covered; (c) no file that no client deleted disappears
  and no client-deleted file reappears; (d) no file is visible with partial content; (e) after
  a single device failure no live file is lost.
- **Parity coverage without trusting the mover or SnapRAID's status**: coverage is proven
  operationally, not by asking SnapRAID whether it is in sync. At each crash point the host
  copies the disk images of the powered-off machine into a second machine state (the
  "drill machine"), fails one device there (a pool device, or the archive disk that holds the
  file), runs the documented S2 recovery sequence and compares every live file with the
  expected hash. If the file comes back bit-exact, the mirror or the parity really covered
  it. This is also scenario S3.4. The drill machine reuses the same topology and start
  script; feasibility of copying a crashed machine's state directory into a new machine is
  **UNVERIFIED** (probe in the first implementation tasks).
- **S2 predictions**: by construction. The fixture lays files out so that SnapRAID's block
  positions per disk are determined by the documented allocation (the oracle model, see 3.4),
  and the expected set of recoverable and unrecoverable files is computed at **block**
  granularity from the pre-failure history (finding F2). The snapshot variant (S2 scenario 9)
  reuses the model with the state of the retained snapshots as the reference for the surviving
  disks and is compared with the plain result of scenario 2. SnapRAID's own `status:unrecoverable`
  log tags are collected as evidence and compared, but the verdict compares the predicted set
  with the *actual* result: a file is recovered if and only if its restored bytes equal the
  expected hash. The block-allocation model of SnapRAID was measured by probe T057: removals
  happen before insertions, and new or rewritten files are assigned by FIEMAP physical start
  using first-fit positions per disk (plan item O2).
- **S4**: the state table is data in the criteria file; the oracle drives each cell and
  checks the result through the lock and status mechanism of D9 and the archive/parity tree
  hashes taken before and after the refused action.
- **Oracle validation (negative controls)**: an oracle that cannot fail proves nothing. The
  suite includes a handful of known-bad variants of the reference mover (for example: the stock
  pattern that removes the pool copy right after the copy, as `rsync --remove-source-files`
  does, which is the mandatory S3 scenario 5 control; removes the pool copy before the
  sync; skips the checksum comparison; deletes a newer pool version) and a corrupted-parity
  variant for S2.
  Each variant must be flagged as FAIL by the corresponding scenario, otherwise the harness
  is wrong. Pure-Python unit tests of the oracle and the manifest comparison run without a VM.
- **Alternatives**: asking `snapraid status`/`diff` for coverage (trusting the tool under
  test); reading the mover's journal (trusting the mover); an independent re-implementation
  of SnapRAID parity from its content file (needs the binary content file format, high
  effort, can be wrong in the same direction as the model).

### D8 - Freezing criteria, versions, result records

- **Decision**:
  - **Where**: decision artifacts live in `docs/phase0/`, outside the throwaway scaffolding
    in `spikes/`, because ADRs will reference them after the scaffolding is gone.
    `docs/phase0/criteria/S1.yaml` ... `S5.yaml` (and `X1.yaml` if the experiment is run),
    `docs/phase0/criteria/frozen.lock`,
    `docs/phase0/results/<spike>/<record-id>/{record.json,evidence.tar.xz}`. X1 uses the same
    mechanics under the id `X1`; it is the only spike that may have no criteria file and no record
    without any check failing (it is optional and non-gating).
  - **Criteria file**: YAML (readable, comments allowed); schema `contracts/criteria.schema.json`.
    Each scenario has a stable id (`S3.2`), precondition, action, observable and an automated
    check name; matrix scenarios declare their case generator (for example the enumerated mover
    crash points). Each S1 to S5 file carries `criteria_version`, `status` (`draft` or `frozen`)
    and the spike's fallback (FR-031); X1 has no fallback.
  - **Freeze**: `spikectl freeze <spike>` computes the SHA-256 of the file bytes and
    **appends** `<file> <version> <sha256>` to `frozen.lock`. A frozen version file is
    never edited. `spikectl verify` (run in CI) fails if a locked file's hash differs from
    its lock entry, if `frozen.lock` lost or changed a line (append-only, checked against
    the base branch), or if a result record names a (version, hash) pair that is not locked.
  - **Criteria first** (SC-002): `spikectl verify` checks with git history that the commit
    that introduced the lock entry for the record's (version, hash) is an ancestor of the
    commit that introduced the record. It needs full history in CI. A rebase merge keeps
    history linear, so the property survives the merge strategy required by the
    constitution.
  - **Amendment** (FR-004): a new file `S<n>.v<k+1>.yaml` (the first file `S<n>.yaml` is version 1, the
    next is `S<n>.v2.yaml`) with `amends`, `reason` and `date`; its own lock entry. Records of older
    versions are never edited or deleted; the *verdict* of a spike is derived by
    `spikectl verdict` from the latest record that names the **current** criteria version, so
    older verdicts are void by construction (SC-002, last clause).
  - **Record mode**: `spikectl run --mode record` refuses to run unless the spike's current
    criteria version is locked and the git tree is clean (no uncommitted spike or criteria
    change). `--mode shakedown` runs anything, writes only to a scratch directory and never
    produces a record.
  - **Result record**: JSON, schema `contracts/result-record.schema.json`, one per run of
    one spike. Fields follow the spec (spike, criteria version, environment revision, date,
    per-scenario outcome, evidence reference, verdict, fallback) plus pins, topology profile,
    fault-point coverage counts (SC-005), residual-risk statements and `supersedes` for
    re-runs. A failed run is a record like any other and is never deleted.
  - **Naming**: record id `<spike>-<UTC timestamp, basic format>-v<criteria version>`, for
    example `S3-20261022T101500Z-v1`; the directory has the same name.
  - **Evidence kept** (per record, one `evidence.tar.xz`, target <= 2 MiB): the
    junit XML, the trimmed driver log, the per-scenario results with the oracle's expected
    and observed manifests (hashes, not file contents), SnapRAID `-l` logs and `fix.log`, the
    final `status.json` snapshots, `dmesg`/journal excerpts of failing scenarios, the
    environment descriptor (tool versions, `flake.lock` hash, kernel config checks), the
    written recovery procedure of S2.7 as rendered by the test. Disk images, full serial
    logs of passing scenarios and file contents are **not** kept. An evidence sanitiser
    removes host user names, host names and absolute host paths before packing
    (constitution: no local absolute paths; no personal data). Guest-internal paths such as
    `/mnt/archive/...` are fine.
  - **Reproducibility record** (SC-004): `spikectl run --repeat 3` writes one record of kind
    `repro` per spike that lists the three run ids and the per-scenario verdict matrix; any
    scenario whose outcome differs between the three runs is `flaky`, which counts as FAIL.
- **Rationale**: it makes "criteria precede results", "amendment voids old verdict" and "ADR
  status equals verdict" mechanically checkable instead of review-only. Append-only lock plus
  git ancestry needs no extra service. A single record directory per run keeps the evidence
  next to the verdict.
- **Alternatives**: git tags per frozen version (tags do not survive a rebase merge); a
  database or external artifact store (not "kept in version control", spec); JSON criteria
  (no comments, harder to review); Markdown criteria with a separate machine file (two
  sources of truth). Git LFS for evidence (extra tooling; revisit if evidence size grows,
  open item O4).

### D9 - ADR gate: who edits what, when

- **Decision**: the suite and `spikectl` never edit an ADR. The sequence is:
  1. A run in `record` mode writes a record directory into the working tree.
  2. A maintainer (or an agent acting on a maintainer's request) commits the record in a
     PR, type `docs(phase0)`, **after** the criteria commit.
  3. `spikectl verdict` prints the gate matrix (spike verdicts and the derived ADR
     eligibility). It is advisory.
  4. A **separate, reviewed docs-only PR** changes the ADR `Status:` line and references the
     record paths. ADR-0002 may say `accepted` only with PASS for S1, S2, S3 and S4;
     ADR-0005 only with PASS for S5. On a gating S1 to S5 FAIL record, the same kind of PR
     adopts the fallback named in the criteria, updates the ADR text and the design document
     (FR-007) and the ADR stays `proposed` until it does.
  5. X1 is never an input of the gate: `spikectl verdict` lists it separately as "non-gating", and
     `spikectl verify` rejects an ADR that cites an X1 record as a condition, and does not
     require X1 records.
  6. `spikectl verify` runs in CI on every PR that touches `docs/adr/` or
     `docs/phase0/`: if ADR-0002 or ADR-0005 claims `accepted`, the required PASS records
     for the **current** criteria versions must exist and be referenced; an S1 to S5 FAIL record
     without a recorded fallback decision is an error (FR-006, FR-007, SC-003). X1 FAIL records
     intentionally have no fallback decision.
- **Rationale**: the constitution requires a PR for everything on `main` and review of
  decisions; keeping the ADR edit as a separate human act preserves the spec's "ADR gate"
  as a decision, while the check removes the possibility of an unnoticed inconsistency.
- **Alternatives**: auto-updating ADR status from CI (violates review-gated change, the
  verdict model says the fallback adoption is a decision); no check (relies on review alone).

### D10 - S5 harness (declarative rebuild)

- **Decision**: a single-machine test that follows ADR-0004 as far as a VM can: a boot
  loader (`virtualisation.useBootLoader = true`, `useEFIBoot = true`), a tmpfs root, a
  persistent `/nix` and a persistent state area on a separate virtual disk
  (`virtualisation.useDefaultFilesystems = false` with explicit
  `virtualisation.fileSystems`, `mountHostNixStore = false` so the store is local and
  survives reboot). The configuration repository is a git repository on the persistent
  area with a flake whose module reads one JSON file with `builtins.fromJSON`. A throwaway
  `apply` script (scaffolding, `spikes/phase0/guest/apply-config.sh`) performs the sequence
  the backend will use: write JSON, `git add` + `git commit`, `nixos-rebuild switch --flake`,
  and, for the failure scenarios, the restoration of the previous generation. Everything is
  offline: the nixpkgs input and the build dependencies of the test configurations are put
  in the VM store at image build time (`system.includeBuildDependencies`,
  `virtualisation.additionalPaths`, `nix.settings.substituters = [ ]`).
- **Why the restoration is part of the harness** (finding F5): `nixos-rebuild-ng` sets the
  profile first and then calls `switch-to-configuration`; there is no automatic rollback.
  `[Certain]` (`pkgs/by-name/ni/nixos-rebuild-ng/src/nixos_rebuild/services.py`,
  `nix.py`, nixpkgs `a033e66`). The S5 criteria therefore test the backend-equivalent
  procedure, not `nixos-rebuild` alone.
- **Active revision vs repository head** (scenario 6): `system.configurationRevision` is set
  from the flake revision (`nixos/modules/misc/version.nix` provides the option;
  `nixos-version --configuration-revision` reports it), compared with `git rev-parse HEAD`.
  `[Certain]` for the option; the exact reporting command is **UNVERIFIED**.
- **Pure evaluation** (scenario 7): flake evaluation sees only tracked files of the flake
  directory; a config file outside it is not readable. `[Likely]` (standard flake behaviour);
  verified by the scenario itself. An untracked new file is invisible to evaluation, which
  is exactly the "uncommitted change at apply time" edge case.
- **Measured durations**: wall-clock of the apply script inside the VM, per change class
  (no-op, add share, change storage parameter), >= 5 warm runs (median, worst) and one cold
  run (first apply after boot with empty eval cache and the new outputs not yet in the
  store). Environment (cores, memory, host CPU model class, pin) goes into the record. Because
  there is no network, the numbers exclude substitution from a binary cache; they are
  lower bounds for typical module changes and are recorded as such (residual risk). The
  thresholds of the spec (5 min / 15 min) are applied unchanged.
- **Feasibility risk**: tmpfs root plus bootloader plus local writable store plus offline
  rebuild is the least-proven part of the plan. Prior art for pieces exists in nixpkgs
  tests (`nixos-rebuild-specialisations.nix`, `nixos-rebuild-install-bootloader.nix`,
  `systemd-initrd-btrfs-raid.nix` for `useBootLoader`/`useEFIBoot`). **UNVERIFIED** as a
  whole: the first S5 implementation task is a feasibility probe. Fallback if the ADR-0004
  layout cannot be reproduced in a VM: record exactly which part (tmpfs root, persistent
  `/nix`) was reproduced, and run scenario 8 with the closest layout; the spec already
  allows "where the VM test can reproduce it".
- **Alternatives**: build the configurations on the host and `nixos-rebuild --store-path`
  inside the VM (skips evaluation cost, which is part of what S5 measures); a container
  instead of a VM (no real boot, no reboot test).

### D11 - Degraded-state lock and status mechanism (scaffolding for S4)

- **Decision**: the lock is enforced by the kernel where possible, with a thin status layer:
  when the archive is degraded, the archive data filesystems and the parity filesystem
  (and the location of the content files) are remounted read-only, so no route
  (scheduled, manual, automatic) can change archive or parity; a small throwaway
  `lockd` script derives the state (healthy, pool degraded, archive degraded, both degraded)
  from device state, writes `status.json` (machine readable, no UI) and a clear refusal
  message through wrappers and `ConditionPathExists=` guards on the timers. The both-degraded
  state additionally flips the share to read-only. With the snapshot workflow, creating,
  deleting and cleaning snapshots are writes to the data disks and are locked with them. The
  cache pool stays writable only through its pool branch; changes to archived files then fail
  with the filesystem's read-only error, which is the "refused with a defined error" the spec
  asks for. mergerfs' per-branch `RO` mode (`branches.md`: excluded from create and action
  policies) is an alternative or additional lock to evaluate; its effect on reads of archived
  files is UNVERIFIED.
- **Rationale**: S4 requires "refused by every route and archive/parity unchanged (verified
  by checksum)" (FR-025). A wrapper alone can be bypassed by calling the real binary; a
  read-only filesystem cannot be bypassed and makes the checksum claim true by
  construction. The wrapper layer only provides the defined error text and status.
- **Alternatives**: wrapper plus systemd conditions only (cooperative, bypassable);
  `chattr +i` on parity files (protects parity but not the archive data or the content
  file); mount namespaces per service (more machinery than the spike needs).
- This is the design under test, so the mechanism is deliberately simple and replaceable;
  a different mechanism chosen after a FAIL is an S4 re-run (FR-031).
- **Pool side**: btrfs degraded behaviour is taken from the documentation in 3.5; a degraded
  mount needs `-o degraded`, including after a reboot (S4 scenario 7).

### D12 - Repository layout and flake outputs

See plan, "Project Structure". Decision: all scaffolding under `spikes/phase0/`, a flake
of its own; nothing outside `spikes/` and `docs/` may reference it (checked by
`spikectl verify`); product code (later phases) never lives under `spikes/`. Rationale: the
spec's out-of-scope clause says throwaway scaffolding is not carried into the product, and
SC-010 requires that Phase 0 adds no product code. Alternative: a `tests/` tree next to
future product directories (would blur the line); a branch-only scaffold (loses
reviewability and CI).

Flake outputs (system `x86_64-linux`):

- `apps.suite`: the one-command entry point (D13).
- `apps.s1` ... `apps.s5`: one spike; `apps.x1`: the optional overlayfs experiment (own node
  profile `x1`, not part of `suite` unless `--include-x1` is given); `apps.controls`: negative
  controls only; `apps.spikectl`: criteria freeze, record verification, verdict matrix.
- `packages.driver-s1` ... `driver-s5`, `packages.driver-x1`, `packages.driver-interactive-s<N>`:
  the test drivers (cached build products; `interactive` for debugging).
- `checks.criteria`, `checks.records`, `checks.oracle-unit`, `checks.formatting`: cheap checks
  that need no KVM and run in `nix flake check` and in the static CI job.
- `devShells.default`: nix, Python with `pytest`, `pyyaml`, `jsonschema`, QEMU tools for
  debugging.

### D13 - One-command entry point, concurrency, reproducibility

- **Decision**: `nix run ./spikes/phase0#suite -- --mode record [--repeat 3] [--jobs N]`.
  `spikectl run` builds the five drivers (the X1 driver only with `--include-x1`), checks
  `/dev/kvm`, then runs spikes concurrently
  according to a small dependency-free schedule: S5 runs in parallel with S1; S2 then S4;
  S3 runs sharded (`SPIKE_SHARD=i/N`, each shard is one driver process over a share of the
  crash-point matrix) after S1/S2 have a result, because S3 needs the S1 finding on view
  consistency (scenario 10) and the S2 recovery sequence. It enforces the global budget (kills runs that exceed it, which
  counts as failed scenarios), writes records (record mode) or scratch output (shakedown),
  and prints the verdict matrix.
- **Reproducibility (SC-004)**: `--repeat 3` runs the whole suite three times in sequence,
  each from a clean machine state directory, then compares per-scenario outcomes. The CI
  version chains three jobs on separate clean runners (D14).
- **Prerequisite on the host**: Nix with the `nix-command` and `flakes` features (the local
  Nix configuration of the maintainer machine has both; a clean machine must enable them),
  `/dev/kvm` readable and writable for the user, and a state directory with enough space.
- **Alternatives**: `nix flake check` as the entry point (builds everything, stops at the
  first failure without `--keep-going`, has no concept of record mode); a Makefile or shell
  script wrapper (adds a layer above nix for no benefit).

### D14 - CI on a KVM runner and the time budget

- **Decision**: a GitHub Actions workflow (created in the implementation phase) with three
  jobs:
  1. `static` (no KVM, every PR touching `spikes/**`, `specs/001-phase0-storage-spikes/**`,
     `docs/phase0/**`, `docs/adr/**`):
     `nix flake check` for the cheap checks and `spikectl verify` with full git history.
  2. `suite` (KVM runner, manual dispatch and a label on the PR): `nix run ...#suite -- --mode
     shakedown` by default; uploads the junit XML and the scratch output as an artifact. In
     record mode the artifact contains the new record directory; the record reaches `main`
     only through a human-reviewed PR (no CI push).
  3. `repro` (KVM runner, manual dispatch): three chained jobs (each `needs` the previous one,
     each on a clean runner) that run the suite in record mode; a final job runs
     `spikectl verify --repro`.
  Third-party actions are pinned by full commit SHA.
- **KVM**: standard GitHub-hosted Linux runners can expose `/dev/kvm` after a udev rule adds
  the runner user to the `kvm` group; GitHub documents this for the larger and 2-vCPU
  runners, and Determinate Systems reports it works on standard runners for public
  projects. This repository is public. It is **observed, not guaranteed**. Sources:
  <https://github.blog/changelog/2024-04-02-github-actions-hardware-accelerated-android-virtualization-now-available/>,
  <https://determinate.systems/blog/kvm-on-github-actions>.
- **Reference runner**: the spec says "the project's reference runner" without defining it.
  Recommendation: a runner with >= 8 vCPU, >= 32 GiB RAM and >= 100 GiB free disk, KVM, with the
  state directory on disk (open item O1). Whether GitHub's standard runner has enough disk
  for the `large` profile is **UNVERIFIED**.
- **Suite budget (SC-009, 2 hours initial target)**: kept at 2 hours. Estimates below are
  `[Guessing]` (nothing was run) and are replaced by measurements during shakedown. They were
  adjusted on 2026-10-15 for the tiered-cache baseline, for these reasons: S3 enumerates 19 crash
  points (38 cases) instead of 34 (68), because the whiteout steps and the overlay view-refresh
  points are gone; S1 no longer copies up a 4+ GiB file; S2 gains snapshot variants; X1 is not in
  the suite.

  | Spike | Main cost | Serial estimate | Notes |
  | --- | --- | --- | --- |
  | S1 | one 4+ GiB file written through SMB and another modified in place, several checksums; 11 scenarios | 20 min | `large` profile; was 25 min (no copy-up) |
  | S2 | 9 scenarios, each: populate, sync, fail, recover; scenarios 2 and 4 repeated with the snapshot workflow; small data | 25 min | `small` profile; was 20 min |
  | S3 | 19 crash points x 2 kill modes = 38 cases x (reboot, invariants, retry, retry-kill, drill) at ~2-4 min | 76-152 min serial | sharded 4-way: 19-38 min; was 135-270 serial |
  | S4 | 4 states, all transitions, reboots | 15 min | |
  | S5 | >= 16 rebuilds (3 classes x 5 warm + cold) at ~1-3 min, plus failure scenarios | 30 min | independent, runs parallel |
  | Negative controls | ~6 variants, including the stock `--remove-source-files` mover | 15 min | |
  | X1 (optional) | former S1 scope on the overlay plus the layer-mutation scenario | ~30 min | outside the budget; only with `--include-x1` |

  With S5 in parallel and S3 four-way sharded, the wall time is estimated at 80-100 min on the
  recommended runner (the first plan estimated 95-130 min); six-way sharding brings the estimate
  to roughly 70-90 min. S3 is still the budget risk, now with more margin. Levers in order of
  preference: more shards, fewer redundant crash points proven equivalent in shakedown, a smaller
  fixture. The plan may revise the target with a recorded reason (spec assumption); that
  happens after shakedown measurements, not before.

  **Note 2026-10-16 (reference runner decided; the text above is the 2026-10-15 recommendation and
  stays as written).** The reference runner is defined in the spec (section "Reference runner"):
  record-mode runs are local on the maintainer's workstation (KVM, 24 logical CPUs, about 31 GiB
  RAM of which only about 10 to 12 GiB were available when measured, about 265 GiB free disk), with
  a VM memory budget of at most 8 GiB for all test VMs together and a state directory on disk with
  at least 100 GiB free. No self-hosted runner is registered for this public repository, so CI
  covers the `static` job and shakedown runs where a runner offers KVM. The "recommended runner"
  (>= 32 GiB RAM) of the estimate above is therefore not the runner that is used: the 8 GiB cap
  limits how many VMs, and so how many S3 shards, run in parallel, and the 80-100 min estimate may
  not hold. It is revisited after the shakedown measurements with a recorded reason (spec
  Assumptions, SC-009).

---

## 3. Technical findings with sources

### 3.1 mergerfs passthrough and the stacking option (X1; the last bullet also concerns S1)

Only the passthrough bullets apply to X1. The baseline's mergerfs facts are in section 7.2.

- `passthrough.max-stack-depth=INT` exists in mergerfs 2.41.1 and 2.42.0. The documentation
  (`mkdocs/docs/config/options.md`): "Set to `1` another filesystem can be stacked on
  mergerfs. Set to `2` to have mergerfs stacked over another filesystem. (default: 1)".
  `[Certain]` Source: trapexit/mergerfs `960f5fdbba5e053eb4a328b409cda0d9dd2e0d48`
  (2.41.1) and `ce3737ff3314ee0eb5edc38720b1a3f1fcb0e153` (2.42.0), file
  `mkdocs/docs/config/options.md`.
- The default in code is also 1: `int passthrough_max_stack_depth = 1;` in
  `libfuse/include/fuse_cfg.hpp` (2.41.1) and `vendored/libfuse/include/fuse_cfg.hpp`
  (2.42.0). The config key is registered in `src/config.cpp`
  (`_map["passthrough.max-stack-depth"]`). `[Certain]`
- mergerfs advertises the value to the kernel whenever the kernel is capable, independent of
  `passthrough.io`: `src/fuse_init.cpp` calls `_want_if_capable(conn_, FUSE_CAP_PASSTHROUGH)`
  unconditionally, and `libfuse/lib/fuse_lowlevel.cpp` then sets
  `outarg.max_stack_depth = fuse_cfg.passthrough_max_stack_depth`. `[Certain]`
  Consequence: with defaults, the mount already tells the kernel `max_stack_depth = 1`
  (finding F1).
- `passthrough.io` (`off`/`ro`/`wo`/`rw`, default `off`) is a separate option; it "requires
  Linux v6.9 or above", works only when mergerfs runs as root, needs `cache.files` other
  than `off` (mergerfs resets it to `auto-full`) and is incompatible with
  `cache.writeback` (reset to `false`). Source: `mkdocs/docs/config/passthrough.md`,
  2.41.1. `[Certain]`
- README of 2.41.1 lists "Read/write overlay on top of read-only filesystem like OverlayFS"
  under non-features, and the FAQ ("Does mergerfs support CoW ...") recommends including an
  overlayfs mount as a branch inside the pool (finding F6). `[Certain]` Sources: `README.md`,
  `mkdocs/docs/faq/compatibility_and_integration.md`, 2.41.1.
- mergerfs docs on `rename`: with path preservation `rename` can return `EXDEV` and the
  `rename-exdev` option controls the fallback (`mkdocs/docs/config/rename-exdev.md`);
  the known-issues page describes SMB clients that mishandle `EXDEV`
  (`mkdocs/docs/known_issues_bugs.md`, "Moving files and directories fails with Samba").
  Relevant to S1 scenario 6 (renames) and to X1. `[Certain]`

### 3.2 Kernel: FUSE passthrough and overlayfs stacking (v6.18 sources; X1 only)

All from `torvalds/linux` tag `v6.18` (tag object `f7b88edb52c8dd01b7e576390d658ae6eef0e134`).

- `fs/fuse/inode.c`, `process_init_reply`: passthrough is enabled only if
  `CONFIG_FUSE_PASSTHROUGH`, the `FUSE_PASSTHROUGH` flag, `max_stack_depth > 0`,
  `max_stack_depth <= FILESYSTEM_MAX_STACK_DEPTH` and no `FUSE_WRITEBACK_CACHE`; then
  `fc->max_stack_depth = arg->max_stack_depth` and `fm->sb->s_stack_depth =
  arg->max_stack_depth`. The in-source comment: "with `max_stack_depth == 1`, FUSE fs can
  be stacked as the underlying fs of a stacked fs (e.g. overlayfs)" and with `> 1` the backing
  files may themselves be on a stacked filesystem. `[Certain]`
- `include/linux/fs.h`: `#define FILESYSTEM_MAX_STACK_DEPTH 2`. `[Certain]`
- `fs/overlayfs/super.c`: for each lower layer `stack_depth = max(stack_depth,
  lower_sb->s_stack_depth)`, then `sb->s_stack_depth++` and, if it exceeds
  `FILESYSTEM_MAX_STACK_DEPTH`, "maximum fs stacking depth exceeded" and `EINVAL`. `[Certain]`
- Consequences `[Likely]` (derived by reading, to be shown by X1 scenario 1): FUSE
  depth 1 + overlay 1 = 2 mounts; depth 0 (passthrough not negotiated) + overlay = 1 mounts;
  depth 2 + overlay = 3 is refused. So the option is a ceiling, and the stack works at both 0 and
  1. A 0 value disables FUSE passthrough altogether (kernel requires `> 0`).
- `fs/overlayfs/super.c` `ovl_lower_dir`: when `index` or `nfs_export` is requested but the lower
  filesystem cannot decode file handles, overlayfs falls back to `index=off,nfs_export=off`
  with a warning. FUSE lower layers normally cannot, so `index` is likely unavailable, which
  affects hard links across copy-up (overlay documentation: hard links of lower files are
  broken by copy-up without `index`). `[Likely]`; matters for the "hard links" edge case of X1.
- `fs/fuse/Kconfig`: `config FUSE_PASSTHROUGH` is `default y` and depends on `FUSE_FS`. The
  nixpkgs common kernel configuration does not mention `FUSE_PASSTHROUGH`, `FUSE_FS` or
  `OVERLAY_FS` (grep over `pkgs/os-specific/linux/kernel` found nothing), so the default
  applies. `[Likely]`; the suite checks `CONFIG_FUSE_PASSTHROUGH` from the running kernel as a
  pre-flight check and records it (UNVERIFIED until run).

### 3.3 overlayfs semantics relevant to X1

Source: `Documentation/filesystems/overlayfs.rst` at `v6.18`.

- Whiteouts are character devices 0/0 (or zero-size files with the `trusted.overlay.whiteout`
  xattr); opaque directories carry an xattr. X1 scenarios 4 and 5 check these in the pool layer.
- Renaming a directory that is on a lower layer or merged returns `EXDEV` by default; with
  `redirect_dir` enabled the directory is copied up (not its contents) and a
  `trusted.overlay.redirect` xattr is set. `redirect_dir` is on only if the mount option or
  `CONFIG_OVERLAY_FS_REDIRECT_DIR` says so. So X1 scenario 6 ("renames succeed ... unless a
  documented setting makes it succeed, in which case that setting is recorded as required")
  will most likely need `redirect_dir=on`. `[Certain]` for the text; the NixOS kernel default for
  `CONFIG_OVERLAY_FS_REDIRECT_DIR` is **UNVERIFIED** (not found in the nixpkgs kernel configs).
- "Changes to the underlying filesystems while part of a mounted overlay filesystem are not
  allowed. If the underlying filesystem is changed, the behavior of the overlay is undefined,
  though it will not result in a crash or deadlock." Offline changes to the lower tree are
  allowed only if `metacopy`, `index`, `xino` and `redirect_dir` have not been used
  (finding F4). Because X1 scenario 6 will probably need `redirect_dir`, even a remount around the
  mover's archive changes may not be a valid refresh procedure; the scenario has to show it.
  `[Certain]` for the text, `[Likely]` for the consequence. Plan consequence: X1 scenario 9 is run for the lower layer
  **and** for the upper layer changes the mover performs (pool copy removal, whiteout
  removal), and the candidate refresh procedure is "unmount and remount the overlay around
  the layer change" (cost: the share is unavailable during it; to be measured).
- Copy-up copies the whole file, which is why the >4 GiB copy-up and the out-of-space copy-up
  are separate scenarios. `metacopy` (copy metadata only) is a mount option that interacts with
  the offline-change rule above; the baseline for the criteria is `metacopy=off`.
  `[Likely]`
- `volatile` mounts are not guaranteed to survive a crash; they are not used.

### 3.4 SnapRAID 14.4 semantics relevant to S2

Source: manual text in `doc/snapraid.d` and `cmdline/*.c` at tag `v14.4`, read from the nix
fetch of `amadvance/snapraid`; the same statements appear on <https://www.snapraid.it/manual>.
All `[Certain]` unless marked.

- **Recovery sequence** (manual "Recovering"): avoid further changes and disable scheduled
  jobs; step 1 reconfigure the failed disk's `data` entry to point at a location with enough free
  space; step 2 `snapraid -d NAME -l fix.log fix`; unrecoverable files are renamed with the
  `.unrecoverable` extension and listed as `unrecoverable:` lines in the log; the fix can be
  retried many times "but note that after syncing, you cannot retry the `fix` command anymore!";
  step 3 `snapraid -d NAME -a check` (checks only the specified disk and ignores parity); step 4
  `snapraid sync`. This is the skeleton of the S2.7 written procedure (detect, lock, replace,
  recover, verify, resynchronise, unlock).
- **Limitation text**: "you may be unable to recover up to the size of the changed or deleted
  files since the last sync operation. This occurs even if the changed or deleted files are not
  on the failed disk" and "newly added files don't prevent recovery of already existing files.
  You will only lose the recently added files if they are on the failed disk" (finding F2).
  Predictions must therefore be computed per parity block position.
- **`fix` semantics**: compares files and parity with the snapshot of the last sync and
  "unconditionally reverts the file state to the last sync"; `-d`, `-f`, `-m` (undelete),
  `-e` filters; `-l` writes the log; `-i/--import` lets `fix` use files moved out of the array,
  but not blocks that an interrupted sync recorded as deleted (measured by probe T057, spec Clarifications 2026-10-20, S2 SnapRAID probe).
- **`sync` semantics**: can be stopped with Ctrl+C and resumes where it stopped; files are
  identified by path and/or inode and checked by size and timestamp; changed files get their
  parity recomputed; only content and parity files are modified, never the array files. `-E`
  `--force-empty`: refuses to sync a disk whose original files are all missing or rewritten unless
  given (finding F3). `-Z` is the analogue for zero-size truncation.
- **`autosave SIZE_IN_GIGABYTES`** (config): "Automatically saves the state when syncing or
  scrubbing after the specified amount of GB processed". The unit is whole GB, so the
  commit granularity of an interrupted sync is the autosave step; with the `small` profile data
  the completed-block boundary of an interrupted sync must be derived differently (below).
- **Deterministic partial sync**: `-S/--start BLKSTART` and `-B/--count BLKCOUNT` process only a
  range of blocks ("mainly for advanced manual recovery"). A sync limited to the first K blocks is
  a clean, deterministic "stopped after K blocks"; the oracle's S2.4 prediction for the device
  failure case uses it. For a real process kill the tag lines `run:pos:<blockpos>:...`
  (`cmdline/state.c`) serve as the named trigger "kill when position >= K", but only with the
  undocumented `--gui` option, at most one line per second, and the processed boundary lies 1 to
  2 positions past the last line; the kill leaves the content file of the sync start, so the check
  derives the boundary from the parity file, never from the log (measured by probe T057, spec Clarifications 2026-10-20, S2 SnapRAID probe).
- **Machine-readable log**: with `-l`, `check`/`fix` write tags `status:unrecoverable:<disk>:<path>`,
  `status:recovered:...`, `status:recoverable:...`, `status:correct:...`, `error:...`,
  `unrecoverable:...` and `summary:exit:ok|recovered|...` (`cmdline/check.c`). They are evidence,
  not the verdict (D7).
- **Block size**: default 256 KiB (`blocksize 256`), "highly recommended not to change" and
  changing it requires recreating parity. Each file occupies a whole number of blocks per disk,
  so fixtures use file sizes that are multiples of the block size to make per-file block
  positions computable.
- **Block-position allocation model**: per disk, removals happen before insertions; new and
  rewritten files are ordered by FIEMAP physical start and take the lowest free positions from
  position 0, while parity position *p* combines the blocks at position *p* on every disk. Probe
  T057 matched this model against `locate` and the parity file in all measured runs. The model is
  still scoped to the measured single-parity allocation cases; double parity and other variants
  remain listed below as unverified.
- **Parity files** are normal files on the parity disk; at least `n_parity + 1` copies of the
  `content` file are needed (manual "content"), so the S2 configuration keeps one on the system
  disk and one on each parity disk; it keeps none on an archive data disk because retained
  snapshots can deadlock a full data disk before cleanup releases space.
- `snapraid pool` (symlink view) is superseded by mergerfs per the manual and is not used.

### 3.5 btrfs pool behaviour (docs, 6.19.1 era)

Sources: <https://btrfs.readthedocs.io/en/latest/btrfs-man5.html> ("degraded"),
<https://btrfs.readthedocs.io/en/latest/btrfs-replace.html>. `[Certain]` for the quoted text
(the pages are the latest documentation, not pinned to 6.19.1; UNVERIFIED that the text is
identical for 6.19.1).

- `degraded`: "Allow mounts with fewer devices than the RAID profile constraints require. A
  read-write mount (or remount) may fail when there are too many devices missing ... mount will
  fall back to other profiles like SINGLE. It is recommended to check and balance chunks with
  unwanted profiles, after a read-write degraded mount." So S4 "pool degraded keeps accepting
  writes" is plausible, and the repair step after replacement must include a balance that converts
  chunks written while degraded back to RAID1; S4 scenario 6 checks the final profile.
- `btrfs replace start`: "If the source device is not available anymore ... the data is built only
  using the RAID redundancy mechanisms"; a disconnected source must be addressed by devid; the
  target must be the same size or larger; resize afterwards with `btrfs filesystem resize
  <devid>:max <mount>`.
- A degraded mount does not persist across a reboot by itself; the mount must name `degraded`.
  **UNVERIFIED** in this harness (S4 scenario 7).

### 3.6 Samba

- samba 4.23.10 at the pinned revision. The spikes need only: one share on the cache pool mount, one
  test user, DOS attributes and extended attributes enabled. `store dos attributes` keeps DOS
  attributes in an xattr, which requires xattr support in the stack (the cache pool is a FUSE mount over btrfs branches;
  mergerfs' `xattr` option defaults to `passthrough`, `mkdocs/docs/config/xattr.md` at 2.41.1,
  `[Certain]`). Exact keys and whether `user.*` xattrs written through the share survive
  a rename and the mover's copy (the stock scripts use `rsync --xattrs`) are **UNVERIFIED** and are exactly what S1 scenario 7
  tests.

### 3.7 NixOS test driver facts used by the plan

All `[Certain]`, nixpkgs `a033e66`:

- Disks: `virtualisation.emptyDiskImages` creates sparse `empty<N>.qcow2` files in the machine
  state directory on first start (`qemu-img create -f qcow2`), with per-disk `driveConfig` (so a
  serial can be set); they persist across stop/start of the same machine.
- `Machine.crash()`: monitor `quit` (QEMU process termination, not host power loss);
  `Machine.shutdown()`: `poweroff` in the guest;
  `Machine.send_monitor_command()` and a QMP session exist; `Machine.block()`/`unblock()` toggle
  the vlan link (not used for disk faults).
- `qemu.forceAccel` aborts if `/dev/kvm` is missing or not accessible.
- Test script helpers used: `wait_for_unit`, `wait_for_file`, `succeed`/`fail`,
  `wait_until_succeeds`, `copy_from_vm`.
- Prior art for `crash()` then restart in tests: `zfs.nix`, `luks.nix`, `systemd-initrd-swraid.nix`.
- Prior art for btrfs RAID with `useBootLoader`: `systemd-initrd-btrfs-raid.nix`; for overlay mounts:
  `filesystems-overlayfs.nix`; for SMB server/client: `samba.nix`.

---

## 4. Best-practice checks for the chosen technologies

- **Pin by revision and lock**: nixpkgs by full commit SHA with `narHash` in `flake.lock`; the spike
  flake follows no other input. (D1)
- **Tests next to what they test**: scenario code, oracle and fixtures are separate files per spike
  and per concern, none above ~500 LoC; the in-VM reference mover and lock scripts are separate
  from the test scripts that drive them (constitution: test code separate, file size).
- **Read-only inputs**: guest-side archive branches are mounted read-only for the share; only the
  reference mover has a read-write path (D11).
- **No sleeps**: waits use `wait_for_*` helpers, hooks and log-tag triggers (FR-010).

## 5. Integration patterns

- **Hooks as files**: the host arms a point by creating a file in a guest-side hook directory;
  the guest blocks on a named FIFO; the host observes a `reached` marker. No network and no
  extra daemon.
- **Records as plain files**: JSON next to criteria, validated by JSON Schema in CI.
- **ADR link**: ADR text cites record paths; CI resolves them.

## 6. Unverified items (collected)

1. `device_del` of a `virtio-blk-pci` disk and `drive_del` behaviour on the pinned QEMU; guest
   and btrfs reaction (D5).
2. `blkdebug` backing definitions inside the NixOS test start script (D5).
3. Copying the state of a powered-off machine into a second machine (drill machine, D7).
4. Residual SnapRAID variants not run by probe T057: double parity, autosave, a device failure or
   power-off during sync, `scan:move`/`copy` placement, and concurrent writers (3.4).
5. `CONFIG_FUSE_PASSTHROUGH`, `CONFIG_OVERLAY_FS_REDIRECT_DIR` in the NixOS 6.18 kernel (3.2, 3.3); X1 only.
6. Whether the stable patches between kernel 6.18 and 6.18.56 touch the analysed paths (1).
7. Whether overlayfs over mergerfs behaves as the code reading predicts (3.1, 3.3). This is what
   the optional experiment X1 decides; it gates nothing. Whether the tiered-cache share behaves
   as documented, including xattrs and the `EXDEV` handling by SMB clients (3.1, 3.6, 7.2), is
   what S1 decides.
8. S5: the ADR-0004 layout with local persistent store and bootloader in a test VM, offline
   rebuild closure size and duration, `nixos-version --configuration-revision` reporting (D10).
9. btrfs documentation text for 6.19.1 exactly; degraded mount across reboot (3.5).
10. GitHub standard runner KVM access and disk size for the `large` profile (D14); all time
    estimates in the budget table (nothing was run).
11. Smb.conf keys and client commands for DOS attributes (D4, 3.6).
12. Qemu `quit` semantics beyond the cited driver code and the exact cache mode of the empty disk
    images (default `writeback`); the emulation gap for device-level volatile caches (D5).
13. Two mergerfs pools (base and cache) mounted over the same branches at the same time, and the
    effect of the cache timeouts (1 s default) on the view while the mover changes branches (7.2).
14. Whether `NC` branch mode excludes a branch from the `moveonenospc` policy; behaviour with
    `moveonenospc` off and `minfreespace` below the free space (F10, 7.2).
15. The written recovery sequence of S2.7 after a disk failure (7.3); the replacement-disk
    configuration and runtime behaviour were measured by probe T057.
16. `snapraid-btrfs` packaging by pinned revision at the project pin (F12); its generated
    configuration and runtime needs were measured by probe T057.
17. The native `snapshot` option of SnapRAID 15.0 (release candidate only; not at the pin) (7.3).
18. btrfs headroom on `arch0` and `arch1`, and `ENOSPC` behaviour with all content copies off the
    data disks (D3); probe T057 measured only the `arch2` case and the copy-on-data-disk hazard.

---

## 7. Decision of 2026-10-15: the tiered-cache pattern is the baseline, overlayfs is experiment X1

### 7.1 Decision and reason

The first plan tested "overlayfs on top of a read-only mergerfs archive". Section 0 showed that
this is not an established setup: the mergerfs README lists "Read/write overlay on top of
read-only filesystem like OverlayFS" under its non-features (F6), upstream documents no
overlayfs on mergerfs, and the kernel documents changes to underlying layers of a mounted overlay
as undefined, which is exactly what the mover does to both layers (F4). The maintainer therefore
decided on 2026-10-15 that the **established pattern becomes the baseline ("variant A")** and that
overlayfs is kept only as a **separate, optional, non-gating experiment (X1)**. Findings F2, F3 and
F5 stay valid; F1, F4 and F6 apply to X1 only. X1 never gates ADR-0002; its result only decides
whether a copy-up enhancement is worth adding later.

### 7.2 What mergerfs documents (the pattern and the options it touches)

Sources: `trapexit/mergerfs` at tag `2.41.1` (commit `960f5fdbba5e053eb4a328b409cda0d9dd2e0d48`),
read through the GitHub contents API at that commit; the same section also exists on the default
branch at `8e87bcbff2689872e61752737308570717af1a3a` (read 2026-10-15). All `[Certain]` unless
marked.

- `mkdocs/docs/extended_usage_patterns.md`, section "tiered cache":
  - "mergerfs does not natively support any sort of tiered caching currently."
  - Create two mergerfs pools: a **base pool** with only the slow branches and a **cache pool**
    with both the fast branches and the slow branches, the cache branches listed **first**.
  - "The best `create` policies to use for the cache pool would probably be `ff`, `lus`, or
    `lfs`", the latter two assuming the cache filesystems are far smaller than the backing ones.
  - The slow branches' mode may be set to `NC`, which allows other create policies; "if the cache
    filesystems fill you'd get 'out of space' errors", which the page calls good because it
    shows that the mover is not configured properly.
  - Programs use the cache pool. The mover "moves files from the cache branches (not pool) to
    the base pool", scheduled with `cron`.
  - Hierarchies of nested mergerfs pools are discouraged; the two pools of the pattern are
    siblings over shared branches, not nested.
- The shipped scripts `tools/mergerfs.time-based-mover` and `tools/mergerfs.percent-full-mover`
  use `find` to pick files and `rsync --archive --acls --xattrs --atimes --hard-links
  --one-file-system --preallocate --remove-source-files` to copy from the cache branch to the base
  pool (read at the commit above). `--remove-source-files` deletes each source file right after
  it was transferred, with no sync in between: this is finding F11 and the stock-pattern negative
  control of S3 scenario 5.
- `mkdocs/docs/config/branches.md`: branch mode `NC` "will be excluded from create policies. You
  can't create on that branch but you can change or delete"; `RO` is excluded from create and
  action policies. So an archived file can still be modified or deleted in place through the
  cache pool (S1 scenarios 3 and 4) while new files cannot land on an archive disk.
- `mkdocs/docs/config/functions_categories_policies.md`: `ff` acts on "the first one found" in
  branch order; `lus` picks the branch with the least used space; `lfs` the one with the least
  free space.
- `mkdocs/docs/config/options.md`, `moveonenospc.md`, `minfreespace.md`: `moveonenospc` is on by
  default (`pfrd`), moves a file to another branch when a write fails with `ENOSPC` and unlinks the
  original; `minfreespace` defaults to `4G` and makes create policies skip branches below it
  (finding F10). Whether `NC` branches are excluded from the `moveonenospc` policy is not stated
  in the pages read (**UNVERIFIED**), so the baseline turns `moveonenospc` off in the cache pool
  and S1 scenario 9 proves it.
- `cache.entry` and `cache.attr` default to 1 second, `cache.negative-entry` and `cache.statfs`
  to 0 (`options.md`). Lookups and attributes may therefore be stale for up to a second after
  the mover changed a branch directly; whether anything beyond waiting is needed is what S1
  scenario 10 records (**UNVERIFIED**).
- `known_issues_bugs.md` and `rename-exdev.md` (renames with path preservation can return
  `EXDEV`, and some SMB clients mishandle it) apply to S1 scenario 6.

### 7.3 SnapRAID with btrfs snapshots

Sources: `automorphism88/snapraid-btrfs` at commit `6492a45ad55c389c0301075dcc8bc8784ef3e274`
(README and the `snapraid-btrfs` script); `dim-geo/btrfssnapraid` at commit
`a401621928e00b8d2d2c3f19c724fa46b58c5c38` (README); `amadvance/snapraid` release `v15.0rc2`
(tag object `1f6c7970f58a5d299ea1fb9069977646808d1855`, marked pre-release, published
2026-09-29), release notes. Read 2026-10-15. The statements are what the documentation and the
script say; **none of it was run** and the whole workflow is **UNVERIFIED in practice**.

- `snapraid-btrfs` runs `check`, `diff`, `scrub` and `sync` against read-only snapshots of the data
  disks (it rewrites the SnapRAID configuration to point at the snapshots) and creates pre and
  post snapshots around `fix` and `touch`. Snapshots are managed through `snapper`, one config per
  data disk. Its FAQ: if files are modified or deleted after a sync, "we can always restore the
  array to the state it was in at the time the read-only snapshots were created, so long as the
  snapshots are not deleted until another sync is completed with new snapshots"; if a sync is
  interrupted, both snapshot sets must be kept until a sync completes; parity and content files do
  not need snapshots; content files on a data disk need a dedicated subvolume; ext4 is recommended
  for parity; space used by retained snapshots is a documented cost.
- In its `fix` handling (`use_snapshot_fix` in the script), the disk named with `-d` is fixed on the
  live filesystem, and the other disks are read from the `scrub` snapshot (per `--help`: the
  snapshots of an interrupted sync, or of the last completed sync if more recent, or the
  post-fix snapshot if a fix was done more recently). A `fix` without `-d` uses the live
  filesystem for all disks. This is the
  mechanism behind "recovery against the snapshots after changes on surviving disks" in S2
  scenario 9. How a replaced disk gets its own snapper configuration and how the written recovery
  sequence changes are things S2 scenario 7 and 9 must establish (**UNVERIFIED**).
- `btrfssnapraid` additionally snapshots parity and content files after each sync and keeps sets of
  snapshots with a shared counter, so that older file versions are protected too. It needs
  Python 3.12 and `snapper`; its last push was 2023-12-13 (GitHub `pushedAt`), so its maintenance
  state is a risk. It is a candidate, not a baseline.
- SnapRAID `v15.0rc2` release notes: "Added support for automated filesystem snapshots ... When
  the 'snapshot' option is enabled ... Supported on Btrfs, Bcachefs, and ZFS in Linux ... ensures
  that 'scrub', 'check', and 'fix' operations utilize a frozen point-in-time reference for data
  disks". The nixpkgs pin carries 14.4 (finding F12), and v15.0rc2 is a release candidate, so
  the native option is not usable at the pin; it is the open-item alternative to external tooling.
- Tool choice (open item O15): default is `snapraid-btrfs` with `snapper` at the pinned SnapRAID
  14.x, fetched by pinned revision because it is not in nixpkgs. The native v15 option replaces it
  only after v15 is released and verified by S2.
- Parity coverage and the mover: `snapraid-btrfs sync` takes new snapshots of all data disks
  before syncing, so M3 (sync) and M4 (verification) of the mover refer to that snapshot set; a
  copy item counts as parity-covered only if it exists in the snapshot set of a **completed**
  sync: the `synced` userdata of the newest snapshot of every data disk together with
  `snapraid -c <config of that set> diff` exit 0 `summary:exit:equal`; `-u sync diff` rewrites
  the userdata and is never run (measured by probe T057, spec Clarifications 2026-10-20, S2 SnapRAID probe).

### 7.4 What changed in the plan

| Item | First plan | Now |
| --- | --- | --- |
| S1 | overlayfs over read-only mergerfs archive, `passthrough.max-stack-depth`, whiteouts, copy-up | mergerfs two-pool tiered-cache share under SMB: new files on the pool, archived files read, changed and deleted in place, create policy, `NC` branches, `moveonenospc`, view consistency across the mover's move, reboot |
| S2 | 8 scenarios, file-level predictions | 9 scenarios, per-block predictions (F2), `-E` and partial-replacement hazard path (F3), snapshot variant (scenario 9) |
| S3 | 9 mover steps, 34 crash points, 68 cases, whiteout invariants | 5 mover steps, 19 crash points, 38 cases, no whiteouts; stock rsync mover is a mandatory negative control |
| S4 | table with a read-only archive for share clients | table with in-place changes to archived files in healthy and pool degraded states, refused in archive degraded |
| S5 | unchanged | unchanged, with F5 (restoration is backend behaviour the harness must supply) added to scenario 4 |
| X1 | did not exist | optional, non-gating overlayfs experiment with its own app, profile and criteria |
| Archive disks | XFS, XFS, ext4 | btrfs, each its own filesystem; parity on ext4 |
| Scenario total | 43 (11+8+9+7+8) | 44 gating (11+9+9+7+8), plus 10 optional X1 scenarios |

Crash point recount (`contracts/mover-steps.md`): boundaries `pre:M1`, `post:M1` to `post:M5` are 6
points. Mid-step points of the first loop item are M1 6 (`tmp-created`, `tmp-partial`,
`tmp-data-synced`, `renamed`, `parent-synced`, `item-1-done`), M2 2, M3 1, M4 1, M5 3
(`item-validated`, `unlinked`, `item-1-done`), 13 in total. 6 + 13 = **19 points, 38 cases**
(first plan: 10 + 24 = 34 points, 68 cases). The `view-refreshed` points of the first plan existed
only for the overlay; if S1 scenario 10 finds that the cache pool needs an explicit refresh step,
the step is added to the list before the criteria are frozen (a normal pre-freeze edit).

### 7.5 What was not verified

Everything in 7.2 and 7.3 was read, not run. The practical interplay of two mergerfs mounts over
the same branches, the cache timeouts versus the mover, `NC` versus `moveonenospc`, the snapshot
recovery procedure including a replaced disk, the snapper packaging on NixOS, and the native v15
option are all open until the shakedown and S1/S2 run. Section 6 lists them (items 13 to 19).
