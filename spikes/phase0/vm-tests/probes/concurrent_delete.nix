# Probe of the concurrent-delete fault kind (task T037c, fault-points contract rule 6: probe
# first). A probe-only addition inside vm-tests/: it never writes a record. Two VM tests that
# differ only in the unlink parameter of the cache pool (task T028a): `default` passes no
# `func.unlink` option, `ff` sets the UNVERIFIED candidate `func.unlink=ff`. Neither adopts a
# setting. Run one at a time; each uses the `server` and the `client` VM.
{ mkSpike }:

let
  # One SMB share of the cache pool and one test user, nothing else.
  share = {
    services.samba = {
      enable = true;
      openFirewall = true;
      settings.cache = {
        path = "/mnt/cache";
        "read only" = "no";
        "valid users" = "spike";
      };
    };
    users.users.spike.isNormalUser = true;
  };

  probe = name: unlinkPolicy: mkSpike {
    inherit name;
    profile = "small";
    machines = [ "server" "client" ];
    budget = "probe";
    testScript = ./probe_concurrent_delete.py;
    extraModules.server = [
      share
      { spike.tieredCache = { enable = true; inherit unlinkPolicy; }; }
    ];
  };
in
{
  default = probe "probe-concurrent-delete" null;
  ff = probe "probe-concurrent-delete-ff" "ff";
}
