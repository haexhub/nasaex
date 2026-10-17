# mkSpike: one NixOS VM test per spike (scaffolding, not product code).
#
# The test is built as a cached derivation but is meant to be EXECUTED OUTSIDE the sandbox by
# running `driver` (research D2): every run is then fresh, a FAIL keeps its evidence, and the
# multi-GiB machine state can live on a disk path. `qemu.forceAccel` makes a missing /dev/kvm
# a hard error, never a silent TCG fallback.
{ pkgs, profiles, snapraidBtrfs, guestScripts }:

let
  inherit (pkgs) lib;

  # Serial budget per spike in minutes (research D14, estimates until shakedown replaces them).
  budgetMinutes = {
    s1 = 20;
    s2 = 25;
    s3 = 40;
    s4 = 15;
    s5 = 30;
    x1 = 30;
    controls = 15;
    probe = 15;
  };

  # VM sizes are proposals, settled in shakedown. The aggregate must stay within the RAM
  # budget that `spikectl` checks before any run (default 8 GiB for all VMs together).
  vmSizing = {
    server = { memorySize = 1536; cores = 2; };
    client = { memorySize = 768; cores = 1; };
  };

  python = pkgs.python3;
  # testlib importable from the Python test script (the driver's Python environment).
  testlibPackage = python.pkgs.toPythonModule (pkgs.runCommand "phase0-testlib" { } ''
    mkdir -p $out/${python.sitePackages}
    cp -r ${../testlib} $out/${python.sitePackages}/testlib
  '');
in
{ name, profile, machines ? [ "server" ], budget, testScript, extraModules ? { } }:

let
  nodeDefs = import ./nodes.nix {
    inherit pkgs lib snapraidBtrfs guestScripts vmSizing;
    profile = profiles.${profile};
  };

  vmResources = lib.genAttrs machines (m: {
    vcpus = vmSizing.${m}.cores;
    memory_mib = vmSizing.${m}.memorySize;
  });

  # What the Python test script and the spikectl preflight need to know about the topology.
  topology = {
    inherit profile vmResources;
    inherit (nodeDefs) archiveLayout;
    disks = nodeDefs.diskTable;
  };

  test = pkgs.testers.runNixOSTest {
    inherit name;
    qemu.forceAccel = true;
    qemu.package = pkgs.qemu_test;
    globalTimeout = budgetMinutes.${budget} * 60;
    extraPythonPackages = _: [ testlibPackage ];
    nodes = lib.genAttrs machines (m: {
      imports = [ nodeDefs.${m} ] ++ (extraModules.${m} or [ ]);
    });
    testScript = ''
      import json

      TOPOLOGY = json.loads(${builtins.toJSON (builtins.toJSON topology)})
      # Host tools the test script calls itself (the driver's PATH does not carry them).
      QEMU_IMG = "${pkgs.qemu_test}/bin/qemu-img"

    '' + builtins.readFile testScript;
  };
in
{
  inherit test topology vmResources;
  driver = test.config.driver;
  # Sum over the VMs that run at the same time: input of the spikectl RAM-budget preflight.
  totalVmRamMiB = lib.foldl' (sum: m: sum + m.memory_mib) 0 (lib.attrValues vmResources);
}
