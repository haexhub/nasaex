# The SnapRAID and snapshot-tool probe (task T057). A probe-only addition inside vm-tests/: it
# never writes a record. Profile `small`, `server` only, plus one NixOS snapper config per archive
# data subvolume (the `.snapshots` subvolume each config needs is created by the probe). The test
# script is snapraid_probe_setup.py, probe_snapraid_snapshots.py and probe_snapraid.py in this
# order (helpers, the phases snap and headroom, the phases alloc and kill and the main flow).
# Run one at a time. Scaffolding for the Phase 0 spikes, not product code.
{ mkSpike }:

let
  server = { lib, ... }: {
    services.snapper.configs = lib.genAttrs [ "arch0" "arch1" "arch2" ] (serial: {
      SUBVOLUME = "/mnt/archive/${serial}";
      FSTYPE = "btrfs";
    });
  };
in
mkSpike {
  name = "probe-snapraid";
  profile = "small";
  machines = [ "server" ];
  budget = "probe-long";
  testScript = builtins.toFile "probe-snapraid.py" (builtins.concatStringsSep "" (map builtins.readFile [
    ./snapraid_probe_setup.py
    ./probe_snapraid_snapshots.py
    ./probe_snapraid.py
  ]));
  scriptPackages.probes = ./.;
  extraModules.server = [ server ];
}
