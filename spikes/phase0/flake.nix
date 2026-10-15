{
  description = "Phase 0 storage spikes: throwaway scaffolding (no product code)";

  # One pin for all of Phase 0 (research D1): full revision of branch nixos-26.05.
  # The committed flake.lock (narHash) is the reproducibility anchor.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/a033e66ab94a6bedf2c3d58d93a36319d3cbbbaf";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      pythonEnv = pkgs.python3.withPackages (ps: [ ps.pytest ps.pyyaml ps.jsonschema ]);
    in
    {
      # Placeholders; later tasks fill in the remaining checks, apps and packages.
      checks.${system} = import ./nix/checks.nix { inherit pkgs; src = ./.; };
      apps.${system} = { };
      packages.${system} = { };
      devShells.${system}.default = import ./nix/devshell.nix { inherit pkgs pythonEnv; };
      formatter.${system} = pkgs.nixpkgs-fmt;
    };
}
