# Cheap checks that need no KVM (run by `nix flake check` and the static CI job).
#   src          the spikes/phase0 directory of the flake
#   repoRoot     the repository root (criteria, records and contracts live outside spikes/)
#   spikectlSrc  spikectl and testlib sources
# The git-based groups of `spikectl verify` (append-only lock history, criteria-before-record
# ancestry) need a git work tree and run in the static CI job and through `apps.spikectl`.
{ pkgs, pythonEnv, src, repoRoot, spikectlSrc }:

let
  verifyGroup = group: pkgs.runCommand "phase0-${group}"
    { nativeBuildInputs = [ pythonEnv ]; }
    ''
      PYTHONPATH=${spikectlSrc} python -m spikectl.cli --root ${repoRoot} verify --only ${group}
      touch $out
    '';
in
{
  # Criteria files validate against the schema and every frozen file matches its lock line.
  criteria = verifyGroup "criteria";

  # Every record validates and names a locked (version, hash) pair.
  records = verifyGroup "records";

  # The pure-Python unit tests (oracle, manifests, recorder, sanitiser, spikectl, controls).
  oracle-unit = pkgs.runCommand "phase0-oracle-unit"
    { nativeBuildInputs = [ pythonEnv pkgs.git ]; }
    ''
      cp -r ${repoRoot} work
      chmod -R u+w work
      cd work/spikes/phase0
      export HOME=$TMPDIR
      pytest -q -p no:cacheprovider
      touch $out
    '';

  # Nix formatter, Python formatter, Python lint and shellcheck of the guest scripts.
  formatting = pkgs.runCommand "phase0-formatting"
    { nativeBuildInputs = [ pkgs.nixpkgs-fmt pkgs.ruff pkgs.shellcheck ]; }
    ''
      cp -r ${src} work
      chmod -R u+w work
      cd work
      nixpkgs-fmt --check $(find . -name '*.nix')
      ruff format --check --no-cache .
      ruff check --no-cache .
      shellcheck -x $(find . -name '*.sh')
      touch $out
    '';
}
