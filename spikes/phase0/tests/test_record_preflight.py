"""spikectl run preflight: the RAM budget of both modes and the gate of record mode (KVM, a
clean git tree, locked criteria)."""

import pytest
from helpers import (
    CRITERIA,
    commit_all,
    init_repo,
    make_root,
    write_criteria,
)

from spikectl import record as record_module
from spikectl.freeze import freeze
from spikectl.record import (
    DEFAULT_RAM_BUDGET_MIB,
    PreflightError,
    configured_ram_budget_mib,
    preflight,
    read_available_ram_mib,
)


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path)


@pytest.fixture
def kvm(tmp_path_factory):
    # outside the repository root, which must stay clean
    path = tmp_path_factory.mktemp("device") / "kvm-device"
    path.write_text("")
    return path


@pytest.fixture
def clean_locked_repo(root):
    init_repo(root)
    write_criteria(root)
    entry = freeze(root, "S1")
    commit_all(root, "docs(phase0): freeze S1 criteria")
    return root, entry


def run_preflight(root, mode, kvm, *, vm_ram_mib=2048, budget=8192, available=16384):
    preflight(
        root,
        "S1",
        mode,
        vm_ram_mib=vm_ram_mib,
        ram_budget_mib=budget,
        available_ram_mib=lambda: available,
        kvm_path=kvm,
    )


# --- RAM budget, both modes ------------------------------------------------


def test_default_ram_budget_is_8_gib_and_can_be_configured():
    assert DEFAULT_RAM_BUDGET_MIB == 8192
    assert configured_ram_budget_mib({}) == 8192
    assert configured_ram_budget_mib({"SPIKE_VM_RAM_BUDGET_MIB": "4096"}) == 4096
    with pytest.raises(PreflightError):
        configured_ram_budget_mib({"SPIKE_VM_RAM_BUDGET_MIB": "lots"})
    with pytest.raises(PreflightError):
        configured_ram_budget_mib({"SPIKE_VM_RAM_BUDGET_MIB": "0"})


@pytest.mark.parametrize("mode", ["shakedown", "record"])
def test_both_modes_refuse_when_planned_vm_memory_exceeds_the_budget(root, kvm, mode):
    with pytest.raises(PreflightError, match="budget"):
        run_preflight(root, mode, kvm, vm_ram_mib=9000, budget=8192)


@pytest.mark.parametrize("mode", ["shakedown", "record"])
def test_both_modes_refuse_when_the_memory_is_not_available(root, kvm, mode):
    with pytest.raises(PreflightError, match="available"):
        run_preflight(root, mode, kvm, vm_ram_mib=4096, available=3000)


def test_shakedown_runs_without_kvm_on_a_dirty_tree_and_unlocked_criteria(root, tmp_path):
    run_preflight(root, "shakedown", tmp_path / "no-kvm-here")


def meminfo(monkeypatch, tmp_path, text):
    """/proc/meminfo as read_available_ram_mib sees it."""
    path = tmp_path / "meminfo"
    path.write_text(text)
    monkeypatch.setattr(record_module, "Path", lambda _name: path)


def test_available_ram_is_memavailable_in_mib(monkeypatch, tmp_path):
    meminfo(monkeypatch, tmp_path, "MemTotal:  8388608 kB\nMemAvailable:  2097152 kB\n")

    assert read_available_ram_mib() == 2048


def test_a_meminfo_without_memavailable_refuses_the_run(monkeypatch, tmp_path):
    meminfo(monkeypatch, tmp_path, "MemTotal:  8388608 kB\nMemFree:  2097152 kB\n")

    with pytest.raises(PreflightError, match="MemAvailable"):
        read_available_ram_mib()


def test_an_unknown_mode_is_refused(root, kvm):
    with pytest.raises(PreflightError, match="unknown mode"):
        run_preflight(root, "rehearsal", kvm)


# --- record mode gate ------------------------------------------------------


def test_record_mode_passes_with_kvm_a_clean_tree_and_locked_criteria(clean_locked_repo, kvm):
    root, _ = clean_locked_repo

    run_preflight(root, "record", kvm)


def test_record_mode_refuses_without_kvm(clean_locked_repo, tmp_path):
    root, _ = clean_locked_repo

    with pytest.raises(PreflightError, match="kvm"):
        run_preflight(root, "record", tmp_path / "missing")


def test_record_mode_refuses_outside_a_git_work_tree(root, kvm):
    with pytest.raises(PreflightError, match="git work tree"):
        run_preflight(root, "record", kvm)


def test_record_mode_refuses_a_dirty_git_tree(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    (root / "stray.txt").write_text("x")

    with pytest.raises(PreflightError, match="dirty"):
        run_preflight(root, "record", kvm)


def test_record_mode_refuses_when_the_current_criteria_version_is_not_locked(root, kvm):
    init_repo(root)
    write_criteria(root)
    commit_all(root)

    with pytest.raises(PreflightError, match="locked"):
        run_preflight(root, "record", kvm)


def test_record_mode_refuses_when_a_newer_draft_amendment_exists(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    write_criteria(root, version=2)
    commit_all(root)

    with pytest.raises(PreflightError, match="locked"):
        run_preflight(root, "record", kvm)


def test_record_mode_ignores_a_newer_draft_of_another_spike(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    write_criteria(root, spike="S3", version=2)
    commit_all(root)

    run_preflight(root, "record", kvm)


def test_record_mode_refuses_when_the_locked_file_was_edited(clean_locked_repo, kvm):
    root, _ = clean_locked_repo
    path = root / CRITERIA / "S1.yaml"
    path.write_text(path.read_text() + "# edit\n")
    commit_all(root)

    with pytest.raises(PreflightError, match="locked"):
        run_preflight(root, "record", kvm)
