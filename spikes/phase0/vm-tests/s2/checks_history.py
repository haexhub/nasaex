"""Checks S2.2 and S2.9 (task T068): the same history after the last sync, recovered without and
with the snapshot workflow.

Both start from tables.base_set, synced (S2.2 by plain SnapRAID, S2.9 by the snapshot workflow),
then tables.histories.changes_after_sync: files changed in place, one deleted, two added through
the mover path. arch0 fails (device-ioerror), and the sequence runs with the boot at
`boot:after-failure` between the replacement and the recovery.

S2.2 (`--plain`): the files of arch0 that come back bit-exact in their synced version are the
recoverable set of tables.predictions.S2.2, no more and no fewer; the recover step lists every
other synced file and exits 3 (files it lists beyond the prediction are recorded, not judged);
the unsynced content of a/a4.bin is gone; the files of the surviving disks keep the content of
the history. The fix log's status tags per file are recorded against the prediction. The recovered
set goes to required_settings["workflow_value"].

S2.9 (the snapshot workflow): every synced file of arch0 comes back, the unsynced content of
a/a4.bin does not, every step exits 0, the surviving disks keep their content. The files it
recovered and S2.2 did not are recorded as the measured value of the workflow (SC-006).

Scaffolding for the spikes, not product code."""

from s2.context import (
    Lab,
    conclude,
    failed_disk,
    listed_files,
    observe_recovery,
    recover_after_failure,
    synced_array,
)

STATUS = {True: "recovered", False: "unrecoverable"}


def _run(lab: Lab, scenario: str, plain: bool):
    """The state, the failure and the steps of one scenario; (prediction, evidence, failures,
    recovery, the observation of the replacement or None)."""
    prediction = lab.tables["predictions"][scenario]
    synced_array(lab, snapshots=not plain)
    lab.apply(prediction["history"])
    seen, failures, recovery = recover_after_failure(lab, prediction, listed_ok=plain, plain=plain)
    observed = None
    if recovery is not None:
        observed = observe_recovery(lab, prediction, seen, failures)
    return prediction, seen, failures, recovery, observed


def _recovered(observed: dict) -> list[str]:
    return sorted(
        path
        for (path, version), state in observed.items()
        if version == "synced" and state == "bit_exact"
    )


def _listing(prediction, recovery, seen, failures) -> None:
    """S2.2: the recover step exits 3 and lists every synced file the prediction loses."""
    disk = failed_disk(prediction)[0]
    lost = [
        r["path"] for r in prediction["files"] if r["version"] == "synced" and not r["recoverable"]
    ]
    step = recovery.results.get("recover")
    if step is None:
        return
    if step.rc == 0 and lost:
        failures.append(
            f"recover recover {disk} exited 0, the prediction lists unrecoverable files "
            "(exit 3 expected)"
        )
    listed = listed_files(step.out)
    seen["listed"] = listed
    seen["listed_extra"] = [path for path in listed if path not in lost]
    failures += [f"recover did not list {path}" for path in lost if path not in listed]


def _fix_tags(lab: Lab, prediction, seen) -> None:
    """The fix log's status tag of every synced file against the prediction (recorded only)."""
    tags = lab.fix_status(failed_disk(prediction)[0])
    seen["status_tags"] = tags
    seen["status_vs_prediction"] = [
        f"{row['path']}: predicted {STATUS[row['recoverable']]}, "
        f"the fix log says {tags.get(row['path'], 'nothing')}"
        for row in prediction["files"]
        if row["version"] == "synced" and tags.get(row["path"]) != STATUS[row["recoverable"]]
    ]


def check_history_plain(lab: Lab) -> None:
    prediction, seen, failures, recovery, observed = _run(lab, "S2.2", plain=True)
    if recovery is not None:
        _listing(prediction, recovery, seen, failures)
        _fix_tags(lab, prediction, seen)
        lab.required_settings["workflow_value"] = {"plain_recovered": _recovered(observed)}
    conclude(lab, "S2.2-history-plain", seen, failures)


def check_history_snapshot(lab: Lab) -> None:
    _, seen, failures, recovery, observed = _run(lab, "S2.9", plain=False)
    if recovery is not None:
        value = lab.required_settings.setdefault("workflow_value", {})
        value["snapshot_recovered"] = _recovered(observed)
        if "plain_recovered" in value:
            gained = set(value["snapshot_recovered"]) - set(value["plain_recovered"])
            value["recovered_by_snapshot_only"] = sorted(gained)
        else:
            value["recovered_by_snapshot_only"] = None
            value["note"] = "S2.2 did not run; nothing to compare with"
        seen["workflow_value"] = dict(value)
    conclude(lab, "S2.9-history-snapshot", seen, failures)


CHECKS = {
    "history_plain": check_history_plain,
    "history_snapshot": check_history_snapshot,
}
