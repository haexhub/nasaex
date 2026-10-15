# Development shell of the Phase 0 spikes: Nix tooling, Python with the test
# dependencies of spikectl and testlib, and QEMU tools for debugging.
{ pkgs, pythonEnv }:

pkgs.mkShell {
  packages = [
    pythonEnv
    pkgs.nixpkgs-fmt
    pkgs.qemu_kvm
    pkgs.ruff
    pkgs.shellcheck
  ];
}
