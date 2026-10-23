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

from s2.context import (
    Lab,
    failed_disk,
    judge,
    last_line,
    recover_after_failure,
    synced_array,
)

COVERAGE = ("complete", "covered")


def _coverage(lab: Lab, seen: dict) -> list[str]:
    """After the resync: the snapshot set is complete and the array is covered again."""
    steps = {word: lab.snapsync(word) for word in COVERAGE}
    seen["covered"] = {word: step.rc for word, step in steps.items()}
    return [
        f"snapsync {word} exited {step.rc} after the resync: {last_line(step.out)}"
        for word, step in steps.items()
        if step.rc != 0
    ]


def check_complete_sync_recovery(lab: Lab) -> None:
    prediction = lab.tables["predictions"]["S2.1"]
    synced_array(lab)
    seen, failures, recovery = recover_after_failure(
        lab, prediction, False, after_resync=lambda seen: _coverage(lab, seen)
    )
    if recovery is not None and "unlock" in recovery.results and recovery.results["unlock"].rc == 0:
        seen["readonly_after_unlock"] = lab.readonly_locations()
        if seen["readonly_after_unlock"]:
            failures.append(
                f"read-only after the unlock: {', '.join(seen['readonly_after_unlock'])}"
            )
    judge(lab, "S2.1-complete-sync-recovery", prediction, seen, failures, recovery is not None)


def check_written_after_sync(lab: Lab) -> None:
    prediction = lab.tables["predictions"]["S2.3"]
    disk = failed_disk(prediction)[0]
    synced_array(lab)
    lab.apply(prediction["history"])
    unsynced = sorted(lab.history.unsynced[disk])
    seen, failures, recovery = recover_after_failure(lab, prediction, True)
    seen["unrecoverable_from_history"] = unsynced
    if recovery is not None and "recover" in recovery.results:
        out = recovery.results["recover"].out
        seen["recover_names_them"] = [
            row["path"]
            for row in prediction["files"]
            if row["version"] == "unsynced" and row["path"] in out
        ]
    judge(lab, "S2.3-written-after-sync", prediction, seen, failures, recovery is not None)


CHECKS = {
    "complete_sync_recovery": check_complete_sync_recovery,
    "written_after_sync": check_written_after_sync,
}
