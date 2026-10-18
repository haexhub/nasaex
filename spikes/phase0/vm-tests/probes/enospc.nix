# The ENOSPC probes (tasks T051b, T051c and T051h). Probe-only additions inside vm-tests/: they
# never write a record. The S1 topology (profile `large`, `server` and `client`), the tiered-cache
# mounts with their defaults and the S1 share `cache`; in addition a control share `poolctl`
# served by the same Samba directly from the pool branch /mnt/pool (no mergerfs), and
# inotify-tools on the server to watch the archive branches. The S1 criteria give the sizes
# (tables.out_of_space). `name` and `script` select the probe: the control probe of T051b (the
# default), the moveonenospc probe of T051c or the retry timeline probe of T051h. The test
# script is enospc_probe_setup.py followed by `script`. Run one at a time.
{ mkSpike
, name ? "probe-enospc-control"
, script ? ./probe_enospc_control.py
}:

let
  # The keys of the S1 share (vm-tests/s1/default.nix) for both shares.
  share = path: {
    inherit path;
    "read only" = "no";
    "valid users" = "spike";
    "store dos attributes" = "yes";
    "ea support" = "yes";
  };

  server = { pkgs, ... }: {
    services.samba = {
      enable = true;
      openFirewall = true;
      settings = {
        cache = share "/mnt/cache";
        poolctl = share "/mnt/pool";
      };
    };
    users.users.spike.isNormalUser = true;
    environment.systemPackages = [ pkgs.inotify-tools ];
    spike.tieredCache.enable = true;
  };
in
mkSpike {
  inherit name;
  profile = "large";
  machines = [ "server" "client" ];
  budget = "probe-long";
  testScript = builtins.toFile "${name}.py"
    (builtins.readFile ./enospc_probe_setup.py + builtins.readFile script);
  scriptPackages = {
    s1 = ../s1;
    probes = ./.;
  };
  criteria = ../../../../docs/phase0/criteria/S1.yaml;
  extraModules.server = [ server ];
}
