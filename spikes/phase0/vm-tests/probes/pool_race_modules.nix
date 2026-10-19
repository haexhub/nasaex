# Probe-only modules of the pool race probe (task T051f, pool_race.nix), shared with the
# side-effect probe of its retrigger unit (task T051g, retrigger_effects.nix), so that both
# measure the same unit, rules and observers. Never part of nix/nodes.nix.
rec {
  poolMembers = [ "/dev/disk/by-id/virtio-pool0" "/dev/disk/by-id/virtio-pool1" ];

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

  # The forced race of T051f (pool_race.nix). Not-ready member: PROGRAM runs while the rules are
  # applied, before the links and the database. Ready member: RUN entries run in order after the
  # database is written, so this sleep follows the retrigger RUN of 64-btrfs.rules and precedes
  # the broadcast of the event to PID 1. Both match ACTION=="add" only.
  sleepRule = ready: what: seconds: { pkgs, ... }: {
    services.udev.extraRules = ''
      SUBSYSTEM=="block", ACTION=="add", ENV{ID_FS_LABEL}=="spikepool", ENV{ID_BTRFS_READY}=="${ready}", ${what}="${pkgs.coreutils}/bin/sleep ${toString seconds}"
    '';
  };
  delayNotReady = sleepRule "0" "PROGRAM";
  delayReady = sleepRule "1" "RUN+";
  delayBoth = [ (delayNotReady 1) (delayReady 2) ];

  # The delay sweep of T051g: the same two rules with delays read per boot from
  # /var/lib/probe-t051g-delay ("<not-ready s> <ready s>"); no file: no delay.
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

  # The observers of T051g (retrigger_observe.py), started at sysinit: a reader of one file
  # through the share path /mnt/cache and directly on /mnt/pool, and a mount table logger.
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
}
