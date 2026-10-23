"""Checks S2.1 and S2.3 (task T067): the failure of a disk after a sync, recovered by the recovery
sequence of guest/recover.sh with the snapshot workflow.

S2.1: after a complete sync and no change, arch0 fails; the sequence runs step by step with the
boot at `boot:after-failure` between the replacement and the recovery. Every step exits 0, every
file of tables.predictions.S2.1 is bit-exact on the replacement, the other disks are unchanged,
after the resync `snapsync complete` and `snapsync covered` exit 0 and after the unlock no
location of the SnapRAID config is read-only.

S2.3: two files are written to the array after the sync (they land on arch1 through the mover
path), then arch1 is unplugged. Every synced file of arch1 comes back, the two files do not (the
prediction table, from the history: SnapRAID knows only the last sync and does not name them,
probe T057); the evidence records whether the recover step names them. The other disks are
unchanged. The steps may exit 3 for `recover` when it lists files; then `verify` and `resync`
accept the loss.

A precondition that is not met (a sync that fails, a layout that is not the model's, a file that
lands on another disk) is an `error`, a deviation from the prediction a `fail`. Scaffolding for
the spikes, not product code."""

from s2.context import Lab, Recovery, last_line, prediction_mismatches
from s2.history import ARCH

COVERAGE = ("complete", "covered")


def _failed(prediction: dict) -> tuple[str, str, str]:
    """(disk, how it fails, the size relation of its replacement) of a prediction table."""
    ((disk, kind),) = prediction["failed"].items()
    return disk, kind, prediction["replacement"][disk]


def _synced_array(lab: Lab) -> None:
    """A fresh array with tables.base_set, synced by the snapshot workflow, complete, and in the
    layout of the model; anything else is no precondition of S2."""
    lab.history.reset()
    lab.populate_base()
    rc, out, _ = lab.history.sync(snapshots=True)
    if rc != 0:
        raise RuntimeError(f"the first sync exited {rc}: {last_line(out)}")
    complete = lab.snapsync("complete")
    if complete.rc != 0:
        raise RuntimeError(f"snapsync complete exited {complete.rc} after the first sync")
    differences = lab.history.layout_differences()
    if differences:
        raise RuntimeError(
            "the layout of the model differs from snapraid locate: " + "; ".join(differences)
        )


def _others_unchanged(lab: Lab, failed: str) -> list[str]:
    changed = []
    for disk in ARCH:
        if disk == failed:
            continue
        seen = lab.history.compare(disk)
        what = [
            f"{kind} {', '.join(paths)}" for kind in ("differs", "missing") if (paths := seen[kind])
        ]
        if what:
            changed.append(f"{disk} changed: {'; '.join(what)}")
    return changed


def _coverage(lab: Lab, seen: dict) -> list[str]:
    """After the resync: the snapshot set is complete and the array is covered again."""
    steps = {word: lab.snapsync(word) for word in COVERAGE}
    seen["covered"] = {word: step.rc for word, step in steps.items()}
    return [
        f"snapsync {word} exited {step.rc} after the resync: {last_line(step.out)}"
        for word, step in steps.items()
        if step.rc != 0
    ]


def _recover(lab, prediction, listed_ok, after_resync=None):
    """The failure, the replacement, the boot and the steps; (evidence, failures, recovery or
    None when the replacement did not succeed and no boot was made)."""
    disk, kind, relation = _failed(prediction)
    seen = {"failed": {"disk": disk, "kind": kind}, "fault": str(lab.history.fail(disk, kind))}
    try:
        lab.history.replace(disk, relation)
    except AssertionError as failure:
        return seen, [str(failure)], None
    lab.boot_after_failure(disk)
    recovery = Recovery(lab, disk)
    failures = []
    recovery.run("recover", "verify", "resync")
    if after_resync and "resync" in recovery.results and recovery.results["resync"].rc == 0:
        failures += after_resync(seen)
    recovery.run("unlock")
    seen["steps"] = recovery.summary()
    return seen, failures + recovery.failures(listed_ok), recovery


def _judge(lab, name, prediction, seen, failures, recovered) -> None:
    disk = _failed(prediction)[0]
    if recovered:
        observed = lab.history.observe(disk)
        seen["observed"] = {f"{path} ({version})": o for (path, version), o in observed.items()}
        seen["mismatches"] = prediction_mismatches(prediction["files"], observed)
        failures += seen["mismatches"] + _others_unchanged(lab, disk)
    seen["failures"] = failures
    lab.evidence(name, seen)
    assert not failures, "; ".join(failures)


def check_complete_sync_recovery(lab: Lab) -> None:
    prediction = lab.tables["predictions"]["S2.1"]
    _synced_array(lab)
    seen, failures, recovery = _recover(lab, prediction, False, lambda seen: _coverage(lab, seen))
    if recovery is not None and "unlock" in recovery.results and recovery.results["unlock"].rc == 0:
        seen["readonly_after_unlock"] = lab.readonly_locations()
        if seen["readonly_after_unlock"]:
            failures.append(
                f"read-only after the unlock: {', '.join(seen['readonly_after_unlock'])}"
            )
    _judge(lab, "S2.1-complete-sync-recovery", prediction, seen, failures, recovery is not None)


def check_written_after_sync(lab: Lab) -> None:
    prediction = lab.tables["predictions"]["S2.3"]
    disk = _failed(prediction)[0]
    _synced_array(lab)
    lab.apply(prediction["history"])
    unsynced = sorted(lab.history.unsynced[disk])
    seen, failures, recovery = _recover(lab, prediction, True)
    seen["unrecoverable_from_history"] = unsynced
    if recovery is not None and "recover" in recovery.results:
        out = recovery.results["recover"].out
        seen["recover_names_them"] = [
            row["path"]
            for row in prediction["files"]
            if row["version"] == "unsynced" and row["path"] in out
        ]
    _judge(lab, "S2.3-written-after-sync", prediction, seen, failures, recovery is not None)


CHECKS = {
    "complete_sync_recovery": check_complete_sync_recovery,
    "written_after_sync": check_written_after_sync,
}
