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
#                      in PROGRAM, after the ioctl and before udev writes that member's
#                      database (the deterministic reproduction of the race)
#   delay-device-opts  delay plus `device=` for both pool members on /mnt/pool (mitigation a)
#   delay-retrigger    delay plus a oneshot `udevadm trigger --action=add` of both pool members
#                      after `udevadm settle` (mitigation b)
#   delay-uuid         delay with /mnt/pool mounted by /dev/disk/by-uuid (mitigation c); the
#                      probe sets the pool's fsid to POOL_UUID with btrfstune after spike-mkfs
# Everything else is nix/nodes.nix as it is; the mitigations are measured, not adopted.
{ mkSpike }:

let
  poolUuid = "5f0a7c1e-0000-4000-8000-000000051f00"; # fixed, so fstab can name it
  poolMembers = [ "/dev/disk/by-id/virtio-pool0" "/dev/disk/by-id/virtio-pool1" ];
  nodesOptions = [ "nofail" "x-systemd.device-timeout=10s" ]; # nix/nodes.nix:48

  monitor = { pkgs, ... }: {
    systemd.services.probe-udev-monitor = {
      description = "Probe T051f: log block uevents";
      wantedBy = [ "sysinit.target" ];
      before = [ "systemd-udev-trigger.service" "shutdown.target" ];
      conflicts = [ "shutdown.target" ];
      unitConfig.DefaultDependencies = false;
      serviceConfig = {
        Type = "exec";
        ExecStart = "${pkgs.systemd}/bin/udevadm monitor --kernel --udev --property "
          + "--subsystem-match=block";
        StandardOutput = "file:/run/probe-udev-monitor.log";
      };
    };
  };

  cpus = n: { lib, ... }: { virtualisation.cores = lib.mkForce n; };

  delay = { pkgs, ... }: {
    services.udev.extraRules = ''
      SUBSYSTEM=="block", ACTION=="add", ENV{ID_FS_LABEL}=="spikepool", ENV{ID_BTRFS_READY}=="0", PROGRAM="${pkgs.coreutils}/bin/sleep 2"
    '';
  };

  deviceOptions = { lib, ... }: {
    virtualisation.fileSystems."/mnt/pool".options = lib.mkForce
      ([ "subvol=pool" ] ++ map (m: "device=${m}") poolMembers ++ nodesOptions);
  };

  retrigger = { pkgs, ... }: {
    systemd.services.probe-pool-retrigger = {
      description = "Probe T051f: re-add the pool members after udev settled";
      wantedBy = [ "local-fs.target" ];
      before = [ "local-fs.target" ];
      after = [ "systemd-udev-trigger.service" ];
      unitConfig.DefaultDependencies = false;
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      path = [ pkgs.systemd ];
      script = ''
        udevadm settle --timeout=30
        udevadm trigger --action=add --settle ${toString poolMembers}
      '';
    };
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
  delay-device-opts = variant "delay-device-opts" [ (cpus 2) delay deviceOptions ];
  delay-retrigger = variant "delay-retrigger" [ (cpus 2) delay retrigger ];
  delay-uuid = variant "delay-uuid" [ (cpus 2) delay byUuid ];
}
