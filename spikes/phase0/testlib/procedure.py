"""The written recovery procedure of S2 (task T062; data-model "Recovery procedure").

guest/recover.sh carries the procedure as `#:` comment lines above each step: a header
`#: <n>. <step> <arguments>`, then `#: Do: ...` and `#: Check: ...`, each continued on further
`#:` lines. This module reads them and renders the text that the S2.7 check stores in the
evidence archive and in `required_settings.recovery_procedure`, so the text cannot drift from the
commands that run. Standard library only."""

import re
from dataclasses import dataclass
from pathlib import Path

# The safe sequence of spec User Story 2, scenario 7, in the SnapRAID manual's shape (research 3.4).
SEQUENCE = ("detect", "lock", "replace", "recover", "verify", "resync", "unlock")
# The command name of guest/recover.sh on the node (nix/guest-scripts.nix drops the suffix).
SCRIPT = "recover"
_HEADER = re.compile(r"(\d+)\. (\w+)(?: (.*))?$")
_FIELD = re.compile(r"(Do|Check): (.*)$")


class ProcedureError(ValueError):
    """The `#:` lines do not form a complete, ordered procedure."""


@dataclass(frozen=True)
class Step:
    number: int
    name: str
    arguments: str
    do: str
    check: str


def parse(text: str) -> list[Step]:
    """The steps of the `#:` lines of a recovery script, in file order."""
    steps: list[dict] = []
    field = None
    for line in text.splitlines():
        if not line.startswith("#:"):
            field = None
            continue
        body = line[2:].strip()
        if header := _HEADER.match(body):
            number = int(header[1])
            if number != len(steps) + 1:
                expected = len(steps) + 1
                raise ProcedureError(f"step {header[2]} is numbered {number}, expected {expected}")
            steps.append({"number": number, "name": header[2], "arguments": header[3] or ""})
            field = None
        elif match := _FIELD.match(body):
            if not steps:
                raise ProcedureError(f"`#: {body}` stands outside a step")
            field = match[1].lower()
            steps[-1][field] = match[2]
        elif field is not None:
            steps[-1][field] += " " + body
        else:
            raise ProcedureError(f"`#: {body}` stands outside a field")
    if not steps:
        raise ProcedureError("no step found")
    for step in steps:
        for field in ("do", "check"):
            if field not in step:
                raise ProcedureError(f"step {step['name']} has no {field.capitalize()}")
    return [Step(**step) for step in steps]


def render(steps: list[Step]) -> str:
    """The written procedure as Markdown; the steps must be the spec's sequence in order."""
    if tuple(s.name for s in steps) != SEQUENCE:
        raise ProcedureError(f"the steps must be {', '.join(SEQUENCE)}")
    lines = [
        "# Recovery of a failed archive data disk",
        "",
        f"Run the steps in this order with `{SCRIPT}`. Every step ends with its check; a failed",
        "check stops the procedure, and no later step runs on top of it.",
        "",
    ]
    for step in steps:
        command = " ".join(filter(None, [SCRIPT, step.name, step.arguments]))
        lines += [
            f"{step.number}. **{step.name}**: `{command}`",
            f"   - Do: {step.do}",
            f"   - Check: {step.check}",
        ]
    return "\n".join(lines) + "\n"


def render_file(path: Path | str) -> str:
    return render(parse(Path(path).read_text()))
