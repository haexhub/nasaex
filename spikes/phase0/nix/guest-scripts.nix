# Installs the in-VM scaffolding of the spikes into the node closure: every script of
# spikes/phase0/guest/ and spikes/phase0/controls/. The real files live in
# $out/share/phase0/guest (scripts that source their siblings resolve them from there);
# $out/bin has one symlink per script, without the .sh suffix. Scaffolding, not product code.
{ pkgs }:

pkgs.stdenvNoCC.mkDerivation {
  pname = "phase0-guest-scripts";
  version = "0";
  src = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [ ../guest ../controls ];
  };
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    share=$out/share/phase0/guest
    mkdir -p $share $out/bin
    for script in guest/*.sh controls/*.sh; do
      [ -e "$script" ] || continue
      install -m755 "$script" $share/
    done
    patchShebangs $share
    for script in $share/*.sh; do
      ln -s "$script" "$out/bin/$(basename "$script" .sh)"
    done
    runHook postInstall
  '';
}
