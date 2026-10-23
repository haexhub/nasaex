# The thin command wrappers of the S2 archive lock on the `server` node (task T061, research
# D11), and the restore of the lock at boot (task T066a). Scaffolding, not product code.
#
# `snapraid` and `snapraid-btrfs` in the system PATH become `archive-lock.sh snapraid` and
# `archive-lock.sh snapraid-btrfs` (high priority over the real tools of nix/nodes.nix), so a
# manual call while the archive is locked gets the defined refusal. snapraid-btrfs still runs the
# real snapraid through its own PATH. The lock itself is the read-only remount of
# guest/archive-lock.sh and does not depend on these wrappers.
#
# A boot mounts every location read-write from the fstab. `archive-lock-restore` runs
# `archive-lock restore` once the mounts of the data and parity disks have finished (mounted, or
# failed after their device timeout: they are nofail) and before sysinit.target, so before every
# timer and every ordinary service: the saved lock is set again, and an array that was synced is
# locked when a data disk is not mounted. Reads the array of nix/snapper-configs.nix.
{ config, pkgs, lib, utils, ... }:

let
  guestScripts = import ./guest-scripts.nix { inherit pkgs; };
  snapraidBtrfs = import ./snapraid-btrfs.nix { inherit pkgs; };
  cfg = config.spike.snapshots;

  wrapper = name: lib.hiPrio (pkgs.writeShellScriptBin name ''
    export ARCHIVE_LOCK_SNAPRAID=${lib.getExe pkgs.snapraid}
    export ARCHIVE_LOCK_SNAPRAID_BTRFS=${lib.getExe snapraidBtrfs}
    exec ${guestScripts}/bin/archive-lock ${name} "$@"
  '');

  mountUnits = map (path: "${utils.escapeSystemdPath path}.mount")
    ([ cfg.parityMount ] ++ map (serial: "/mnt/archive/${serial}") cfg.dataDisks);
in
{
  environment.systemPackages = [ (wrapper "snapraid") (wrapper "snapraid-btrfs") ];

  systemd.services.archive-lock-restore = {
    description = "Set the S2 archive lock again after a boot";
    wantedBy = [ "sysinit.target" ];
    after = [ "local-fs.target" ] ++ mountUnits;
    before = [ "sysinit.target" "shutdown.target" ];
    conflicts = [ "shutdown.target" ];
    unitConfig.DefaultDependencies = false;
    path = with pkgs; [ coreutils gawk gnugrep gnused util-linux ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${guestScripts}/bin/archive-lock restore";
    };
  };
}
