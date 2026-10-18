{
  description = "Phase 0 storage spikes: throwaway scaffolding (no product code)";

  # One pin for all of Phase 0 (research D1): full revision of branch nixos-26.05.
  # The committed flake.lock (narHash) is the reproducibility anchor.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      inherit (pkgs) lib;

      pythonEnv = pkgs.python3.withPackages (ps: [ ps.pytest ps.pyyaml ps.jsonschema ]);
      spikectlSrc = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [ ./spikectl ./testlib ];
      };

      profiles = import ./nix/profiles.nix { inherit lib; };
      snapraidBtrfs = import ./nix/snapraid-btrfs.nix { inherit pkgs; };
      guestScripts = import ./nix/guest-scripts.nix { inherit pkgs; };
      mkSpike = import ./nix/mkspike.nix { inherit pkgs profiles snapraidBtrfs guestScripts; };
      apps = import ./nix/apps.nix { inherit pkgs pythonEnv spikectlSrc; };

      # Probe-only VM tests: they never write records.
      probeFaults = import ./vm-tests/probes/faults.nix { inherit mkSpike; };
      probeDelete = import ./vm-tests/probes/concurrent_delete.nix { inherit mkSpike; };
      probeSmbAttrs = import ./vm-tests/probes/smb_attrs.nix { inherit mkSpike; };
      probeBootMounts = import ./vm-tests/probes/boot_mounts.nix { inherit mkSpike; };
      probeBootLoad = import ./vm-tests/probes/boot_mounts.nix {
        inherit mkSpike;
        name = "probe-boot-load";
        script = ./vm-tests/probes/probe_boot_load.py;
      };
      # The two-device pool race of S1.11 (T051f): one driver per variant of pool_race.nix.
      probePoolRace = import ./vm-tests/probes/pool_race.nix { inherit mkSpike; };
      # Side effects of the pool retrigger unit of T051f (T051g): one driver per variant.
      probeRetriggerEffects = import ./vm-tests/probes/retrigger_effects.nix { inherit mkSpike; };
      probeEnospcControl = import ./vm-tests/probes/enospc.nix { inherit mkSpike; };
      probeNcMoveOnEnospc = import ./vm-tests/probes/enospc.nix {
        inherit mkSpike;
        name = "probe-nc-moveonenospc";
        script = ./vm-tests/probes/probe_nc_moveonenospc.py;
      };
      # The spikes' VM tests.
      s1 = import ./vm-tests/s1/default.nix { inherit mkSpike; };

      probeDeleteMidCopy = import ./vm-tests/probes/concurrent_delete.nix {
        inherit mkSpike;
        name = "probe-concurrent-delete-mid-copy";
        script = ./vm-tests/probes/probe_concurrent_delete_mid_copy.py;
      };

      app = pkg: { type = "app"; program = lib.getExe pkg; };
      placeholders = lib.genAttrs
        [ "s2" "s3" "s4" "s5" "x1" "controls" "suite" ]
        apps.placeholder;
    in
    {
      checks.${system} = import ./nix/checks.nix
        {
          inherit pkgs pythonEnv spikectlSrc;
          src = ./.;
          repoRoot = ../..;
        } // {
        tiered-cache-layout = import ./tests/tiered_cache_layout.nix { inherit pkgs mkSpike; };
      };

      apps.${system} = lib.mapAttrs (_: app) (placeholders // {
        spikectl = apps.spikectl;
        # `nix run ./spikes/phase0#s1 -- --mode shakedown|record [--only <id>]` (spikectl run).
        s1 = apps.spikeRun "S1";
        probe-faults = apps.runDriver "probe-faults" probeFaults.driver;
        probe-concurrent-delete = apps.runDriver "probe-concurrent-delete" probeDelete.default.driver;
        probe-concurrent-delete-ff = apps.runDriver "probe-concurrent-delete-ff" probeDelete.ff.driver;
        probe-smb-attrs = apps.runDriver "probe-smb-attrs" probeSmbAttrs.driver;
        probe-concurrent-delete-mid-copy =
          apps.runDriver "probe-concurrent-delete-mid-copy" probeDeleteMidCopy.default.driver;
        probe-concurrent-delete-mid-copy-ff =
          apps.runDriver "probe-concurrent-delete-mid-copy-ff" probeDeleteMidCopy.ff.driver;
        probe-boot-mounts = apps.runDriver "probe-boot-mounts" probeBootMounts.default.driver;
        probe-boot-mounts-no-device-timeout = apps.runDriver "probe-boot-mounts-no-device-timeout"
          probeBootMounts.no-device-timeout.driver;
        # PROBE_HOST_IO=1 adds the host IO load of variant D2 (probe_boot_load.py).
        probe-boot-load = apps.runDriver "probe-boot-load" probeBootLoad.default.driver;
        probe-boot-load-no-device-timeout = apps.runDriver "probe-boot-load-no-device-timeout"
          probeBootLoad.no-device-timeout.driver;
        probe-enospc-control = apps.runDriver "probe-enospc-control" probeEnospcControl.driver;
        probe-nc-moveonenospc = apps.runDriver "probe-nc-moveonenospc" probeNcMoveOnEnospc.driver;
      } // lib.mapAttrs'
        (v: p: lib.nameValuePair "probe-pool-race-${v}" (apps.runDriver "probe-pool-race-${v}" p.driver))
        probePoolRace
        // lib.mapAttrs'
        (v: p: lib.nameValuePair "probe-retrigger-effects-${v}"
          (apps.runDriver "probe-retrigger-effects-${v}" p.driver))
        probeRetriggerEffects);

      packages.${system} = {
        driver-s1 = s1.driver;
        driver-interactive-s1 = s1.driverInteractive;
        driver-probe-faults = probeFaults.driver;
        driver-probe-concurrent-delete = probeDelete.default.driver;
        driver-probe-concurrent-delete-ff = probeDelete.ff.driver;
        driver-probe-smb-attrs = probeSmbAttrs.driver;
        driver-probe-concurrent-delete-mid-copy = probeDeleteMidCopy.default.driver;
        driver-probe-concurrent-delete-mid-copy-ff = probeDeleteMidCopy.ff.driver;
        driver-probe-boot-mounts = probeBootMounts.default.driver;
        driver-probe-boot-mounts-no-device-timeout = probeBootMounts.no-device-timeout.driver;
        driver-probe-boot-load = probeBootLoad.default.driver;
        driver-probe-boot-load-no-device-timeout = probeBootLoad.no-device-timeout.driver;
        driver-probe-enospc-control = probeEnospcControl.driver;
        driver-probe-nc-moveonenospc = probeNcMoveOnEnospc.driver;
      }
      // lib.mapAttrs' (v: p: lib.nameValuePair "driver-probe-pool-race-${v}" p.driver) probePoolRace
      // lib.mapAttrs'
        (v: p: lib.nameValuePair "driver-probe-retrigger-effects-${v}" p.driver)
        probeRetriggerEffects
      # Cached test drivers of the spikes; US2 to US5 and X1 replace these placeholders.
      // lib.genAttrs
        [ "driver-s2" "driver-s3" "driver-s4" "driver-s5" "driver-x1" ]
        apps.placeholder;

      devShells.${system}.default = import ./nix/devshell.nix { inherit pkgs pythonEnv; };
      formatter.${system} = pkgs.nixpkgs-fmt;
    };
}
