"""The result a spike's test script reports to `spikectl run` (spikectl/run.py reads it).

The test script writes RESULT_FILE in the driver's output directory, its working directory:
{"scenarios": <Recorder.scenarios()>, "tool_versions": {...}} plus "required_settings" and
"coverage" where the spike has them. It rewrites the whole file after every scenario, through a
temporary file and a rename, so that a run cut short at its budget keeps every scenario that
finished; spikectl turns the missing ones into `error`. Standard library only (it runs in the
test driver)."""

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

RESULT_FILE = "spike-result.json"
# tool_versions of a record must name these (contracts/result-record.schema.json).
REQUIRED_TOOLS = ("kernel", "qemu")


def version(output: str) -> str:
    """The first non-empty line of a tool's version output."""
    return next((line.strip() for line in output.splitlines() if line.strip()), "")


def write_spike_result(
    path: Path,
    scenarios: Sequence[dict],
    tool_versions: Mapping[str, str],
    required_settings: Mapping | None = None,
    coverage: Mapping | None = None,
) -> None:
    missing = [tool for tool in REQUIRED_TOOLS if not tool_versions.get(tool)]
    if missing:
        raise ValueError(f"tool_versions lacks {', '.join(missing)}")
    result: dict = {"scenarios": list(scenarios), "tool_versions": dict(tool_versions)}
    if required_settings is not None:
        result["required_settings"] = dict(required_settings)
    if coverage is not None:
        result["coverage"] = dict(coverage)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    os.replace(temporary, path)
