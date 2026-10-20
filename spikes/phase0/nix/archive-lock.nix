# The thin command wrappers of the S2 archive lock on the `server` node (task T061, research
# D11). Scaffolding, not product code.
#
# `snapraid` and `snapraid-btrfs` in the system PATH become `archive-lock.sh snapraid` and
# `archive-lock.sh snapraid-btrfs` (high priority over the real tools of nix/nodes.nix), so a
# manual call while the archive is locked gets the defined refusal. snapraid-btrfs still runs the
# real snapraid through its own PATH. The lock itself is the read-only remount of
# guest/archive-lock.sh and does not depend on these wrappers.
{ pkgs, lib, ... }:

let
  guestScripts = import ./guest-scripts.nix { inherit pkgs; };
  snapraidBtrfs = import ./snapraid-btrfs.nix { inherit pkgs; };

  wrapper = name: lib.hiPrio (pkgs.writeShellScriptBin name ''
    export ARCHIVE_LOCK_SNAPRAID=${lib.getExe pkgs.snapraid}
    export ARCHIVE_LOCK_SNAPRAID_BTRFS=${lib.getExe snapraidBtrfs}
    exec ${guestScripts}/bin/archive-lock ${name} "$@"
  '');
in
{
  environment.systemPackages = [ (wrapper "snapraid") (wrapper "snapraid-btrfs") ];
}
