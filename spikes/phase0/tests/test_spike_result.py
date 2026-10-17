"""The result a test script reports to `spikectl run` (testlib/spike_result.py): the file name
and shape that spikectl/run.py reads, written so that a run cut short keeps what finished."""

import json

import jsonschema
import pytest
from helpers import CONTRACTS, REPO_ROOT
from testlib.spike_result import RESULT_FILE, version, write_spike_result

from spikectl import run
from testlib.recorder import Recorder

VERSIONS = {"kernel": "6.18.56", "qemu": "qemu-img version 10.2.4", "samba": "4.23.10"}


def scenarios():
    recorder = Recorder()
    recorder.run("S1.1", lambda: None)

    def broken():
        raise AssertionError("view differs")

    recorder.run("S1.3", broken)
    return recorder.scenarios()


def test_spikectl_reads_the_file_the_test_script_writes(tmp_path):
    assert run.RESULT_FILE == RESULT_FILE

    write_spike_result(tmp_path / RESULT_FILE, scenarios(), VERSIONS, {"create_policy": "ff"})

    result = run._read_result(tmp_path / RESULT_FILE)
    assert [s["id"] for s in result["scenarios"]] == ["S1.1", "S1.3"]
    assert result["tool_versions"] == VERSIONS
    assert result["required_settings"] == {"create_policy": "ff"}
    assert "coverage" not in result
    assert list(tmp_path.iterdir()) == [tmp_path / RESULT_FILE]


def test_every_scenario_written_is_a_valid_scenario_result_of_the_record(tmp_path):
    schema = json.loads((REPO_ROOT / CONTRACTS / "result-record.schema.json").read_text())
    path = tmp_path / RESULT_FILE

    write_spike_result(path, scenarios(), VERSIONS, coverage={"cases": 2})

    for scenario in json.loads(path.read_text())["scenarios"]:
        jsonschema.validate(
            scenario, {**schema["$defs"]["scenarioResult"], "$defs": schema["$defs"]}
        )
    assert json.loads(path.read_text())["coverage"] == {"cases": 2}


@pytest.mark.parametrize("missing", ["kernel", "qemu"])
def test_tool_versions_the_record_requires_must_be_present(tmp_path, missing):
    versions = {k: v for k, v in VERSIONS.items() if k != missing}

    with pytest.raises(ValueError, match=missing):
        write_spike_result(tmp_path / RESULT_FILE, scenarios(), versions)


def test_a_rewrite_replaces_the_whole_file(tmp_path):
    path = tmp_path / RESULT_FILE
    write_spike_result(path, scenarios(), VERSIONS)

    write_spike_result(path, scenarios()[:1], VERSIONS)

    assert [s["id"] for s in json.loads(path.read_text())["scenarios"]] == ["S1.1"]


def test_version_is_the_first_non_empty_line_of_a_tool_output():
    assert version("\nmergerfs v2.41.1\n\nmore\n") == "mergerfs v2.41.1"
    assert version("  4.23.10  ") == "4.23.10"
