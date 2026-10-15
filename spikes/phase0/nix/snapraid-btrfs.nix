# Snapshot tooling for the archive (plan open item O15, decided 2026-10-15).
# Not in nixpkgs at the pin, so it is fetched by pinned upstream revision with a
# fixed-output hash. Scaffolding for the Phase 0 spikes, not product code.
{ pkgs }:

let
  rev = "6492a45ad55c389c0301075dcc8bc8784ef3e274";
  runtimeInputs = with pkgs; [
    bash
    coreutils
    gawk
    gnugrep
    gnused
    btrfs-progs
    snapper
    snapraid
  ];
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "snapraid-btrfs";
  # Upstream reports 0.14.1+git at this revision.
  version = "0.14.1-${builtins.substring 0 7 rev}";

  src = pkgs.fetchFromGitHub {
    owner = "automorphism88";
    repo = "snapraid-btrfs";
    inherit rev;
    hash = "sha256-YPkKjlx7j7yjTWReAvgaFNz6dFxVIcaN9to2cDfmovU=";
  };

  nativeBuildInputs = [ pkgs.makeWrapper ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 snapraid-btrfs $out/bin/snapraid-btrfs
    patchShebangs $out/bin/snapraid-btrfs
    wrapProgram $out/bin/snapraid-btrfs \
      --prefix PATH : ${pkgs.lib.makeBinPath runtimeInputs}
    runHook postInstall
  '';

  meta = {
    description = "Run SnapRAID against btrfs snapshots managed by snapper";
    homepage = "https://github.com/automorphism88/snapraid-btrfs";
    license = pkgs.lib.licenses.gpl3Plus;
    mainProgram = "snapraid-btrfs";
  };
}
