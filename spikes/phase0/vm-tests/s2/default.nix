# The S2 VM test (task T065): parity recovery on the `server` alone. Profile `small`: three btrfs
# archive disks, one ext4 parity disk and one spare per size class (nix/profiles.nix), so a
# replacement of the same, a larger and a smaller size is at hand. The server runs the
# tiered-cache pools (its cache pool is the share of S2, vm-tests/s2/history.py), the
# snapshot workflow of T060 (nix/snapper-configs.nix with /etc/snapraid.conf) and the archive
# lock of T061 (nix/archive-lock.nix). Each scenario starts from blank disk images
# (history.reset). The checks are the package `s2` of the test script (this directory); the
# SnapRAID log and locate readers come from the package `probes` (../probes/snapraid_observe.py).
# The criteria file is read as CRITERIA. Budget `s2` of nix/mkspike.nix (research D14, about
# 25 min serial). A probe may reuse the definition with its own `name` and `testScript` and
# `criteria = null`. Scaffolding, not product code.
{ mkSpike
, name ? "s2"
, testScript ? ./test_script.py
, criteria ? ../../../../docs/phase0/criteria/S2.yaml
, budget ? "s2"
}:

mkSpike {
  inherit name testScript criteria budget;
  profile = "small";
  machines = [ "server" ];
  scriptPackages = {
    s2 = ./.;
    probes = ../probes;
  };
  extraModules.server = [
    ../../nix/snapper-configs.nix
    ../../nix/archive-lock.nix
    { spike.snapshots.enable = true; spike.tieredCache.enable = true; }
  ];
}
