"""The ground the S2 checks share (task T067): the recovery sequence of guest/recover.sh step by
step, the boot at `boot:after-failure`, the history of a criteria table, the judgement of an
observation against a prediction table, and the evidence files.

A step that exits with an unexpected status is a FAIL of the scenario (AssertionError), a table
that the harness cannot follow is an `error` (RuntimeError): testlib.recorder maps the two so.
The `history` is vm-tests/s2/history.py; `tables` are the criteria's `tables`; `recorder` takes
the evidence references (testlib.recorder). The sequence every check of S2 runs (a synced
array, the failure, the replacement, the boot, the steps) and its judgement are here too, for
tasks T067 to T073. Scaffolding for the spikes, not product code."""

import json
import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from s2.history import ARCH

from testlib import faults
from testlib.recorder import write_evidence

TAIL = 20
FLAGS = {  # step -> the options it takes: plain (SnapRAID without the workflow), accept
    "recover": ("plain",),
    "verify": ("accept",),
    "resync": ("plain", "accept"),
}
OPTION = {"plain": "--plain", "accept": "--accept-unrecoverable"}
LISTED = 3  # `recover` lists unrecoverable files and exits with this status

# The paths of /etc/snapraid.conf that are on a read-only mount: nothing may be, once unlocked.
_READONLY = """import json, os, re, subprocess
paths = [
    line.split()[-1].rstrip("/")
    for line in open("/etc/snapraid.conf")
    if re.match(r"(data|([2-6z]-)?parity|content)\\s", line)
]
readonly = []
for path in paths:
    probe = path
    while not os.path.exists(probe):  # findmnt exits 1 without a word on a missing path
        probe = os.path.dirname(probe)
    options = subprocess.run(
        ["findmnt", "-n", "-o", "OPTIONS", "--target", probe],
        capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    if "ro" in options:
        readonly.append(path)
print(json.dumps(readonly))"""


@dataclass(frozen=True)
class Step:
    rc: int
    out: str


def last_line(text: str) -> str:
    return next((line for line in reversed(text.splitlines()) if line.strip()), "")


class Recovery:
    """One run of the recovery sequence for a failed disk, step after step. A step that exits
    with another status than 0 (3 for `recover` when it lists files) ends the run: the later
    steps refuse to run on top of it. `recover` listing files makes `verify` and `resync` take
    --accept-unrecoverable."""

    def __init__(self, lab: "Lab", disk: str, plain: bool = False):
        self.lab, self.disk, self.plain = lab, disk, plain
        self.results: dict[str, Step] = {}
        self.accept = False
        self.stopped = False

    def run(self, *names: str) -> None:
        for name in names:
            if self.stopped:
                return
            options = [
                OPTION[o]
                for o in FLAGS.get(name, ())
                if (o == "plain" and self.plain) or (o == "accept" and self.accept)
            ]
            command = " ".join(["recover", name, self.disk, *options])
            rc, out = self.lab.machine.execute(f"{command} 2>&1", timeout=900)
            self.results[name] = Step(rc, out)
            if name == "recover" and rc == LISTED:
                self.accept = True
            elif rc != 0:
                self.stopped = True

    def failures(self, listed_ok: bool = False) -> list[str]:
        """The steps that exited with another status than 0 (`recover` may list files)."""
        return [
            f"recover {name} {self.disk} exited {step.rc}: {last_line(step.out)}"
            for name, step in self.results.items()
            if step.rc != 0 and not (listed_ok and name == "recover" and step.rc == LISTED)
        ]

    def summary(self) -> dict:
        return {
            name: {"rc": step.rc, "out": step.out.strip().splitlines()[-TAIL:]}
            for name, step in self.results.items()
        }


@dataclass
class Lab:
    history: object
    recorder: object
    tables: Mapping
    required_settings: dict = field(default_factory=dict)

    @property
    def machine(self):
        return self.history.machine

    def populate_base(self) -> None:
        """tables.base_set into the branch directories, in the order of the table."""
        self.history.populate((e["disk"], e["path"], e["size"]) for e in self.tables["base_set"])

    def apply(self, name: str) -> None:
        """tables.histories[name], action by action; a file lands where the table says."""
        for action in self.tables["histories"][name]:
            kind, path = action["action"], action["path"]
            if kind == "add":
                disk = self.history.add(path, action["size"])
                if disk != action["lands_on"]:
                    raise RuntimeError(
                        f"{path} landed on {disk}, the table says {action['lands_on']}"
                    )
            elif kind == "modify":
                self.history.modify(path, action["offsets"])
            elif kind == "delete":
                self.history.delete(path)
            else:
                raise ValueError(f"histories.{name}: no action {kind}")

    def boot_after_failure(self, serial: str) -> None:
        """The lifecycle point `boot:after-failure`: a graceful shutdown, the failed disk taken
        out (its image replaced by an empty one), a boot."""
        machine = self.machine
        machine.shutdown()
        faults.device_failed_at_boot(
            machine, self.history.disks[serial], self.history.qemu_img, point="boot:after-failure"
        )
        machine.start()
        machine.wait_for_unit("multi-user.target")
        # The mounts are nofail with a device timeout; a boot with a missing disk still has jobs.
        machine.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=180)

    def snapsync(self, word: str) -> Step:
        rc, out = self.machine.execute(f"snapsync {word} 2>&1", timeout=900)
        return Step(rc, out)

    def fix_status(self, disk: str) -> dict[str, str]:
        """The status tag of every file in the newest fix log of `disk`: recovered or
        unrecoverable, by the path in the share."""
        _, out = self.machine.execute(
            f"cat $(ls -t /var/log/recover/fix-{disk}-*.log | head -1) 2>&1"
        )
        tags = {}
        for line in out.splitlines():
            parts = line.split(":", 3)
            if parts[0] == "status" and len(parts) == 4 and parts[2] == disk:
                tags[parts[3].removeprefix("branch/")] = parts[1]
        return tags

    def readonly_locations(self) -> list[str]:
        return json.loads(self.machine.succeed(f"python3 -c {shlex.quote(_READONLY)}"))

    def evidence(self, name: str, data) -> None:
        """Write status/<name>.json and reference it from the running scenario."""
        write_evidence(self.recorder, name, data)


def prediction_mismatches(
    files: Iterable[Mapping], observed: Mapping[tuple[str, str], str]
) -> list[str]:
    """A prediction table's rows (path, version, recoverable) against History.observe: a
    recoverable file is bit-exact, any other is not, and nothing observed lies outside the
    table (no more, no fewer)."""
    out, predicted = [], set()
    for row in files:
        key = (row["path"], row["version"])
        predicted.add(key)
        label = f"{row['path']} ({row['version']})"
        if key not in observed:
            out.append(f"{label} was not observed")
            continue
        if (observed[key] == "bit_exact") != row["recoverable"]:
            wanted = "recoverable" if row["recoverable"] else "unrecoverable"
            out.append(f"{label}: predicted {wanted}, observed {observed[key]}")
    out += [
        f"{path} ({version}) was observed but is not in the prediction"
        for path, version in sorted(set(observed) - predicted)
    ]
    return out


def listed_files(out: str) -> list[str]:
    """The files the `recover` step lists as unrecoverable, by the path in the share."""
    return [
        line.strip().removeprefix("branch/")
        for line in out.splitlines()
        if line.startswith("  branch/")
    ]


def failed_disk(prediction: Mapping) -> tuple[str, str, str]:
    """(disk, how it fails, the size relation of its replacement) of a prediction table."""
    ((disk, kind),) = prediction["failed"].items()
    return disk, kind, prediction["replacement"][disk]


def synced_array(lab: Lab, snapshots: bool = True) -> None:
    """A fresh array with tables.base_set, synced (by the snapshot workflow, complete, or by
    plain SnapRAID), in the layout of the model; anything else is no precondition of S2."""
    lab.history.reset()
    lab.populate_base()
    rc, out, _ = lab.history.sync(snapshots=snapshots)
    if rc != 0:
        raise RuntimeError(f"the first sync exited {rc}: {last_line(out)}")
    if snapshots:
        complete = lab.snapsync("complete")
        if complete.rc != 0:
            raise RuntimeError(f"snapsync complete exited {complete.rc} after the first sync")
    differences = lab.history.layout_differences()
    if differences:
        raise RuntimeError(
            "the layout of the model differs from snapraid locate: " + "; ".join(differences)
        )


def survivors_unchanged(lab: Lab, failed: str) -> list[str]:
    """The other disks keep the content of the history: a file written or changed after the sync
    holds that content now, and a synced file the history left alone is still there."""
    changed = lab.history.changed
    out = []
    for disk in ARCH:
        if disk == failed:
            continue
        wrong: dict[str, list[str]] = {}
        for (rel, version), state in lab.history.observe(disk).items():
            if state == "bit_exact":
                continue
            if version == "synced" and f"branch/{rel}" in changed.get(disk, ()):
                continue  # its synced content was replaced or removed by the history
            wrong.setdefault(state, []).append(rel)
        if wrong:
            what = "; ".join(f"{state} {', '.join(rels)}" for state, rels in wrong.items())
            out.append(f"{disk} changed: {what}")
    return out


def recover_after_failure(
    lab: Lab,
    prediction: Mapping,
    listed_ok: bool,
    plain: bool = False,
    after_resync: Callable[[dict], list[str]] | None = None,
):
    """The failure, the replacement, the boot and the steps: (evidence, failures, the recovery,
    None when the replacement did not succeed and no boot was made)."""
    disk, kind, relation = failed_disk(prediction)
    seen = {"failed": {"disk": disk, "kind": kind}, "fault": str(lab.history.fail(disk, kind))}
    try:
        lab.history.replace(disk, relation)
    except AssertionError as failure:
        return seen, [str(failure)], None
    lab.boot_after_failure(disk)
    recovery = Recovery(lab, disk, plain=plain)
    failures = []
    recovery.run("recover", "verify", "resync")
    if after_resync and "resync" in recovery.results and recovery.results["resync"].rc == 0:
        failures += after_resync(seen)
    recovery.run("unlock")
    seen["steps"] = recovery.summary()
    return seen, failures + recovery.failures(listed_ok), recovery


def observe_recovery(lab: Lab, prediction: Mapping, seen: dict, failures: list[str]) -> dict:
    """The replacement and the survivors against the prediction; the observation."""
    disk = failed_disk(prediction)[0]
    observed = lab.history.observe(disk)
    seen["observed"] = {f"{path} ({version})": o for (path, version), o in observed.items()}
    seen["mismatches"] = prediction_mismatches(prediction["files"], observed)
    failures += seen["mismatches"] + survivors_unchanged(lab, disk)
    return observed


def conclude(lab: Lab, name: str, seen: dict, failures: list[str]) -> None:
    seen["failures"] = failures
    lab.evidence(name, seen)
    assert not failures, "; ".join(failures)


def judge(lab: Lab, name: str, prediction: Mapping, seen: dict, failures: list, recovered: bool):
    if recovered:
        observe_recovery(lab, prediction, seen, failures)
    conclude(lab, name, seen, failures)
