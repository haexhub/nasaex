# Fault-kind probe VM test (tasks T034, fault-points contract rule 6: probe first).
# A probe-only addition inside vm-tests/: it never writes a record. One small VM, `small`
# profile, the tiered-cache mounts enabled so that reboot behaviour covers them too.
{ mkSpike }:

mkSpike {
  name = "probe-faults";
  profile = "small";
  machines = [ "server" ];
  budget = "probe";
  testScript = ./probe_faults.py;
  extraModules.server = [{ spike.tieredCache.enable = true; }];
}
