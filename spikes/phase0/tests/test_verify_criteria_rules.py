"""Rules on criteria and records that the schemas do not carry yet (mirroring them in the
contracts is a later reviewed docs PR): the S2 criteria carry S2.7 as the reviewed scenario
(finding U11, T037d)."""

import pytest
from helpers import LOCK, criteria_doc, lock_lines, make_root, write_criteria

from spikectl.freeze import FreezeError, freeze
from spikectl.verify import verify


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


def checks(findings):
    return {f.check for f in findings}


# --- S2 criteria carry S2.7 as the reviewed scenario ------------------------


@pytest.mark.parametrize(
    "doc",
    [
        criteria_doc("S2", scenarios=2),  # no S2.7 at all
        criteria_doc("S2", scenarios=7),  # S2.7 as an ordinary automated scenario
    ],
    ids=["without-S2.7", "S2.7-not-reviewed"],
)
def test_s2_criteria_without_the_reviewed_s2_7_are_reported(root, doc):
    write_criteria(root, doc=doc)

    findings = verify(root, only=("criteria",))

    assert checks(findings) == {"criteria-review"}
    assert "S2.yaml" in findings[0].message


def test_an_s2_amendment_without_the_reviewed_s2_7_is_reported(root):
    write_criteria(root, doc=criteria_doc("S2", version=1, scenarios=7, review=True))
    freeze(root, "S2")
    write_criteria(root, doc=criteria_doc("S2", version=2, scenarios=7))

    assert checks(verify(root, only=("criteria",))) == {"criteria-review"}


def test_s2_criteria_with_the_reviewed_s2_7_are_accepted(root):
    write_criteria(root, doc=criteria_doc("S2", scenarios=7, review=True))

    assert verify(root, only=("criteria",)) == []


def test_other_spikes_need_no_reviewed_scenario(root):
    for spike in ("S1", "S3", "S4", "S5", "X1"):
        write_criteria(root, doc=criteria_doc(spike))

    assert verify(root, only=("criteria",)) == []


@pytest.mark.parametrize("scenarios", [2, 7])
def test_freeze_refuses_s2_criteria_without_the_reviewed_s2_7(root, scenarios):
    path = write_criteria(root, doc=criteria_doc("S2", scenarios=scenarios))
    original = path.read_bytes()

    with pytest.raises(FreezeError, match="S2.7"):
        freeze(root, "S2")

    assert path.read_bytes() == original
    assert lock_lines(root) == []
    assert not (root / LOCK).exists()


def test_freeze_accepts_s2_criteria_with_the_reviewed_s2_7(root):
    write_criteria(root, doc=criteria_doc("S2", scenarios=7, review=True))

    entry = freeze(root, "S2")

    assert (entry.file, entry.version) == ("S2.yaml", 1)
