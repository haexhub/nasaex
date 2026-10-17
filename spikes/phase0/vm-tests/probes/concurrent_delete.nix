# Probe of the concurrent-delete fault kind (task T037c, fault-points contract rule 6: probe
# first). A probe-only addition inside vm-tests/: it never writes a record. Two VM tests that
# differ only in the unlink parameter of the cache pool (task T028a): `default` passes no
# `func.unlink` option, `ff` sets the UNVERIFIED candidate `func.unlink=ff`. Neither adopts a
# setting. Run one at a time; each uses the `server` and the `client` VM.
# `name` and `script` select the probe: T037c (the default) or the mid-copy delete of T037f.
# The test script is delete_probe_setup.py followed by `script`.
{ mkSpike
, name ? "probe-concurrent-delete"
, script ? ./probe_concurrent_delete.py
}:

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

  testScript = builtins.toFile "${name}.py"
    (builtins.readFile ./delete_probe_setup.py + builtins.readFile script);

  probe = suffix: unlinkPolicy: mkSpike {
    name = name + suffix;
    profile = "small";
    machines = [ "server" "client" ];
    budget = "probe";
    inherit testScript;
    extraModules.server = [
      share
      { spike.tieredCache = { enable = true; inherit unlinkPolicy; }; }
    ];
  };
in
{
  default = probe "" null;
  ff = probe "-ff" "ff";
}
