# Probe of the side effects of the pool retrigger unit of T051f (task T051g). Probe-only, never
# writes a record. The S1 server as the pool race probe boots it (boot_mounts.nix: profile
# `large` with every labelled disk and mount of nix/nodes.nix, the tiered-cache mounts and the
# S1 share; 2 vCPUs; the udev monitor of T051f), plus two observers started at sysinit
# (`observe` of pool_race_modules.nix, retrigger_observe.py): a reader of one file through the
# share path /mnt/cache and directly on /mnt/pool, and a logger of the mount table under /mnt. The unit is `retrigger` of
# pool_race_modules.nix, the candidate of T051f, unchanged. The test script is the prelude
# (PROBE_MODE, ABSENT, retrigger_prelude.py), boot_probe_setup.py, retrigger_boot.py and
# probe_retrigger_effects.py.
# Variants, run one at a time:
#   plain, retrigger            normal boots without / with the unit (mode normal)
#   no-pool0, no-pool0-retrigger, no-pool1, no-pool1-retrigger
#                               the member is left out of the VM definition; the probe attaches
#                               it (QEMU_OPTS) for the formatting boot and the boot that writes
#                               the reader file only, then boots without it and hot-plugs it
#                               through the QEMU monitor after each boot (mode degraded)
#   sweep, sweep-retrigger      the delay-both reproduction of T051f with delays the probe sets
#                               per boot in /var/lib/probe-t051g-delay ("<not-ready s> <ready s>":
#                               PROGRAM sleep of the not-ready member's add event, RUN sleep of
#                               every ready add event, as delayNotReady and delayReady of
#                               pool_race.nix; `sweepDelays` of pool_race_modules.nix); no file:
#                               no delay (mode sweep)
# Everything else is nix/nodes.nix as it is; nothing here is adopted.
{ mkSpike }:

let
  inherit (import ./pool_race_modules.nix) monitor cpus retrigger observe without sweepDelays;

  variant = name: mode: absent: modules: (import ./boot_mounts.nix {
    inherit mkSpike;
    name = "probe-retrigger-effects-${name}";
    script = builtins.toFile "probe_retrigger_effects.py"
      (builtins.readFile ./retrigger_boot.py + builtins.readFile ./probe_retrigger_effects.py);
    extraServerModules = [ monitor (cpus 2) observe ] ++ modules;
    prelude = ''
      PROBE_MODE = "${mode}"
      ABSENT = ${if absent == null then "None" else "\"${absent}\""}
    '' + builtins.readFile ./retrigger_prelude.py;
  }).default;
in
{
  plain = variant "plain" "normal" null [ ];
  retrigger = variant "retrigger" "normal" null [ retrigger ];
  no-pool0 = variant "no-pool0" "degraded" "pool0" [ (without "pool0") ];
  no-pool0-retrigger = variant "no-pool0-retrigger" "degraded" "pool0" [ (without "pool0") retrigger ];
  no-pool1 = variant "no-pool1" "degraded" "pool1" [ (without "pool1") ];
  no-pool1-retrigger = variant "no-pool1-retrigger" "degraded" "pool1" [ (without "pool1") retrigger ];
  sweep = variant "sweep" "sweep" null [ sweepDelays ];
  sweep-retrigger = variant "sweep-retrigger" "sweep" null [ sweepDelays retrigger ];
}
