# The S1 VM test (task T041): the tiered-cache share driven from a separate SMB client.
# Profile `large`; the `server` mounts the base pool and the cache pool (nix/tiered-cache.nix with
# its defaults) and shares the cache pool through one SMB share with one test user, the DOS
# attribute and extended attribute keys set explicitly (measured as required by the probe of task
# T039), nothing else; the `client` reaches it over the test vlan. The checks are the package `s1`
# of the test script (this directory); the criteria file is read as CRITERIA. Budget `s1` of
# nix/mkspike.nix (research D14, about 20 min serial). Scaffolding, not product code.
{ mkSpike }:

let
  share = {
    services.samba = {
      enable = true;
      openFirewall = true;
      settings.cache = {
        path = "/mnt/cache";
        "read only" = "no";
        "valid users" = "spike";
        "store dos attributes" = "yes";
        "ea support" = "yes";
      };
    };
    users.users.spike.isNormalUser = true;
  };
in
mkSpike {
  name = "s1";
  profile = "large";
  machines = [ "server" "client" ];
  budget = "s1";
  testScript = ./test_script.py;
  scriptPackages.s1 = ./.;
  criteria = ../../../../docs/phase0/criteria/S1.yaml;
  extraModules.server = [
    share
    { spike.tieredCache.enable = true; }
  ];
}
