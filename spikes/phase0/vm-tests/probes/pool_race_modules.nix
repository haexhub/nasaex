# Probe-only modules of the pool race probe (task T051f, pool_race.nix), shared with the
# side-effect probe of its retrigger unit (task T051g, retrigger_effects.nix) and with the probe
# of the retrigger variants (task T051i, retrigger_v2.nix), so that all of them measure the same
# units, rules and observers. Never part of nix/nodes.nix.
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

  # The candidate of T051f, measured by T051g; T051i keeps it as the reference ("orig").
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

  # T051i variants of that unit, under the same unit name so that the boot observation of T051g
  # reads them unchanged. Both trigger by property instead of by path, as line 15 of systemd's
  # 64-btrfs.rules does (`udevadm trigger -s block -p ID_BTRFS_READY=0`, default action change):
  # only a block device whose udev database says ID_BTRFS_READY=0 gets an event, and a missing
  # pool member is simply not enumerated. `action` is `add` or `change`.
  # U1: only the trigger changes; the settle stays that of the original (30 s, failing the unit).
  retriggerByProperty = action: { pkgs, ... }: {
    systemd.services.probe-pool-retrigger = {
      description = "Probe T051i U1: retrigger not-ready btrfs devices (${action}) after udev settled";
      wantedBy = [ "local-fs.target" ];
      before = [ "local-fs.target" ];
      after = [ "systemd-udev-trigger.service" ];
      unitConfig.DefaultDependencies = false;
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      path = [ pkgs.systemd ];
      script = ''
        udevadm settle --timeout=30
        udevadm trigger --settle --action=${action} --subsystem-match=block --property-match=ID_BTRFS_READY=0
      '';
    };
  };

  # U2: U1 with every wait bounded: settle at most 5 s, the trigger's own settle at most 4 s,
  # neither failure fails the script (`|| echo`), and TimeoutStartSec=10s as the backstop, so the
  # unit holds local-fs.target at most about 9 s and exits 0 unless that backstop fires.
  retriggerBounded = action: { pkgs, ... }: {
    systemd.services.probe-pool-retrigger = {
      description = "Probe T051i U2: bounded retrigger of not-ready btrfs devices (${action})";
      wantedBy = [ "local-fs.target" ];
      before = [ "local-fs.target" ];
      after = [ "systemd-udev-trigger.service" ];
      unitConfig.DefaultDependencies = false;
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; TimeoutStartSec = "10s"; };
      path = [ pkgs.systemd pkgs.coreutils ];
      script = ''
        udevadm settle --timeout=5 || echo "udev queue not empty after 5 s (exit $?), triggering anyway"
        timeout 4 udevadm trigger --settle --action=${action} --subsystem-match=block \
          --property-match=ID_BTRFS_READY=0 || echo "trigger not settled within 4 s (exit $?)"
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
