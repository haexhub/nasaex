# Probe of the boot race of S1.11 (task T051a). A probe-only addition inside vm-tests/: it never
# writes a record. The S1 server alone (profile `large`, the disks and mount definitions of
# nix/nodes.nix, the tiered-cache mounts and the S1 share), rebooted N times; it measures per boot
# when each by-label device and each mount unit became active, and whether everything came up.
# Variants, run one at a time:
#   default            the mount options of nix/nodes.nix as they are (nofail,
#                      x-systemd.device-timeout=10s)
#   no-device-timeout  the same mounts without x-systemd.device-timeout (systemd's default
#                      device timeout applies); nothing else changes. The option lists below
#                      restate nodes.nix without that one option; the probe records /etc/fstab
#                      so a drift from nodes.nix shows in the result.
{ mkSpike }:

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
    name = "probe-boot-mounts" + suffix;
    profile = "large";
    machines = [ "server" ];
    budget = "probe-long";
    testScript = ./probe_boot_mounts.py;
    scriptPackages.probes = ./.;
    extraModules.server = [
      share
      { spike.tieredCache.enable = true; }
    ] ++ overrides;
  };
in
{
  default = probe "" [ ];
  no-device-timeout = probe "-no-device-timeout" [ withoutDeviceTimeout ];
}
