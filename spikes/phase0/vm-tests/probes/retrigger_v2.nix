# Probe of the retrigger variants and of the shutdown difference of T051g (task T051i).
# Probe-only, never writes a record. The S1 server of T051g (boot_mounts.nix with the udev
# monitor of T051f, 2 vCPUs and the observers of T051g), plus probe-shutdown-capture.service:
# ordered before mnt-base.mount and mnt-cache.mount, so systemd stops it after both at shutdown,
# and its ExecStop records the mergerfs servers still running then (shutdown_observe.py). The
# test script is the prelude (PROBE_MODE, ABSENT, retrigger_prelude.py), boot_probe_setup.py,
# retrigger_boot.py and probe_retrigger_v2.py. The retrigger unit of a variant (all named
# probe-pool-retrigger.service, defined in pool_race_modules.nix):
#   plain                       none
#   orig                        `retrigger`, the candidate of T051f measured by T051g
#   u1-add, u1-change           `retriggerByProperty`: the trigger by property match
#   u2-add, u2-change           `retriggerBounded`: the same with every wait bounded
# Variants, run one at a time (<u> one of the above):
#   <u>                 normal boots (mode normal)
#   no-pool0-<u>, no-pool1-<u>  the member left out of the VM definition (mode degraded, T051g)
#   delay-both-<u>      the forced race of T051f, `delayBoth` (mode normal)
#   sweep-<u>           the delay sweep of T051g, `sweepDelays` (mode sweep)
# Everything else is nix/nodes.nix as it is; nothing here is adopted.
{ mkSpike }:

let
  modules = import ./pool_race_modules.nix;
  inherit (modules) monitor cpus observe without delayBoth sweepDelays;

  shutdownCapture = { pkgs, ... }:
    let
      tool = pkgs.writeShellScriptBin "probe-t051i-observe" ''
        exec ${pkgs.python3}/bin/python3 ${./shutdown_observe.py} "$@"
      '';
    in
    {
      environment.systemPackages = [ tool ];
      systemd.services.probe-shutdown-capture = {
        description = "Probe T051i: capture the mergerfs servers after their mounts stopped";
        wantedBy = [ "multi-user.target" ];
        # Started before the mergerfs mounts, so its stop job runs after both of theirs.
        before = [ "mnt-base.mount" "mnt-cache.mount" "shutdown.target" ];
        conflicts = [ "shutdown.target" ];
        unitConfig.DefaultDependencies = false;
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStart = "${pkgs.coreutils}/bin/true";
          ExecStop = "${tool}/bin/probe-t051i-observe capture /var/lib/probe-t051i/capture.jsonl";
          StandardOutput = "journal+console";
        };
      };
    };

  units = {
    plain = [ ];
    orig = [ modules.retrigger ];
    u1-add = [ (modules.retriggerByProperty "add") ];
    u1-change = [ (modules.retriggerByProperty "change") ];
    u2-add = [ (modules.retriggerBounded "add") ];
    u2-change = [ (modules.retriggerBounded "change") ];
  };

  variant = name: mode: absent: extra: {
    inherit name;
    value = (import ./boot_mounts.nix {
      inherit mkSpike;
      name = "probe-retrigger-v2-${name}";
      script = builtins.toFile "probe_retrigger_v2.py"
        (builtins.readFile ./retrigger_boot.py + builtins.readFile ./probe_retrigger_v2.py);
      extraServerModules = [ monitor (cpus 2) observe shutdownCapture ] ++ extra;
      prelude = ''
        PROBE_MODE = "${mode}"
        ABSENT = ${if absent == null then "None" else "\"${absent}\""}
      '' + builtins.readFile ./retrigger_prelude.py;
    }).default;
  };

  perUnit = f: map (u: f u units.${u}) (builtins.attrNames units);
in
builtins.listToAttrs (
  perUnit (u: m: variant u "normal" null m)
  ++ perUnit (u: m: variant "no-pool0-${u}" "degraded" "pool0" ([ (without "pool0") ] ++ m))
  ++ perUnit (u: m: variant "no-pool1-${u}" "degraded" "pool1" ([ (without "pool1") ] ++ m))
  ++ perUnit (u: m: variant "delay-both-${u}" "normal" null (delayBoth ++ m))
  ++ perUnit (u: m: variant "sweep-${u}" "sweep" null ([ sweepDelays ] ++ m))
)
