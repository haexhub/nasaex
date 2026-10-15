# Cheap checks that need no KVM (run by `nix flake check` and the static CI job).
# `src` is the spikes/phase0 directory of the flake.
{ pkgs, src }:

{
  # Nix formatter, Python formatter and Python lint, all in check mode.
  formatting = pkgs.runCommand "phase0-formatting"
    {
      nativeBuildInputs = [ pkgs.nixpkgs-fmt pkgs.ruff ];
    }
    ''
      cp -r ${src} work
      chmod -R u+w work
      cd work
      nixpkgs-fmt --check $(find . -name '*.nix')
      ruff format --check --no-cache .
      ruff check --no-cache .
      touch $out
    '';
}
