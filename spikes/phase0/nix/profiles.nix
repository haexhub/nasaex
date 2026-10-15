# Topology profiles (research D3). Sizes are initial proposals, to be settled in shakedown.
# Every disk becomes a virtualisation.emptyDiskImages entry with a stable serial, so it
# resolves at /dev/disk/by-id/virtio-<serial> and removing one never renumbers the others.
{ lib }:

let
  disk = serial: role: sizeMiB: filesystem: { inherit serial role sizeMiB filesystem; };

  # One profile from the sizes of its disks. Spare images are attached from the start and
  # stay unused until a scenario replaces a failed disk: one spare per size class, so a
  # replacement of the same size, a larger one and a smaller one are always at hand.
  mkProfile = { pool, arch0, arch1, arch2, parity }: {
    disks = [
      (disk "pool0" "pool" pool "btrfs")
      (disk "pool1" "pool" pool "btrfs")
      (disk "arch0" "archive" arch0 "btrfs")
      (disk "arch1" "archive" arch1 "btrfs")
      (disk "arch2" "archive" arch2 "btrfs")
      (disk "parity0" "parity" parity "ext4")
      (disk "spare0" "replacement" arch0 "")
      (disk "spare1" "replacement" arch1 "")
      (disk "spare2" "replacement" arch2 "")
      (disk "spare3" "replacement" pool "")
    ];
  };

  large = mkProfile {
    pool = 8192;
    arch0 = 6144;
    arch1 = 10240;
    arch2 = 4096;
    parity = 12288;
  };

  small = mkProfile {
    pool = 1024;
    arch0 = 640;
    arch1 = 1024;
    arch2 = 512;
    parity = 1280;
  };
in
{
  inherit large small;
  # S5 and X1 use the large disks (S5 gets its own single-machine topology in its own task).
  s5 = large;
  x1 = large;
}
