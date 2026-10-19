"""The S1 lab (vm-tests/s1/context.py): the server and client of the S1 VM test as the checks
drive them. The branch helpers run here against local directories through a machine that runs
the guest commands in a local shell; mounting, smbd and the client need the VMs, so for them
the order of the commands the lab sends is checked."""

import json
from pathlib import Path

import pytest
from helpers import LocalMachine, RecordingMachine, xattrs_supported
from s1.context import Lab, branches_with_mode, running_baseline, variant_options

from testlib.fixtures import Entry, Fixture, expected_manifest
from testlib.manifest import build_manifest, tree_hash
from testlib.recorder import Recorder

CONFIGURED = (
    "allow_other,fsname=cachepool,category.create=ff,moveonenospc=false,minfreespace=64M,"
    "passthrough.io=off,cache.files=off"
)
WHAT = "/mnt/pool:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC"


# tables.baseline of the criteria and what the running cache pool reports for it (the
# formats measured in probe T051h: minfreespace in bytes, branches as path=MODE joined by ":").
BASELINE = {
    "category.create": "ff",
    "archive_branch_mode": "NC",
    "branches": "/mnt/pool=RW:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC",
    "moveonenospc": "false",
    "minfreespace": "64M",
    "passthrough.io": "off",
    "cache.files": "off",
    "func.unlink": "epall",
}
RUNNING = {
    "category.create": "ff",
    "moveonenospc": "false",
    "minfreespace": "67108864",
    "passthrough.io": "off",
    "cache.files": "off",
    "func.unlink": "epall",
    "branches": "/mnt/pool=RW:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC",
}


def test_the_running_baseline_is_read_option_by_option_and_matches_its_table():
    seen, differences = running_baseline(BASELINE, RUNNING.__getitem__)

    assert seen == RUNNING
    assert differences == []


def test_a_differing_option_of_the_running_pool_is_named_with_both_values():
    _, differences = running_baseline(BASELINE, (RUNNING | {"cache.files": "auto-full"}).get)

    assert differences == ["cache.files is auto-full, the criteria say off"]


def test_minfreespace_is_compared_in_bytes():
    _, differences = running_baseline(BASELINE, (RUNNING | {"minfreespace": "64000000"}).get)

    assert differences == ["minfreespace is 64000000, the criteria say 64M (67108864 bytes)"]


def test_running_baseline_requires_all_archive_branches_and_modes():
    wrong_mode = "/mnt/pool=RW:/mnt/archive/arch0/branch=NC,4G:/mnt/archive/arch1/branch=RW"
    missing_archive = "/mnt/pool=RW:/mnt/archive/arch0/branch=NC"
    pool_override = "/mnt/pool=RW,1G:/mnt/archive/arch0/branch=NC:/mnt/archive/arch1/branch=NC"
    cases = {
        wrong_mode: [
            "archive branch /mnt/archive/arch1/branch has mode RW, the criteria say NC",
            f"branches is {wrong_mode}, the criteria say {BASELINE['branches']}",
        ],
        missing_archive: [
            f"branches is {missing_archive}, the criteria say {BASELINE['branches']}"
        ],
        pool_override: [f"branches is {pool_override}, the criteria say {BASELINE['branches']}"],
    }

    for branches, expected in cases.items():
        _, differences = running_baseline(BASELINE, (RUNNING | {"branches": branches}).get)

        assert differences == expected


def test_variant_options_replace_a_value_in_place_and_keep_everything_else():
    options = variant_options(CONFIGURED, {"category.create": "lus"})

    assert options == CONFIGURED.replace("category.create=ff", "category.create=lus")


def test_variant_options_append_an_option_the_mount_does_not_set():
    options = variant_options(CONFIGURED, {"func.unlink": "ff"})

    assert options == CONFIGURED + ",func.unlink=ff"


def test_variant_options_change_several_options_and_keep_flags_without_value():
    options = variant_options(CONFIGURED, {"passthrough.io": "rw", "cache.files": "auto-full"})

    assert options.split(",")[0] == "allow_other"
    assert "passthrough.io=rw" in options.split(",")
    assert "cache.files=auto-full" in options.split(",")
    assert "passthrough.io=off" not in options


def test_branches_with_mode_keeps_the_pool_branch_first_and_untouched():
    assert branches_with_mode(WHAT, "NC") == WHAT
    assert branches_with_mode(WHAT, None) == (
        "/mnt/pool:/mnt/archive/arch0/branch:/mnt/archive/arch1/branch"
    )
    assert branches_with_mode(WHAT, "RW").split(":")[1:] == [
        "/mnt/archive/arch0/branch=RW",
        "/mnt/archive/arch1/branch=RW",
    ]


@pytest.fixture
def local_lab(tmp_path, monkeypatch):
    if not xattrs_supported(tmp_path):
        pytest.skip("user xattrs are not supported on the test filesystem")
    roots = {}
    for key in ("pool", "arch0", "arch1", "arch2", "trash:arch0"):
        roots[key] = tmp_path / key.replace(":", "-")
        roots[key].mkdir()
    monkeypatch.chdir(tmp_path)
    machine = LocalMachine()
    lab = Lab(machine, machine, {k: str(v) for k, v in roots.items()}, Recorder(), user=None)
    return lab, roots


FIXTURE = Fixture(
    "lab",
    (
        Entry("d/pool.txt", "pool", size=10),
        Entry("d/arch.txt", "arch1", size=20, xattrs=(("user.k", "v"),)),
        Entry("both.txt", "pool", size=5),
        Entry("both.txt", "arch0", size=5),
        Entry("old.1.2", "trash:arch0", size=3),
    ),
)


def test_place_populates_each_branch_from_the_description(local_lab):
    lab, roots = local_lab

    lab.place(FIXTURE)

    for key, root in roots.items():
        assert build_manifest(root) == expected_manifest(FIXTURE, (key,)), key


def test_manifest_and_tree_hashes_read_the_branches_on_the_server(local_lab):
    lab, roots = local_lab
    lab.place(FIXTURE)

    assert lab.manifest("arch1") == build_manifest(roots["arch1"])
    assert lab.manifest("pool", "d") == build_manifest(roots["pool"] / "d")
    assert lab.manifest("arch2", "missing") == {}
    assert lab.tree_hashes(("arch0", "arch1")) == {
        "arch0": tree_hash(build_manifest(roots["arch0"])),
        "arch1": tree_hash(build_manifest(roots["arch1"])),
    }


def test_holders_name_the_share_branches_that_hold_a_path_never_the_trash(local_lab):
    lab, _ = local_lab
    lab.place(FIXTURE)

    assert lab.holders("both.txt") == ["pool", "arch0"]
    assert lab.holders("d/arch.txt") == ["arch1"]
    assert lab.holders("old.1.2") == []
    assert lab.holders("nowhere") == []


def test_remove_deletes_a_path_on_one_branch_only(local_lab):
    lab, _ = local_lab
    lab.place(FIXTURE)

    lab.remove("arch0", "both.txt")

    assert lab.holders("both.txt") == ["pool"]


def test_evidence_is_written_below_status_and_attached_to_the_running_scenario(local_lab):
    lab, _ = local_lab
    recorder = lab.recorder

    recorder.run("S1.4", lambda: lab.evidence("S1.4-unlink", {"effective": "epall"}))

    path = Path("status/S1.4-unlink.json")
    assert json.loads(path.read_text()) == {"effective": "epall"}
    (entry,) = recorder.scenarios()[0]["evidence"]
    assert entry["path"] == "status/S1.4-unlink.json"
    assert len(entry["sha256"]) == 64


def unit(field, value):
    return f"systemctl cat mnt-cache.mount | sed -n 's/^{field}=//p'", value


def test_a_variant_remounts_the_cache_pool_only_after_the_clients_and_smbd_let_go():
    server = RecordingMachine(dict([unit("What", WHAT), unit("Options", CONFIGURED)]))
    client = RecordingMachine()
    lab = Lab(server, client, {"pool": "/mnt/pool"}, Recorder())

    with lab.variant({"category.create": "lus"}):
        inside = list(server.commands)
    after = server.commands[len(inside) :]

    stop_smbd = inside.index("systemctl stop samba-smbd.service")
    stop_unit = inside.index("systemctl stop mnt-cache.mount")
    mount = next(i for i, c in enumerate(inside) if c.startswith("mergerfs "))
    assert any(c.startswith("umount /mnt/share") for c in client.commands[:2])
    assert stop_smbd < stop_unit < mount
    assert "category.create=lus" in inside[mount] and WHAT in inside[mount]
    assert inside[-1] == "systemctl start samba-smbd.service"
    assert after.index("umount /mnt/cache || true") < after.index("systemctl start mnt-cache.mount")
    assert after[-1] == "systemctl start samba-smbd.service"
    assert client.commands[-1].startswith("mkdir -p /mnt/share2")


def test_the_baseline_variant_leaves_the_mounts_alone():
    server, client = RecordingMachine(), RecordingMachine()
    lab = Lab(server, client, {"pool": "/mnt/pool"}, Recorder())

    with lab.variant({}):
        pass

    assert server.commands == [] and client.commands == []


def test_a_variant_without_the_no_create_mode_drops_it_from_the_archive_branches():
    server = RecordingMachine(dict([unit("What", WHAT), unit("Options", CONFIGURED)]))
    lab = Lab(server, RecordingMachine(), {"pool": "/mnt/pool"}, Recorder())

    with lab.variant({}, archive_mode=None):
        mount = next(c for c in server.commands if c.startswith("mergerfs "))

    assert "=NC" not in mount
    assert "/mnt/pool:/mnt/archive/arch0/branch:/mnt/archive/arch1/branch" in mount


class FailingMount(RecordingMachine):
    def succeed(self, command, **kwargs):
        out = super().succeed(command, **kwargs)
        if command.startswith("mergerfs "):
            raise RuntimeError("mergerfs: invalid option")
        return out


def test_a_variant_that_fails_to_mount_restores_the_baseline_mount_and_smbd():
    server = FailingMount(dict([unit("What", WHAT), unit("Options", CONFIGURED)]))
    lab = Lab(server, RecordingMachine(), {"pool": "/mnt/pool"}, Recorder())

    with pytest.raises(RuntimeError, match="invalid option"):
        with lab.variant({"cache.files": "bogus"}):
            pass

    assert server.commands[-2:] == [
        "systemctl start mnt-cache.mount",
        "systemctl start samba-smbd.service",
    ]


def test_effective_returns_the_option_value_without_the_line_end_of_the_guest_output():
    # The value goes into the required settings of the record (S1 shakedown, run 1: "epall\n").
    server = RecordingMachine({"user.mergerfs.func.unlink": "epall\n"})
    lab = Lab(server, RecordingMachine(), {"pool": "/mnt/pool"}, Recorder())

    assert lab.effective("func.unlink") == "epall"


def test_a_fresh_session_is_a_mount_of_its_own_that_is_unmounted_afterwards():
    client = RecordingMachine()
    lab = Lab(RecordingMachine(), client, {}, Recorder())

    with lab.fresh_session() as session:
        assert session.mountpoint == "/mnt/share3"
        assert session.path("a.bin") == "/mnt/share3/a.bin"
        assert len(client.commands) == 1
        assert "mount -t cifs //server/cache /mnt/share3" in client.commands[0]
        assert "nosharesock" in client.commands[0].rpartition("-o")[2]

    assert client.commands[-1] == "umount /mnt/share3"
    assert len(client.commands) == 2


def test_a_fresh_session_is_unmounted_when_the_block_raises():
    client = RecordingMachine()
    lab = Lab(RecordingMachine(), client, {}, Recorder())

    with pytest.raises(RuntimeError), lab.fresh_session():
        raise RuntimeError("the retry failed")

    assert client.commands[-1] == "umount /mnt/share3"
