# Runnable entry points of the flake: spikectl, the driver runner and placeholders.
{ pkgs, pythonEnv, spikectlSrc }:

{
  spikectl = pkgs.writeShellApplication {
    name = "spikectl";
    runtimeInputs = [ pythonEnv pkgs.git ];
    text = ''
      PYTHONPATH=${spikectlSrc} exec python -m spikectl.cli "$@"
    '';
  };

  # Runs a cached test driver OUTSIDE the nix sandbox (research D2) with a fresh machine state
  # on disk. The driver keeps machine state (multi-GiB qcow2 images) below XDG_RUNTIME_DIR,
  # which is usually a tmpfs, so it is pointed at the state directory instead. AF_UNIX socket
  # paths (and the vde network plug, which resolves symlinks) are limited to 107 bytes, so the
  # state directory must have a short real path: SPIKE_STATE_DIR overrides the default
  # spikes/phase0/state of a checkout whose path is too deep.
  runDriver = name: driver: pkgs.writeShellApplication {
    name = "run-${name}";
    runtimeInputs = [ pkgs.git pkgs.coreutils ];
    text = ''
      root=$(git rev-parse --show-toplevel)
      state=''${SPIKE_STATE_DIR:-$root/spikes/phase0/state}/${name}
      if [ "''${#state}" -gt 70 ]; then
        echo "state directory path is too long for AF_UNIX sockets (''${#state} > 70): $state" >&2
        echo "set SPIKE_STATE_DIR to a shorter directory on a disk, not a tmpfs" >&2
        exit 1
      fi
      rm -rf "$state"
      mkdir -p "$state/run" "$state/out"
      cd "$state/out"
      export XDG_RUNTIME_DIR=$state/run
      # With a display set the driver starts QEMU with a window; the test QEMU has no GUI.
      unset DISPLAY WAYLAND_DISPLAY
      exec ${driver}/bin/nixos-test-driver --output_directory "$state/out" \
        --junit-xml "$state/out/junit.xml" "$@"
    '';
  };

  # Outputs that later phases fill in.
  placeholder = name: pkgs.writeShellApplication {
    inherit name;
    text = ''
      echo "${name} is a placeholder: the spike that fills it in is not implemented yet" >&2
      exit 1
    '';
  };
}
