# The S2 guest-script probe (tasks T060, T061): snapsync.sh and archive-lock.sh against the real
# SnapRAID, snapraid-btrfs, snapper and btrfs of the `server` node, with nix/snapper-configs.nix
# and nix/archive-lock.nix. A probe-only addition inside vm-tests/: it never writes a record.
# Run one at a time. Scaffolding for the Phase 0 spikes, not product code.
{ mkSpike }:

mkSpike {
  name = "probe-guest-s2";
  profile = "small";
  machines = [ "server" ];
  budget = "probe";
  testScript = ./probe_guest_s2.py;
  extraModules.server = [
    ../../nix/snapper-configs.nix
    ../../nix/archive-lock.nix
    { spike.snapshots.enable = true; spike.tieredCache.enable = true; }
  ];
}
