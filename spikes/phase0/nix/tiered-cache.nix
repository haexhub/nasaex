# The two-pool tiered-cache mount module (mergerfs pattern "tiered cache", research 7.2).
# Scaffolding for the spikes, not product code.
#
#   base pool  (/mnt/base):  the archive branches only; the mover writes into the archive here
#   cache pool (/mnt/cache): the pool branch first, then the archive branches in mode NC
#                            (no create); this is the share clients see
#
# Defaults are the decisions of plan O16 and O9: create policy `ff`, `moveonenospc` off, an
# explicit low `minfreespace`, `passthrough.io=off`, `cache.files=off`. Everything S1 varies is
# an option. The unlink policy of the cache pool is unset by default, so the mergerfs default
# stays in effect (task T028a); probe T037c and S1 measure alternatives through `unlinkPolicy`.
{ config, lib, ... }:

let
  cfg = config.spike.tieredCache;
  inherit (lib) mkOption mkEnableOption mkIf types;

  # A systemd mount unit, not an fstab entry: the helper of a fuse filesystem hands every
  # fstab option (nofail, x-systemd.*) to mergerfs, which rejects unknown options. The unit is
  # wanted by multi-user.target (not required by local-fs.target), so a missing branch
  # leaves the system booted and the pool unmounted instead of dropping to emergency mode.
  mergerfsMount = { where, branches, fsname, createPolicy, extra ? [ ] }: {
    what = lib.concatStringsSep ":" branches;
    inherit where;
    type = "fuse.mergerfs";
    options = lib.concatStringsSep "," ([
      "allow_other"
      "fsname=${fsname}"
      "category.create=${createPolicy}"
      "moveonenospc=${lib.boolToString cfg.moveOnEnospc}"
      "minfreespace=${cfg.minFreeSpace}"
      "passthrough.io=${cfg.passthroughIo}"
      "cache.files=${cfg.cacheFiles}"
    ] ++ extra ++ cfg.extraOptions);
    wantedBy = [ "multi-user.target" ];
    unitConfig.RequiresMountsFor = [ cfg.poolBranch ] ++ cfg.archiveBranches;
  };
in
{
  options.spike.tieredCache = {
    enable = mkEnableOption "the base pool and the cache pool";

    poolBranch = mkOption {
      type = types.str;
      default = "/mnt/pool";
      description = "The fast branch (btrfs RAID1 pool subvolume): first branch of the cache pool.";
    };
    archiveBranches = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "The branch directories of the archive disks (each inside its data subvolume, beside the trash directory), in branch order.";
    };
    baseMountPoint = mkOption { type = types.str; default = "/mnt/base"; };
    cacheMountPoint = mkOption { type = types.str; default = "/mnt/cache"; };

    createPolicy = mkOption {
      type = types.str;
      default = "ff";
      description = "Create policy of the cache pool (plan O16; S1 tries ff and lus).";
    };
    baseCreatePolicy = mkOption {
      type = types.str;
      default = "mfs";
      description = "Create policy of the base pool (mixed disk sizes: most free space).";
    };
    archiveBranchMode = mkOption {
      type = types.str;
      default = "NC";
      description = "Mode of the archive branches in the cache pool; NC allows change and delete but never create.";
    };
    unlinkPolicy = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        `func.unlink` of the cache pool. null sets no option, so the mergerfs default applies
        (`epall` per the pinned 2.41.1 source, UNVERIFIED until measured). Adopting a value is a
        reviewed amendment (required setting, FR-013), never a silent default.
      '';
    };
    moveOnEnospc = mkOption { type = types.bool; default = false; };
    minFreeSpace = mkOption {
      type = types.str;
      default = "64M";
      description = "Explicitly low, far below the small pool size; the mergerfs default of 4G would exclude every small branch.";
    };
    passthroughIo = mkOption {
      type = types.enum [ "off" "ro" "wo" "rw" ];
      default = "off";
      description = "S1 repeats selected scenarios with rw (plan O9).";
    };
    cacheFiles = mkOption {
      type = types.enum [ "off" "partial" "full" "auto-full" "per-process" ];
      default = "off";
      description = "S1 repeats selected scenarios with auto-full (plan O9).";
    };
    extraOptions = mkOption { type = types.listOf types.str; default = [ ]; };
  };

  config = mkIf cfg.enable {
    systemd.tmpfiles.rules = map (mountPoint: "d ${mountPoint} 0755 root root -") [
      cfg.baseMountPoint
      cfg.cacheMountPoint
    ];

    systemd.mounts = [
      (mergerfsMount {
        where = cfg.baseMountPoint;
        branches = cfg.archiveBranches;
        fsname = "basepool";
        createPolicy = cfg.baseCreatePolicy;
      })
      (mergerfsMount {
        where = cfg.cacheMountPoint;
        branches = [ cfg.poolBranch ] ++ map (b: "${b}=${cfg.archiveBranchMode}") cfg.archiveBranches;
        fsname = "cachepool";
        createPolicy = cfg.createPolicy;
        extra = lib.optional (cfg.unlinkPolicy != null) "func.unlink=${cfg.unlinkPolicy}";
      })
    ];
  };
}
