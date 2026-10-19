# Probes of the boot race of S1.11 (tasks T051a and T051d). Probe-only additions inside vm-tests/:
# they never write a record. The S1 server alone (profile `large`, the disks and mount definitions
# of nix/nodes.nix, the tiered-cache mounts and the S1 share), rebooted N times; they measure per
# boot when each by-label device and each mount unit became active, and whether everything came
# up. `name` and `script` select the probe: the idle reboots of T051a (the default) or the reboots
# after the S1 heavy IO of T051d. The test script is boot_probe_setup.py followed by `script`.
# `extraServerModules` adds probe-only modules to the server (the pool race probe of T051f,
# pool_race.nix); empty by default, so T051a and T051d are unchanged. `prelude` is Python put
# before boot_probe_setup.py, run before the first start (the side-effect probe of T051g,
# retrigger_effects.nix, names its mode and attaches a disk its VM definition leaves out there);
# empty by default.
# Variants, run one at a time:
#   default            the mount options of nix/nodes.nix as they are (nofail,
#                      x-systemd.device-timeout=10s)
#   no-device-timeout  the same mounts without x-systemd.device-timeout (systemd's default
#                      device timeout applies); nothing else changes. The option lists below
#                      restate nodes.nix without that one option; the probe records /etc/fstab
#                      so a drift from nodes.nix shows in the result.
{ mkSpike
, name ? "probe-boot-mounts"
, script ? ./probe_boot_mounts.py
, extraServerModules ? [ ]
, prelude ? ""
}:

let
  archive = [ "arch0" "arch1" "arch2" ]; # the archive serials of nix/profiles.nix

  # The S1 share (vm-tests/s1/default.nix), so that the boot starts the same units as S1.11.
  share = {
    services.samba = {
      enable = true;
      openFirewall = true;
      settings.cache = {
        path = "/mnt/cache";
        "read only" = "no";
        "valid users" = "spike";
        "store dos attributes" = "yes";
        "ea support" = "yes";
      };
    };
    users.users.spike.isNormalUser = true;
  };

  withoutDeviceTimeout = { lib, ... }: {
    virtualisation.fileSystems = lib.mapAttrs (_: options: { options = lib.mkForce options; }) ({
      "/mnt/pool" = [ "subvol=pool" "nofail" ];
      "/mnt/parity" = [ "nofail" ];
    }
    // lib.genAttrs (map (s: "/mnt/archive/${s}") archive) (_: [ "subvol=data" "nofail" ])
    // lib.genAttrs (map (s: "/mnt/archive-content/${s}") archive) (_: [ "subvol=content" "nofail" ]));
  };

  probe = suffix: overrides: mkSpike {
    name = name + suffix;
    profile = "large";
    machines = [ "server" ];
    budget = "probe-long";
    testScript = builtins.toFile "${name}${suffix}.py"
      (prelude + builtins.readFile ./boot_probe_setup.py + builtins.readFile script);
    scriptPackages = {
      probes = ./.;
      s1 = ../s1;
    };
    extraModules.server = [
      share
      { spike.tieredCache.enable = true; }
      # The pool retrigger unit that nix/nodes.nix adds to every spike server (task T028b) is
      # switched off: these probes measured the boot without it (or with their own variant of
      # it, pool_race_modules.nix), and their committed results stay reproducible.
      { systemd.services.pool-retrigger.enable = false; }
    ] ++ extraServerModules ++ overrides;
  };
in
{
  default = probe "" [ ];
  no-device-timeout = probe "-no-device-timeout" [ withoutDeviceTimeout ];
}
