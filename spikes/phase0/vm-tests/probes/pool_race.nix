# Probe of the two-device pool race of S1.11 (task T051f). Probe-only, never writes a record.
# The S1 server of boot_mounts.nix as T051a booted it (profile `large` with every labelled disk
# and every mount of nix/nodes.nix, the tiered-cache mounts and the S1 share), plus a udev
# monitor that logs every block event (kernel and udev, with properties) from before the
# coldplug trigger to /run/probe-udev-monitor.log. The test script is boot_probe_setup.py
# followed by probe_pool_race.py. Variants, run one at a time:
#   cpu2               the options of nix/nodes.nix, 2 vCPUs (the S1 server size)
#   cpu4               the same with 4 vCPUs
#   delay              cpu2 plus a udev rule that, on the add event of the pool member whose
#                      `btrfs ready` saw an incomplete volume (ID_BTRFS_READY=0), sleeps 2 s
#                      in PROGRAM, after the ioctl and before udev links the member and writes
#                      its database: the not-ready member misses the retrigger
#   delay-both         cpu2 plus that rule with 1 s, and a RUN sleep of 2 s on the add event of
#                      the member that completed the volume (ID_BTRFS_READY=1), after its
#                      retrigger RUN and before udev announces the event to PID 1: the
#                      not-ready member misses the retrigger and owns the link when PID 1
#                      handles the ready member's event (the deterministic reproduction)
#   delay-both-device-opts  delay-both plus `device=` for both pool members on /mnt/pool
#                      (mitigation a)
#   delay-both-retrigger    delay-both plus a oneshot `udevadm trigger --action=add` of both
#                      pool members after `udevadm settle` (mitigation b)
#   delay-both-uuid    delay-both with /mnt/pool mounted by /dev/disk/by-uuid (mitigation c);
#                      the probe sets the pool's fsid to that UUID with btrfstune after spike-mkfs
# Everything else is nix/nodes.nix as it is; the mitigations are measured, not adopted.
{ mkSpike }:

let
  # Shared with the side-effect probe of T051g (retrigger_effects.nix).
  inherit (import ./pool_race_modules.nix) poolMembers monitor cpus retrigger;
  poolUuid = "5f0a7c1e-0000-4000-8000-000000051f00"; # fixed, so fstab can name it
  nodesOptions = [ "nofail" "x-systemd.device-timeout=10s" ]; # nix/nodes.nix:48

  sleepRule = ready: what: seconds: { pkgs, ... }: {
    services.udev.extraRules = ''
      SUBSYSTEM=="block", ACTION=="add", ENV{ID_FS_LABEL}=="spikepool", ENV{ID_BTRFS_READY}=="${ready}", ${what}="${pkgs.coreutils}/bin/sleep ${toString seconds}"
    '';
  };
  # Not-ready member: PROGRAM runs while the rules are applied, before the links and the database.
  delayNotReady = sleepRule "0" "PROGRAM";
  # Ready member: RUN entries run in order after the database is written, so this sleep follows
  # the retrigger RUN of 64-btrfs.rules and precedes the broadcast of the event to PID 1.
  delayReady = sleepRule "1" "RUN+";
  delay = delayNotReady 2;
  delayBoth = [ (delayNotReady 1) (delayReady 2) ];

  deviceOptions = { lib, ... }: {
    virtualisation.fileSystems."/mnt/pool".options = lib.mkForce
      ([ "subvol=pool" ] ++ map (m: "device=${m}") poolMembers ++ nodesOptions);
  };

  byUuid = { lib, ... }: {
    virtualisation.fileSystems."/mnt/pool".device = lib.mkForce "/dev/disk/by-uuid/${poolUuid}";
  };

  variant = name: modules: (import ./boot_mounts.nix {
    inherit mkSpike;
    name = "probe-pool-race-${name}";
    script = ./probe_pool_race.py;
    extraServerModules = [ monitor ] ++ modules;
  }).default;
in
{
  cpu2 = variant "cpu2" [ (cpus 2) ];
  cpu4 = variant "cpu4" [ (cpus 4) ];
  delay = variant "delay" [ (cpus 2) delay ];
  delay-both = variant "delay-both" ([ (cpus 2) ] ++ delayBoth);
  delay-both-device-opts = variant "delay-both-device-opts" ([ (cpus 2) deviceOptions ] ++ delayBoth);
  delay-both-retrigger = variant "delay-both-retrigger" ([ (cpus 2) retrigger ] ++ delayBoth);
  delay-both-uuid = variant "delay-both-uuid" ([ (cpus 2) byUuid ] ++ delayBoth);
}
