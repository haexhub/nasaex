# Evaluation check of the archive layout and the unlink parameter (task T028a). No VM runs:
# it reads the evaluated `server` node of a test and fails the build with every mismatch.
#   - each archive branch is the branch directory inside the data subvolume, beside `trash/`,
#     and the data subvolume (the SnapRAID data path) holds both
#   - the cache pool sets no `func.unlink` by default (the mergerfs default stays in effect)
#   - `unlinkPolicy` sets `func.unlink` on the cache pool only
{ pkgs, mkSpike }:

let
  inherit (pkgs) lib;

  spikeWith = settings: mkSpike {
    name = "tiered-cache-layout";
    profile = "small";
    machines = [ "server" ];
    budget = "probe";
    testScript = builtins.toFile "unused.py" "";
    extraModules.server = [{ spike.tieredCache = { enable = true; } // settings; }];
  };

  optionsOf = spike: where:
    let unit = lib.findFirst (m: m.where == where) null spike.test.config.nodes.server.systemd.mounts;
    in lib.splitString "," unit.options;
  whatOf = spike: where:
    (lib.findFirst (m: m.where == where) null spike.test.config.nodes.server.systemd.mounts).what;
  unlinkOptions = opts: lib.filter (lib.hasPrefix "func.unlink") opts;

  default = spikeWith { };
  ff = spikeWith { unlinkPolicy = "ff"; };
  layout = default.topology.archiveLayout;
  archives = [ "arch0" "arch1" "arch2" ];

  expect = name: got: want: lib.optional (got != want)
    "${name}: got ${builtins.toJSON got}, want ${builtins.toJSON want}";

  failures = lib.concatLists [
    (expect "archive layout" layout (map
      (serial: {
        inherit serial;
        data = "/mnt/archive/${serial}";
        branch = "/mnt/archive/${serial}/branch";
        trash = "/mnt/archive/${serial}/trash";
      })
      archives))
    (expect "base pool branches" (whatOf default "/mnt/base")
      "/mnt/archive/arch0/branch:/mnt/archive/arch1/branch:/mnt/archive/arch2/branch")
    (expect "cache pool branches" (whatOf default "/mnt/cache")
      "/mnt/pool:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC:/mnt/archive/arch2/branch=NC")
    (expect "default cache pool unlink options" (unlinkOptions (optionsOf default "/mnt/cache")) [ ])
    (expect "default base pool unlink options" (unlinkOptions (optionsOf default "/mnt/base")) [ ])
    (expect "cache pool unlink options with ff" (unlinkOptions (optionsOf ff "/mnt/cache")) [ "func.unlink=ff" ])
    (expect "base pool unlink options with ff" (unlinkOptions (optionsOf ff "/mnt/base")) [ ])
  ];
in
pkgs.runCommand "phase0-tiered-cache-layout" { } (
  if failures == [ ] then "touch $out"
  else ''
    echo ${lib.escapeShellArg (lib.concatStringsSep "\n" failures)} >&2
    exit 1
  ''
)
