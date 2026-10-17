"""SMB client driver: the kernel client (mount.cifs) for tree operations, the user-space client
(smbclient) for DOS attributes. Tree operations run here against a local directory standing in
for the mount point, through a machine that executes the commands in a local shell; mounting and
smbclient need the VMs, so for them the commands the driver sends are checked. Which command
exposes which attribute in the real stack is measured by the probe vm-tests/probes."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from helpers import xattrs_supported

from testlib.manifest import Item, build_manifest
from testlib.smb import (
    DOS_ARCHIVE,
    DOS_HIDDEN,
    CifsMount,
    Share,
    SmbClient,
    SmbError,
    parse_dos_attributes,
)

PHASE0 = Path(__file__).resolve().parents[1]
SHARE = Share(server="server", name="cache", user="spike", password="secret")


class LocalMachine:
    """Runs commands in a local bash, like the driver runs them in the guest; testlib is
    importable, as it is in the guest Python."""

    def __init__(self):
        self.commands: list[str] = []

    def execute(self, command, **_):
        self.commands.append(command)
        env = {**os.environ, "PYTHONPATH": str(PHASE0)}
        done = subprocess.run(
            ["bash", "-c", f"set -euo pipefail; {command}"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        return done.returncode, done.stdout

    def succeed(self, command, **_):
        status, out = self.execute(command)
        if status != 0:
            raise AssertionError(f"command failed ({status}): {command}")
        return out


class RecordingMachine:
    """Records commands; `outputs` maps a command prefix to the output it produces."""

    def __init__(self, outputs=None):
        self.commands: list[str] = []
        self.outputs = outputs or {}

    def succeed(self, command, **_):
        self.commands.append(command)
        return next((out for prefix, out in self.outputs.items() if prefix in command), "")

    def execute(self, command, **_):
        return 0, self.succeed(command)


@pytest.fixture
def share_dir(tmp_path):
    root = tmp_path / "mnt share"  # a space, so that quoting is exercised
    (root / "dir").mkdir(parents=True)
    (root / "dir" / "ä file.txt").write_bytes(b"content")
    return root


@pytest.fixture
def cifs(share_dir):
    return CifsMount(LocalMachine(), SHARE, str(share_dir))


# --- kernel client: mount, list, read, checksum, copy, rename, delete, xattr --------------------


def test_mount_uses_mount_cifs_with_the_share_user_and_extra_options():
    machine = RecordingMachine()
    mount = CifsMount(machine, SHARE, "/mnt/share", options=("actimeo=0",))

    mount.mount()
    mount.umount()

    mount_cmd, umount_cmd = machine.commands
    words = shlex.split(mount_cmd.split("&&")[-1])
    assert words[:5] == ["mount", "-t", "cifs", "//server/cache", "/mnt/share"]
    assert words[5:] == ["-o", "username=spike,password=secret,actimeo=0"]
    assert "mkdir -p /mnt/share" in mount_cmd
    assert shlex.split(umount_cmd) == ["umount", "/mnt/share"]


def test_manifest_is_built_in_the_client_with_the_branch_manifest_code(cifs, share_dir):
    assert cifs.manifest() == build_manifest(share_dir)
    assert cifs.manifest("dir") == build_manifest(share_dir / "dir")
    assert cifs.manifest()["dir/ä file.txt"].size == 7


def test_listdir_exists_and_sha256_read_through_the_mount(cifs):
    assert cifs.listdir() == ["dir"]
    assert cifs.listdir("dir") == ["ä file.txt"]
    assert cifs.exists("dir/ä file.txt")
    assert not cifs.exists("dir/missing")
    assert cifs.sha256("dir/ä file.txt") == (
        "ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73"
    )


def test_copy_rename_and_delete_change_the_tree_as_a_client_would(cifs, share_dir):
    cifs.copy("dir/ä file.txt", "copy.txt")
    cifs.copy("dir", "dir2")
    cifs.rename("dir2", "moved")
    cifs.rename("copy.txt", "moved/renamed.txt")
    cifs.delete("dir")

    assert sorted(build_manifest(share_dir)) == ["moved", "moved/renamed.txt", "moved/ä file.txt"]
    assert (share_dir / "moved" / "renamed.txt").read_bytes() == b"content"


def test_rename_of_a_directory_onto_an_existing_name_does_not_nest_it(cifs, share_dir):
    (share_dir / "target").mkdir()
    (share_dir / "target" / "keep.txt").write_text("")

    with pytest.raises(AssertionError):
        cifs.rename("dir", "target")  # mv -T: an existing non-empty target is not entered

    assert (share_dir / "dir").is_dir()
    assert not (share_dir / "target" / "dir").exists()


def test_xattrs_are_set_read_and_listed_as_bytes(cifs, share_dir):
    if not xattrs_supported(share_dir):
        pytest.skip("user xattrs are not supported on the test filesystem")

    cifs.setxattr("dir/ä file.txt", "user.tag", b"\x00v'1")

    assert cifs.getxattr("dir/ä file.txt", "user.tag") == b"\x00v'1"
    assert cifs.getxattr("dir/ä file.txt", "user.absent") is None
    assert cifs.listxattr("dir/ä file.txt") == ["user.tag"]
    assert cifs.manifest()["dir/ä file.txt"].xattrs == (("user.tag", "00762731"),)


def test_an_xattr_error_other_than_a_missing_attribute_is_raised(cifs):
    with pytest.raises(SmbError, match="dir/missing"):  # ENOENT, or ENOTSUP without xattrs
        cifs.getxattr("dir/missing", "user.tag")


def test_manifest_items_are_the_manifest_type(cifs):
    assert all(isinstance(item, Item) for item in cifs.manifest().values())


# --- user-space client: DOS attributes ------------------------------------------------------------

ALLINFO = """altname: AFILE~1.TXT
create_time:    Sat Oct 17 10:00:00 AM 2026 UTC
access_time:    Sat Oct 17 10:00:00 AM 2026 UTC
write_time:     Sat Oct 17 10:00:00 AM 2026 UTC
change_time:    Sat Oct 17 10:00:00 AM 2026 UTC
attributes: HA (22)
stream: [::$DATA], 7 bytes
"""


def test_dos_attributes_are_parsed_from_allinfo():
    assert parse_dos_attributes(ALLINFO) == DOS_HIDDEN | DOS_ARCHIVE
    assert parse_dos_attributes("attributes:  (80)\n") == 0x80


def test_allinfo_without_an_attributes_line_is_an_error():
    with pytest.raises(SmbError, match="attributes"):
        parse_dos_attributes("NT_STATUS_OBJECT_NAME_NOT_FOUND opening remote file\n")


def test_smbclient_reads_and_sets_dos_attributes_with_the_share_credentials():
    machine = RecordingMachine({"allinfo": ALLINFO})
    client = SmbClient(machine, SHARE)

    assert client.dos_attributes("dir/a file.txt") == 0x22
    client.set_dos_attributes("dir/a file.txt", "+h")
    client.set_dos_attributes("dir/a file.txt", "-ra")

    allinfo, plus_h, minus_ra = (shlex.split(c) for c in machine.commands)
    assert allinfo[:4] == ["smbclient", "//server/cache", "-U", "spike%secret"]
    assert allinfo[4:] == ["-c", 'allinfo "dir/a file.txt"']
    assert plus_h[4:] == ["-c", 'setmode "dir/a file.txt" +h']
    assert minus_ra[4:] == ["-c", 'setmode "dir/a file.txt" -ra']


def test_smbclient_checksums_a_file_streamed_to_standard_output():
    machine = RecordingMachine({"get ": "ab" * 32 + "  -\n"})
    client = SmbClient(machine, SHARE)

    assert client.sha256("big/file.bin") == "ab" * 32

    command = machine.commands[0]
    assert command.endswith("| sha256sum")
    assert shlex.split(command.removesuffix("| sha256sum"))[4:] == ["-c", 'get "big/file.bin" -']


@pytest.mark.parametrize("change", ["h", "+x", "+", "+h;rm x", ""])
def test_set_dos_attributes_refuses_anything_but_a_setmode_change(change):
    client = SmbClient(RecordingMachine(), SHARE)
    with pytest.raises(ValueError):
        client.set_dos_attributes("a.txt", change)


@pytest.mark.parametrize("path", ['a"b.txt', "a;b.txt"])
def test_smbclient_refuses_paths_it_cannot_quote_in_a_command(path):
    client = SmbClient(RecordingMachine(), SHARE)
    with pytest.raises(ValueError):
        client.dos_attributes(path)
