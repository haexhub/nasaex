# Probe of the side effects of the pool retrigger unit of T051f (task T051g). Probe-only, never
# writes a record. The S1 server as the pool race probe boots it (boot_mounts.nix: profile
# `large` with every labelled disk and mount of nix/nodes.nix, the tiered-cache mounts and the
# S1 share; 2 vCPUs; the udev monitor of T051f), plus two observers started at sysinit
# (retrigger_observe.py): a reader of one file through the share path /mnt/cache and directly
# on /mnt/pool, and a logger of the mount table under /mnt. The unit is `retrigger` of
# pool_race_modules.nix, the candidate of T051f, unchanged. The test script is the prelude
# (PROBE_MODE, ABSENT, retrigger_prelude.py), boot_probe_setup.py, probe_retrigger_effects.py.
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
#                               pool_race.nix); no file: no delay (mode sweep)
# Everything else is nix/nodes.nix as it is; nothing here is adopted.
{ mkSpike }:

let
  inherit (import ./pool_race_modules.nix) monitor cpus retrigger;

  observe = { pkgs, ... }:
    let
      tool = pkgs.writeShellScriptBin "probe-t051g-observe" ''
        exec ${pkgs.python3}/bin/python3 ${./retrigger_observe.py} "$@"
      '';
      early = what: args: {
        description = "Probe T051g: ${what}";
        wantedBy = [ "sysinit.target" ];
        before = [ "shutdown.target" ];
        conflicts = [ "shutdown.target" ];
        unitConfig.DefaultDependencies = false;
        serviceConfig = {
          Type = "exec";
          ExecStart = "${tool}/bin/probe-t051g-observe ${args}";
        };
      };
      file = "probe-t051g/reader.bin";
    in
    {
      environment.systemPackages = [ tool ];
      systemd.services.probe-reader = early "read a file through the share path"
        "read /run/probe-reader.log /mnt/cache/${file} /mnt/pool/${file}";
      systemd.services.probe-mountlog = early "log the mount table" "mounts /run/probe-mounts.log";
    };

  # The emptyDiskImages entries of nix/nodes.nix (diskImage) without the disk `serial`.
  without = serial: { lib, ... }:
    let
      disks = (import ../../nix/profiles.nix { inherit lib; }).large.disks;
    in
    {
      virtualisation.emptyDiskImages = lib.mkForce (map
        (d: {
          size = d.sizeMiB;
          driveConfig.deviceExtraOpts = { inherit (d) serial; id = "disk-${d.serial}"; };
        })
        (lib.filter (d: d.serial != serial) disks));
    };

  sweepDelays = { pkgs, ... }:
    let
      sleep = pkgs.writeShellScript "probe-t051g-delay" ''
        # $1: 1 = the not-ready delay, 2 = the ready delay of /var/lib/probe-t051g-delay
        read -r notready ready < /var/lib/probe-t051g-delay || exit 0
        if [ "$1" = 1 ]; then seconds=$notready; else seconds=$ready; fi
        exec ${pkgs.coreutils}/bin/sleep "$seconds"
      '';
    in
    {
      services.udev.extraRules = ''
        SUBSYSTEM=="block", ACTION=="add", ENV{ID_FS_LABEL}=="spikepool", ENV{ID_BTRFS_READY}=="0", PROGRAM="${sleep} 1"
        SUBSYSTEM=="block", ACTION=="add", ENV{ID_FS_LABEL}=="spikepool", ENV{ID_BTRFS_READY}=="1", RUN+="${sleep} 2"
      '';
    };

  variant = name: mode: absent: modules: (import ./boot_mounts.nix {
    inherit mkSpike;
    name = "probe-retrigger-effects-${name}";
    script = ./probe_retrigger_effects.py;
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
