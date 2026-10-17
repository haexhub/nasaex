# Probe of the SMB attribute commands and smb.conf keys (task T039, plan open item O12; research
# D4, 3.6). A probe-only addition inside vm-tests/: it never writes a record. One VM test with the
# `server` and the `client` VM; the server shares the cache pool three times, so that one boot
# measures the Samba defaults and both explicit settings of the attribute keys (all of them are
# share-level parameters). Nothing here adopts a setting for S1.
{ mkSpike }:

let
  share = extra: {
    path = "/mnt/cache";
    "read only" = "no";
    "valid users" = "spike";
  } // extra;

  smb = {
    services.samba = {
      enable = true;
      openFirewall = true;
      settings = {
        # Samba's defaults for every attribute key.
        attrs-default = share { };
        attrs-on = share {
          "store dos attributes" = "yes";
          "ea support" = "yes";
        };
        attrs-off = share {
          "store dos attributes" = "no";
          "ea support" = "no";
        };
      };
    };
    users.users.spike.isNormalUser = true;
  };
in
mkSpike {
  name = "probe-smb-attrs";
  profile = "small";
  machines = [ "server" "client" ];
  budget = "probe";
  testScript = ./probe_smb_attrs.py;
  extraModules.server = [
    smb
    { spike.tieredCache.enable = true; }
  ];
}
