# The array and the snapshot workflow of S2 on the `server` node (task T060; spec Clarifications
# 2026-10-20, S2 SnapRAID probe). Scaffolding, not product code.
#
#   - /etc/snapraid.conf: each archive disk's whole `data` subvolume is a data disk, single parity
#     in a file on the parity disk, content copies on the system disk (first, so SnapRAID's lock
#     file lives there) and on the parity disk, none on an archive data disk (with a copy there, a
#     full disk with retained snapshots cannot complete the sync that would free it, probe T057);
#     `.snapshots` is excluded
#   - one snapper config per archive data disk; the NixOS module writes only the config files
#   - `spike-snapshots-init SERIAL...` creates the `.snapshots` subvolume that snapper needs in a
#     data subvolume (root-owned, not world-writable). The disks start blank and a replacement
#     disk is new, so the harness runs it after `spike-mkfs` and after each replacement.
{ config, lib, pkgs, ... }:

let
  cfg = config.spike.snapshots;
  inherit (lib) mkOption types;

  data = serial: "/mnt/archive/${serial}";

  snapshotsInit = pkgs.writeShellApplication {
    name = "spike-snapshots-init";
    runtimeInputs = [ pkgs.btrfs-progs pkgs.coreutils pkgs.util-linux ];
    text = ''
      # spike-snapshots-init SERIAL...: <data subvolume>/.snapshots as a btrfs subvolume, 0750 root.
      for serial in "$@"; do
        dir=/mnt/archive/$serial
        if ! mountpoint -q "$dir"; then
          echo "spike-snapshots-init: $dir is not mounted" >&2
          exit 1
        fi
        if [ -e "$dir/.snapshots" ]; then
          btrfs subvolume show "$dir/.snapshots" >/dev/null # refuses anything but a subvolume
        else
          btrfs subvolume create "$dir/.snapshots" >/dev/null
        fi
        chown root:root "$dir/.snapshots"
        chmod 750 "$dir/.snapshots"
      done
    '';
  };
in
{
  options.spike.snapshots = {
    enable = lib.mkEnableOption "the SnapRAID array and the snapper configs of S2";
    dataDisks = mkOption {
      type = types.listOf types.str;
      default = [ "arch0" "arch1" "arch2" ];
      description = "Serials of the archive disks (nix/profiles.nix), mounted at /mnt/archive/<serial>.";
    };
    parityMount = mkOption {
      type = types.str;
      default = "/mnt/parity";
      description = "Mount point of the parity disk.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.etc."snapraid.conf".text = lib.concatStringsSep "\n" ([
      "parity ${cfg.parityMount}/snapraid.parity"
      "content /var/lib/snapraid/snapraid.content"
      "content ${cfg.parityMount}/snapraid.content"
    ]
    ++ map (serial: "data ${serial} ${data serial}/") cfg.dataDisks
    ++ [ "exclude /.snapshots/" "" ]);

    systemd.tmpfiles.rules = [ "d /var/lib/snapraid 0700 root root -" ];

    services.snapper.configs = lib.genAttrs cfg.dataDisks (serial: {
      SUBVOLUME = data serial;
      FSTYPE = "btrfs";
    });

    environment.systemPackages = [ snapshotsInit ];
  };
}
