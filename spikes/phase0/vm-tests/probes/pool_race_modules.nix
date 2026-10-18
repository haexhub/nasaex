# Probe-only modules of the pool race probe (task T051f, pool_race.nix), shared with the
# side-effect probe of its retrigger unit (task T051g, retrigger_effects.nix) so that both
# measure the same unit. Never part of nix/nodes.nix.
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
}
