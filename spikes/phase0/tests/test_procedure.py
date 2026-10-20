"""The written recovery procedure of S2 (testlib/procedure.py, task T062), rendered from the `#:`
lines of guest/recover.sh."""

from pathlib import Path

import pytest

from testlib import procedure

RECOVER = Path(__file__).resolve().parents[1] / "guest" / "recover.sh"

TWO_STEPS = """\
#!/usr/bin/env bash
#: 1. detect DISK
#: Do: Look for evidence.
#: Check: Evidence found.
recover_detect() { :; }
#: 2. lock DISK
#: Do: Lock it,
#: and wait.
#: Check: Locked.
"""


def test_parse_reads_number_name_arguments_do_and_check_of_every_step():
    steps = procedure.parse(TWO_STEPS)

    assert steps == [
        procedure.Step(1, "detect", "DISK", "Look for evidence.", "Evidence found."),
        procedure.Step(2, "lock", "DISK", "Lock it, and wait.", "Locked."),
    ]


def test_the_steps_of_recover_sh_are_the_sequence_of_the_spec_in_order():
    steps = procedure.parse(RECOVER.read_text())

    assert [s.name for s in steps] == list(procedure.SEQUENCE)
    assert [s.number for s in steps] == list(range(1, 8))
    assert all(s.do and s.check for s in steps)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (TWO_STEPS.replace("2. lock", "3. lock"), "numbered 3"),
        (TWO_STEPS.replace("#: Check: Locked.\n", ""), "lock has no Check"),
        (TWO_STEPS.replace("#: Do: Look for evidence.\n", ""), "detect has no Do"),
        (TWO_STEPS + "true\n#: stray text\n", "outside a field"),
        ("#!/bin/sh\n", "no step"),
        ("#: Do: Lock it.\n", "outside a step"),
    ],
)
def test_parse_refuses_a_malformed_procedure(text, message):
    with pytest.raises(procedure.ProcedureError, match=message):
        procedure.parse(text)


def test_render_refuses_steps_out_of_the_spec_sequence():
    steps = procedure.parse(TWO_STEPS)

    with pytest.raises(procedure.ProcedureError, match="detect, lock, replace"):
        procedure.render(steps)


def test_render_gives_every_step_with_its_command_do_and_check():
    text = procedure.render(procedure.parse(RECOVER.read_text()))

    assert text.startswith("# Recovery of a failed archive data disk\n")
    assert "1. **detect**: `recover detect DISK`\n" in text
    assert "3. **replace**: `recover replace DISK DEVICE FAILED_BYTES`\n" in text
    assert text.count("   - Do: ") == 7 and text.count("   - Check: ") == 7
    assert text.endswith("\n") and "#:" not in text


def test_render_from_file_reads_recover_sh():
    assert procedure.render_file(RECOVER) == procedure.render(procedure.parse(RECOVER.read_text()))
