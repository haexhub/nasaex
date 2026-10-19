# The `server` and `client` VM nodes of the spikes (scaffolding, not product code).
#
# server: btrfs RAID1 pool with a pool subvolume, one btrfs filesystem per archive disk with a
#   `data` subvolume and a separate, never-snapshotted `content` subvolume, the SnapRAID parity
#   file on ext4, and the tools under test. The data subvolume holds two sibling directories
#   (trash amendment, task T028a): `branch`, the archive branch of both pools, and `trash`,
#   outside every pool branch; the SnapRAID data path is the whole data subvolume, so trash
#   content is parity-covered. That the trash stays hidden from both pool views is UNVERIFIED
#   until S1.1 and S3.7 (e) prove it. The disks start blank; `spike-mkfs` formats them
#   on demand, never at boot, so a blank replacement disk stays blank until a scenario acts.
#   Mounts are `nofail` and resolve by label, so a missing disk degrades instead of blocking boot.
# client: the SMB client VM of S1.
#
# No network access inside the VMs: the test vlan only connects the nodes with each other.
{ pkgs, lib, profile, snapraidBtrfs, guestScripts, vmSizing, testlibPackage }:

let
  disks = profile.disks;
  byRole = role: lib.filter (d: d.role == role) disks;
  pool = byRole "pool";
  archive = byRole "archive";
  parity = lib.head (byRole "parity");

  # emptyDiskImages entry i is the file empty<i>.qcow2 of the machine state directory; the
  # host swaps it to simulate a replaced disk (fault kind device-failed-at-boot).
  diskTable = lib.imap0 (idx: d: d // { image = "empty${toString idx}.qcow2"; }) disks;

  # Per archive disk: the data subvolume mount (the SnapRAID data path), the branch directory
  # and the trash directory beside it. Fixtures and the oracle name `branch` as the archive branch.
  archiveLayout = map
    (d: rec {
      inherit (d) serial;
      data = "/mnt/archive/${d.serial}";
      branch = "${data}/branch";
      trash = "${data}/trash";
    })
    archive;

  diskImage = d: {
    size = d.sizeMiB;
    driveConfig.deviceExtraOpts = {
      inherit (d) serial;
      id = "disk-${d.serial}";
    };
  };

  byId = serial: "/dev/disk/by-id/virtio-${serial}";
  mountOptions = [ "nofail" "x-systemd.device-timeout=10s" ];

  spikeMkfs = pkgs.writeShellApplication {
    name = "spike-mkfs";
    runtimeInputs = [ pkgs.btrfs-progs pkgs.e2fsprogs pkgs.util-linux pkgs.coreutils ];
    text = ''
      # spike-mkfs pool|archive|parity|all : format blank disks of the topology.
      # Refuses to touch a disk that already carries a filesystem signature.
      refuse_if_formatted() {
        if blkid -p "$1" >/dev/null 2>&1; then
          echo "spike-mkfs: $1 already carries a filesystem" >&2
          exit 1
        fi
      }
      directories() { # <label> <subvolume> <directory>...
        local tmp
        tmp=$(mktemp -d)
        mount -o "subvol=$2" "/dev/disk/by-label/$1" "$tmp"
        shift 2
        for dir in "$@"; do mkdir "$tmp/$dir"; done
        umount "$tmp"
        rmdir "$tmp"
      }
      subvolumes() { # <label> <subvolume>...
        local tmp
        tmp=$(mktemp -d)
        mount "/dev/disk/by-label/$1" "$tmp"
        shift
        for sv in "$@"; do btrfs subvolume create "$tmp/$sv" >/dev/null; done
        umount "$tmp"
        rmdir "$tmp"
      }
      do_pool() {
        ${lib.concatMapStringsSep "\n" (d: ''refuse_if_formatted ${byId d.serial}'') pool}
        mkfs.btrfs -q -f -L spikepool -d raid1 -m raid1 ${lib.concatMapStringsSep " " (d: byId d.serial) pool}
        udevadm settle
        subvolumes spikepool pool
      }
      do_archive() {
        ${lib.concatMapStringsSep "\n" (d: ''
          refuse_if_formatted ${byId d.serial}
          mkfs.btrfs -q -f -L ${d.serial} ${byId d.serial}
          udevadm settle
          subvolumes ${d.serial} data content
          directories ${d.serial} data branch trash
        '') archive}
      }
      do_parity() {
        refuse_if_formatted ${byId parity.serial}
        mkfs.ext4 -q -F -L ${parity.serial} ${byId parity.serial}
        udevadm settle
      }
      case "''${1:-all}" in
        pool) do_pool ;;
        archive) do_archive ;;
        parity) do_parity ;;
        all) do_pool; do_archive; do_parity ;;
        *) echo "usage: spike-mkfs pool|archive|parity|all" >&2; exit 2 ;;
      esac
    '';
  };

  # Test VMs replace `fileSystems` with `virtualisation.fileSystems`, so the mounts go there.
  fileSystems = {
    "/mnt/pool" = {
      device = "/dev/disk/by-label/spikepool";
      fsType = "btrfs";
      options = [ "subvol=pool" ] ++ mountOptions;
    };
    "/mnt/parity" = {
      device = "/dev/disk/by-label/${parity.serial}";
      fsType = "ext4";
      options = mountOptions;
    };
  }
  // lib.listToAttrs (map
    (d: {
      name = "/mnt/archive/${d.serial}";
      value = {
        device = "/dev/disk/by-label/${d.serial}";
        fsType = "btrfs";
        options = [ "subvol=data" ] ++ mountOptions;
      };
    })
    archive)
  // lib.listToAttrs (map
    (d: {
      name = "/mnt/archive-content/${d.serial}";
      value = {
        device = "/dev/disk/by-label/${d.serial}";
        fsType = "btrfs";
        options = [ "subvol=content" ] ++ mountOptions;
      };
    })
    archive);

  common = {
    # No network access inside the VMs: the host-facing user network is isolated, only the
    # test vlan between the nodes remains.
    virtualisation.restrictNetwork = true;
    boot.supportedFilesystems = [ "btrfs" "ext4" ];
    # testlib in the guest: the fixture generator populates branches, testlib.manifest reads
    # branches on the server and the share on the client (testlib/smb.py).
    environment.systemPackages = [ (pkgs.python3.withPackages (_: [ testlibPackage ])) ];
  };

  server = { ... }: {
    imports = [ common ./tiered-cache.nix ];
    spike.tieredCache.archiveBranches = lib.mkDefault (map (a: a.branch) archiveLayout);
    virtualisation = vmSizing.server // {
      emptyDiskImages = map diskImage disks;
      inherit fileSystems;
    };

    # systemd creates mount points for its own mount units only; tests also mount by hand.
    systemd.tmpfiles.rules = map (mountPoint: "d ${mountPoint} 0755 root root -")
      (lib.attrNames fileSystems);

    # The bounded pool retrigger unit, a required setting of the pool storage (FR-013; spec
    # Clarifications 2026-10-19, S1.11 reboots; task T028b). Of the two pool members, the one
    # whose `btrfs ready` runs first stays SYSTEMD_READY=0 until the retrigger of line 15 of
    # systemd's 64-btrfs.rules reaches it; a member whose udev database is written after that
    # trigger stays not ready, and if it owns /dev/disk/by-label/spikepool the label device unit
    # never appears and the pool mounts fail (probe T051f, vm-tests/probes/pool_race.results.md).
    # This is `retriggerBounded "change"` of vm-tests/probes/pool_race_modules.nix, unchanged:
    # variant U2 with action change of probe T051i (vm-tests/probes/retrigger_v2.results.md:
    # forced race rescued in 10 of 10 boots, 30 normal boots up with no event sent, exit 0 in
    # degraded boots and with udev unsettled for 31 s). It sends a change event only to block
    # devices whose udev database still says ID_BTRFS_READY=0, so a missing member is never
    # named, and neither bounded wait fails the unit. Checked by tests/pool_retrigger.nix.
    systemd.services.pool-retrigger = {
      description = "Retrigger not-ready btrfs devices after udev settled (bounded)";
      wantedBy = [ "local-fs.target" ];
      before = [ "local-fs.target" ];
      after = [ "systemd-udev-trigger.service" ];
      unitConfig.DefaultDependencies = false;
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "10s"; };
      path = [ pkgs.systemd pkgs.coreutils ];
      script = ''
        udevadm settle --timeout=5 || echo "udev queue not empty after 5 s (exit $?), triggering anyway"
        timeout 4 udevadm trigger --settle --action=change --subsystem-match=block \
          --property-match=ID_BTRFS_READY=0 || echo "trigger not settled within 4 s (exit $?)"
      '';
    };

    boot.kernelModules = [ "fuse" ];
    system.fsPackages = [ pkgs.mergerfs ];

    environment.systemPackages = [
      spikeMkfs
      guestScripts
      pkgs.mergerfs
      pkgs.snapraid
      pkgs.btrfs-progs
      pkgs.e2fsprogs
      pkgs.snapper
      snapraidBtrfs
      pkgs.samba
      pkgs.rsync
      pkgs.util-linux
      pkgs.coreutils
    ];
  };

  client = { ... }: {
    imports = [ common ];
    virtualisation = vmSizing.client;
    boot.supportedFilesystems = [ "cifs" ];
    environment.systemPackages = [ pkgs.cifs-utils pkgs.samba ];
  };
in
{
  inherit server client diskTable archiveLayout spikeMkfs;
}
