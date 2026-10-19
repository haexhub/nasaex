# Evaluation check of the pool retrigger unit (task T028b, spec Clarifications 2026-10-19, S1.11
# reboots). No VM runs: it reads the evaluated `server` node of a spike and of two boot probes
# and fails the build with every mismatch.
#   - the server of every spike (nix/nodes.nix) carries pool-retrigger.service with exactly the
#     settings of `retriggerBounded "change"` (vm-tests/probes/pool_race_modules.nix), the unit
#     probe T051i measured as variant U2 with action change: the settings listed below, and the
#     same values of every one of them as the unit of that probe (retrigger_v2.nix, u2-change)
#   - the server of the boot probe probe-boot-mounts (vm-tests/probes/boot_mounts.nix, on which
#     the boot probes of T051a, T051d, T051f, T051g and T051i build) has no such unit, so their
#     committed results stay reproducible and their own retrigger variants never run beside it
{ pkgs, mkSpike }:

let
  inherit (pkgs) lib;

  serverOf = spike: spike.test.config.nodes.server;
  spike = serverOf (mkSpike {
    name = "pool-retrigger";
    profile = "large";
    machines = [ "server" ];
    budget = "probe";
    testScript = builtins.toFile "unused.py" "";
  });
  probe = serverOf (import ../vm-tests/probes/boot_mounts.nix { inherit mkSpike; }).default;
  measured = serverOf (import ../vm-tests/probes/retrigger_v2.nix { inherit mkSpike; }).u2-change;

  unit = spike.systemd.services.pool-retrigger or null;
  reference = measured.systemd.services.probe-pool-retrigger;

  settings = u: {
    inherit (u) enable wantedBy before after path script;
    DefaultDependencies = u.unitConfig.DefaultDependencies or null;
    Type = u.serviceConfig.Type or null;
    RemainAfterExit = u.serviceConfig.RemainAfterExit or null;
    TimeoutStartSec = u.serviceConfig.TimeoutStartSec or null;
  };
  listed = {
    enable = true;
    wantedBy = [ "local-fs.target" ];
    before = [ "local-fs.target" ];
    after = [ "systemd-udev-trigger.service" ];
    DefaultDependencies = false;
    Type = "oneshot";
    RemainAfterExit = true;
    TimeoutStartSec = "10s";
  };

  expect = name: got: want: lib.optional (got != want)
    "${name}: got ${builtins.toJSON got}, want ${builtins.toJSON want}";
  compare = what: got: want:
    lib.concatLists (lib.mapAttrsToList (key: value: expect "${what} ${key}" got.${key} value) want);

  probeUnit = probe.systemd.units."pool-retrigger.service" or null;

  failures = (
    if unit == null then [ "the spike server has no pool-retrigger.service" ]
    else
      compare "pool-retrigger.service" (settings unit) listed
        ++ compare "pool-retrigger.service against probe T051i u2-change" (settings unit)
        (settings reference)
        # The unit's own path entries; NixOS appends its default service path after them.
        ++ expect "pool-retrigger.service path names" (lib.take 2 (map lib.getName unit.path))
        [ "systemd" "coreutils" ]
  )
  ++ lib.optional (probeUnit != null && probeUnit.enable)
    "the boot probe server probe-boot-mounts carries pool-retrigger.service";
in
pkgs.runCommand "phase0-pool-retrigger" { } (
  if failures == [ ] then "touch $out"
  else ''
    echo ${lib.escapeShellArg (lib.concatStringsSep "\n" failures)} >&2
    exit 1
  ''
)
