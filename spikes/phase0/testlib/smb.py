"""SMB client driver of S1 (research D4): the share as a separate client VM sees it.

Two client implementations, so that no verdict depends on the quirks of one: the kernel client
(`mount.cifs`) for the tree operations (list, read, checksum, copy, rename, delete, xattr) and
the Samba user-space client (`smbclient`) for DOS attributes, which a kernel mount does not
expose as file metadata. Which command exposes which attribute and which `smb.conf` keys are
needed is measured by the probe vm-tests/probes/probe_smb_attrs.py (plan open item O12).

Manifests are built inside the client guest by testlib.manifest, the code that also reads the
branches on the server, so share and branch manifests compare field by field. The `machine`
arguments are driver machine objects (duck-typed). Standard library only."""

import json
import re
import shlex
from dataclasses import dataclass

from testlib.manifest import Manifest, from_json

# The four DOS attributes a client can change (smbclient `setmode` letters r, h, s, a).
DOS_READONLY = 0x01
DOS_HIDDEN = 0x02
DOS_SYSTEM = 0x04
DOS_ARCHIVE = 0x20

_SETMODE_CHANGE = re.compile(r"[+-][rsha]+")
_ATTRIBUTES_LINE = re.compile(r"^attributes:\s*[A-Za-z]*\s*\(([0-9a-fA-F]+)\)", re.MULTILINE)
_NT_STATUS = re.compile(r"NT_STATUS_[A-Z_]+")

_MANIFEST = """import sys
from pathlib import Path
from testlib.manifest import build_manifest, to_json
print(to_json(build_manifest(Path(sys.argv[1]))))"""
_LISTDIR = "import json, os, sys; print(json.dumps(sorted(os.listdir(sys.argv[1]))))"
_LISTXATTR = "import json, os, sys; print(json.dumps(sorted(os.listxattr(sys.argv[1]))))"
_SETXATTR = "import os, sys; os.setxattr(sys.argv[1], sys.argv[2], bytes.fromhex(sys.argv[3]))"
_GETXATTR = """import errno, json, os, sys
try:
    value = os.getxattr(sys.argv[1], sys.argv[2])
except OSError as error:
    print(json.dumps({"errno": errno.errorcode.get(error.errno, str(error.errno))}))
else:
    print(json.dumps({"value": value.hex()}))"""


class SmbError(Exception):
    """A client operation did not produce the expected answer."""


@dataclass(frozen=True)
class Share:
    server: str  # host name of the server VM on the test vlan
    name: str
    user: str
    password: str

    @property
    def unc(self) -> str:
        return f"//{self.server}/{self.name}"


def parse_dos_attributes(allinfo: str) -> int:
    """The DOS attribute mask from the output of smbclient `allinfo`."""
    match = _ATTRIBUTES_LINE.search(allinfo)
    if match is None:
        raise SmbError(f"no attributes line in allinfo output: {allinfo.strip()!r}")
    return int(match.group(1), 16)


def nt_status(output: str) -> str | None:
    """The first NT status that an smbclient output names, None if it names none; callers keep
    the whole output as evidence, since a later line may name another status."""
    match = _NT_STATUS.search(output)
    return match.group(0) if match else None


class CifsMount:
    """The share mounted by the kernel client; paths are relative to the mount point."""

    def __init__(self, machine, share: Share, mountpoint: str, options: tuple[str, ...] = ()):
        self.machine = machine
        self.share = share
        self.mountpoint = mountpoint
        self.options = options

    def path(self, rel: str = "") -> str:
        return f"{self.mountpoint}/{rel}" if rel else self.mountpoint

    def _q(self, rel: str = "") -> str:
        return shlex.quote(self.path(rel))

    def _python(self, code: str, *args: str) -> str:
        quoted = " ".join(shlex.quote(a) for a in args)
        return self.machine.succeed(f"python3 -c {shlex.quote(code)} {quoted}")

    def mount(self) -> None:
        options = ",".join(
            (f"username={self.share.user}", f"password={self.share.password}", *self.options)
        )
        self.machine.succeed(
            f"mkdir -p {self._q()} && mount -t cifs {shlex.quote(self.share.unc)} {self._q()} "
            f"-o {shlex.quote(options)}"
        )

    def umount(self) -> None:
        self.machine.succeed(f"umount {self._q()}")

    def manifest(self, rel: str = "") -> Manifest:
        """Manifest of everything below `rel` as the client sees it (testlib.manifest)."""
        return from_json(self._python(_MANIFEST, self.path(rel)))

    def listdir(self, rel: str = "") -> list[str]:
        return json.loads(self._python(_LISTDIR, self.path(rel)))

    def exists(self, rel: str) -> bool:
        return self.machine.execute(f"test -e {self._q(rel)}")[0] == 0

    def sha256(self, rel: str) -> str:
        """Read the whole file through the mount and return its SHA-256."""
        return self.machine.succeed(f"sha256sum < {self._q(rel)}").split()[0]

    def copy(self, src: str, dst: str) -> None:
        self.machine.succeed(f"cp -r -- {self._q(src)} {self._q(dst)}")

    def rename(self, src: str, dst: str) -> None:
        """Rename or move; `-T` never moves a directory into an existing one."""
        self.machine.succeed(f"mv -T -- {self._q(src)} {self._q(dst)}")

    def delete(self, rel: str) -> None:
        """Delete a file, or a directory with everything in it."""
        self.machine.succeed(f"rm -r -- {self._q(rel)}")

    def setxattr(self, rel: str, name: str, value: bytes) -> None:
        self._python(_SETXATTR, self.path(rel), name, value.hex())

    def getxattr(self, rel: str, name: str) -> bytes | None:
        """The value, None when the file has no such attribute; any other error is raised."""
        answer = json.loads(self._python(_GETXATTR, self.path(rel), name))
        if "value" in answer:
            return bytes.fromhex(answer["value"])
        if answer["errno"] == "ENODATA":
            return None
        raise SmbError(f"getxattr {name} of {rel}: {answer['errno']}")

    def listxattr(self, rel: str) -> list[str]:
        return json.loads(self._python(_LISTXATTR, self.path(rel)))


class SmbClient:
    """The share through smbclient; paths are relative to the share root."""

    def __init__(self, machine, share: Share):
        self.machine = machine
        self.share = share

    @staticmethod
    def _remote(rel: str) -> str:
        # smbclient splits `-c` at ';' and takes a double-quoted argument as one name.
        if '"' in rel or ";" in rel:
            raise ValueError(f"smbclient cannot address {rel!r} in a command")
        return f'"{rel}"'

    def _command(self, command: str) -> str:
        credentials = f"{self.share.user}%{self.share.password}"
        return (
            f"smbclient {shlex.quote(self.share.unc)} -U {shlex.quote(credentials)} "
            f"-c {shlex.quote(command)}"
        )

    def allinfo(self, rel: str) -> str:
        return self.machine.succeed(self._command(f"allinfo {self._remote(rel)}"))

    def dos_attributes(self, rel: str) -> int:
        return parse_dos_attributes(self.allinfo(rel))

    def set_dos_attributes(self, rel: str, change: str) -> None:
        """`change` adds (+) or removes (-) attributes by letter, for example `+h` or `-ra`."""
        if not _SETMODE_CHANGE.fullmatch(change):
            raise ValueError(f"not a setmode change: {change!r}")
        self.machine.succeed(self._command(f"setmode {self._remote(rel)} {change}"))

    def put(self, local: str, rel: str) -> tuple[int, str]:
        """Upload a client file; exit status and output, which names the NT status of a refused
        upload (a refusal is an outcome here, not an error)."""
        status, output = self.machine.execute(
            self._command(f"put {self._remote(local)} {self._remote(rel)}") + " 2>&1"
        )
        return status, output

    def sha256(self, rel: str) -> str:
        """Stream the whole file to standard output and return its SHA-256."""
        command = self._command(f"get {self._remote(rel)} -")
        return self.machine.succeed(f"{command} | sha256sum").split()[0]
