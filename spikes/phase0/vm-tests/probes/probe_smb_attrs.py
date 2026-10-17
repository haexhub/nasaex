# Probe of the SMB attribute commands and smb.conf keys (task T039, plan open item O12). It
# measures, it does not judge, and it never writes a record. Three shares of the cache pool differ
# only in the attribute keys (Samba defaults, `store dos attributes` and `ea support` set to yes,
# and set to no). Per share a file of each origin (created through the share, so on the pool
# branch; created on the archive branch arch0) gets its DOS attributes changed with smbclient and
# a user xattr with the kernel client, through the S1 client driver testlib/smb.py. Measured:
# which client command shows which attribute (smbclient `allinfo`, the cifs pseudo-xattr
# `user.cifs.dosattrib`, the cifs user xattrs), what the server stores on the branch, whether it
# survives a restart of smbd, and whether the checksums of both clients agree. Result:
# probe-smb-attrs.json in the working directory of the driver and a PROBE-RESULT line in the log;
# the commands run are part of the result. Measured outcome: smb_attrs.results.md.
import json
import shlex
import time
from pathlib import Path

from testlib.smb import DOS_HIDDEN, DOS_READONLY, DOS_SYSTEM, CifsMount, Share, SmbClient

STARTED = time.monotonic()
POOL = "/mnt/pool"
ARCH0 = TOPOLOGY["archiveLayout"][0]["branch"]
SHARES = ("attrs-default", "attrs-on", "attrs-off")
KEYS = (
    "store dos attributes",
    "ea support",
    "map archive",
    "map hidden",
    "map system",
    "map readonly",
)
PASSWORD = "probe"
RESULT: dict = {"environment": {}, "commands": [], "vm_resources": TOPOLOGY["vmResources"]}


class Logged:
    """A driver machine whose commands become part of the result."""

    def __init__(self, machine):
        self.machine = machine
        self.name = machine.name

    def succeed(self, command, **kwargs):
        RESULT["commands"].append(f"{self.name}$ {command}")
        return self.machine.succeed(command, **kwargs)

    def execute(self, command, **kwargs):
        RESULT["commands"].append(f"{self.name}$ {command}")
        return self.machine.execute(command, **kwargs)


def attempt(fn):
    try:
        return {"ok": fn()}
    except Exception as error:  # noqa: BLE001 - a probe records every outcome
        return {"error": f"{type(error).__name__}: {error}"[:300]}


def stored(path: str) -> dict:
    """Every xattr of a branch file as the server stores it (name -> hex), read as root."""
    code = (
        "import json, os, sys; p = sys.argv[1]; "
        "print(json.dumps({n: os.getxattr(p, n).hex() for n in sorted(os.listxattr(p))}))"
    )
    return json.loads(srv.succeed(f"python3 -c {shlex.quote(code)} {shlex.quote(path)}"))


def dosattrib(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    return {"hex": raw.hex(), "le32": int.from_bytes(raw[:4], "little") if len(raw) >= 4 else None}


def has(measure: dict, bits: int, key: str = "ok") -> bool | None:
    value = measure.get(key)
    if isinstance(value, dict):
        value = value.get("le32")
    return None if value is None else value & bits == bits


# --- setup: topology, tiered-cache mounts, three shares, client mounts ---------------------------
start_all()
server.wait_for_unit("multi-user.target")
server.wait_until_succeeds('test -z "$(systemctl list-jobs --no-legend)"', timeout=120)
srv, cl = Logged(server), Logged(client)
for key, (machine, command) in {
    "server_kernel": (server, "uname -r"),
    "mergerfs": (server, "mergerfs -V 2>&1 | head -1"),
    "samba": (server, "smbd --version"),
    "client_kernel": (client, "uname -r"),
    "mount_cifs": (client, "mount.cifs -V"),
    "smbclient": (client, "smbclient --version"),
}.items():
    RESULT["environment"][key] = machine.succeed(command).strip()

srv.succeed("spike-mkfs all")
for mount in [POOL, *(a["data"] for a in TOPOLOGY["archiveLayout"])]:
    srv.succeed(f"mount {mount}")
srv.succeed("systemctl start mnt-base.mount mnt-cache.mount")
RESULT["effective_parameters"] = {
    share: {
        key: srv.succeed(
            f"testparm -s --section-name={share} --parameter-name={shlex.quote(key)} 2>/dev/null"
        ).strip()
        for key in KEYS
    }
    for share in SHARES
}
server.wait_for_unit("samba.target")
srv.succeed(f"printf '{PASSWORD}\\n{PASSWORD}\\n' | smbpasswd -s -a spike")
client.wait_for_unit("multi-user.target")

mounts, clients = {}, {}
for share in SHARES:
    for root in (POOL, ARCH0):
        srv.succeed(f"mkdir -m 0777 {root}/{share}")
    credentials = Share("server", share, "spike", PASSWORD)
    # actimeo=0: the probe measures what each command exposes, not the attribute cache
    mounts[share] = CifsMount(cl, credentials, f"/mnt/{share}", ("actimeo=0",))
    mounts[share].mount()
    clients[share] = SmbClient(cl, credentials)

# --- per share and origin: DOS attributes with smbclient, user xattrs with the kernel client -----
files = {}
for share in SHARES:
    cl.succeed(f"echo pool > {mounts[share].path(share + '/pool.txt')}")
    srv.succeed(f"echo archive > {ARCH0}/{share}/archive.txt")
    srv.succeed(f"chown spike {ARCH0}/{share}/archive.txt")
    files[share] = {
        "pool": (f"{share}/pool.txt", f"{POOL}/{share}/pool.txt"),
        "archive": (f"{share}/archive.txt", f"{ARCH0}/{share}/archive.txt"),
    }
    for _rel, branch_path in files[share].values():
        code = "import os, sys; os.setxattr(sys.argv[1], 'user.preset', b'from-branch')"
        srv.succeed(f"python3 -c {shlex.quote(code)} {branch_path}")


def text(measure: dict) -> dict:
    if isinstance(measure.get("ok"), bytes):
        measure["ok"] = measure["ok"].decode(errors="replace")
    return measure


def measure_file(share: str, cifs: CifsMount, smb: SmbClient, rel: str, branch: str) -> dict:
    """DOS attributes changed with smbclient, a user xattr with the kernel client, the readonly
    bit through the cifs pseudo-xattr; each read back by every command and on the branch."""
    m: dict = {"branch_before": attempt(lambda: stored(branch))}
    m["allinfo_initial"] = attempt(lambda: smb.dos_attributes(rel))
    m["cifs_dosattrib_initial"] = attempt(
        lambda: dosattrib(cifs.getxattr(rel, "user.cifs.dosattrib"))
    )
    m["setmode_plus_hs"] = attempt(lambda: smb.set_dos_attributes(rel, "+hs"))
    m["allinfo_after_setmode"] = attempt(lambda: smb.dos_attributes(rel))
    m["cifs_dosattrib_after_setmode"] = attempt(
        lambda: dosattrib(cifs.getxattr(rel, "user.cifs.dosattrib"))
    )
    m["branch_after_setmode"] = attempt(lambda: stored(branch))
    m["cifs_setxattr_user_probe"] = attempt(
        lambda: cifs.setxattr(rel, "user.probe", share.encode())
    )
    m["cifs_getxattr_user_probe"] = text(attempt(lambda: cifs.getxattr(rel, "user.probe")))
    m["cifs_getxattr_user_preset"] = text(attempt(lambda: cifs.getxattr(rel, "user.preset")))
    m["cifs_listxattr"] = attempt(lambda: cifs.listxattr(rel))
    m["branch_after_xattr"] = attempt(lambda: stored(branch))
    readonly = (m["allinfo_after_setmode"].get("ok", 0x20) | DOS_READONLY).to_bytes(4, "little")
    m["cifs_set_dosattrib_readonly"] = attempt(
        lambda: cifs.setxattr(rel, "user.cifs.dosattrib", readonly)
    )
    m["allinfo_after_cifs_set"] = attempt(lambda: smb.dos_attributes(rel))
    return m


measured: dict = {}
for share in SHARES:
    cifs, smb = mounts[share], clients[share]
    for origin, (rel, branch_path) in files[share].items():
        measured[(share, origin)] = measure_file(share, cifs, smb, rel, branch_path)
    rel = files[share]["pool"][0]
    RESULT.setdefault("checksums", {})[share] = {
        "cifs": attempt(lambda c=cifs, r=rel: c.sha256(r)),
        "smbclient": attempt(lambda s=smb, r=rel: s.sha256(r)),
        "server_branch": srv.succeed(f"sha256sum < {files[share]['pool'][1]}").split()[0],
    }
    RESULT.setdefault("cifs_manifest", {})[share] = attempt(
        lambda c=cifs, s=share: {
            p: [i.kind, i.size, oct(i.mode), list(map(list, i.xattrs))]
            for p, i in c.manifest(s).items()
        }
    )

# --- after a restart of smbd and a fresh client mount -------------------------------------------
srv.succeed("systemctl restart samba-smbd.service")
server.wait_for_unit("samba-smbd.service")
for share in SHARES:
    mounts[share].umount()
    mounts[share].mount()
    for origin, (rel, _branch_path) in files[share].items():
        m = measured[(share, origin)]
        m["allinfo_after_restart"] = attempt(lambda r=rel, s=clients[share]: s.dos_attributes(r))
        m["cifs_dosattrib_after_restart"] = attempt(
            lambda r=rel, c=mounts[share]: dosattrib(c.getxattr(r, "user.cifs.dosattrib"))
        )
        m["cifs_getxattr_user_probe_after_restart"] = text(
            attempt(lambda r=rel, c=mounts[share]: c.getxattr(r, "user.probe"))
        )

# --- summary --------------------------------------------------------------------------------------
HS = DOS_HIDDEN | DOS_SYSTEM
summary: dict = {"effective_parameters": RESULT["effective_parameters"]}
for (share, origin), m in measured.items():
    after = m["branch_after_xattr"].get("ok", {})
    summary[f"{share}/{origin}"] = {
        "setmode_hs_shown_by_allinfo": has(m["allinfo_after_setmode"], HS),
        "setmode_hs_shown_by_cifs_dosattrib": has(m["cifs_dosattrib_after_setmode"], HS),
        "setmode_hs_kept_after_smbd_restart": has(m["allinfo_after_restart"], HS),
        "cifs_dosattrib_set_readonly_ok": "ok" in m["cifs_set_dosattrib_readonly"],
        "cifs_dosattrib_readonly_shown_by_allinfo": has(m["allinfo_after_cifs_set"], DOS_READONLY),
        "user_xattr_roundtrip_cifs": m["cifs_getxattr_user_probe"].get("ok") == share,
        "user_xattr_kept_after_smbd_restart": m["cifs_getxattr_user_probe_after_restart"].get("ok")
        == share,
        "branch_xattr_shown_by_cifs": m["cifs_getxattr_user_preset"].get("ok") == "from-branch",
        "branch_xattr_names_after": sorted(after),
    }
for share, sums in RESULT["checksums"].items():
    summary[f"{share}/checksums_agree"] = (
        sums["cifs"].get("ok") == sums["smbclient"].get("ok") == sums["server_branch"]
    )
summary["probe_seconds"] = round(time.monotonic() - STARTED, 1)
RESULT["measured"] = {f"{share}/{origin}": m for (share, origin), m in measured.items()}
RESULT["summary"] = summary
Path("probe-smb-attrs.json").write_text(json.dumps(RESULT, indent=2, sort_keys=True, default=str))
print("PROBE-RESULT " + json.dumps(summary, sort_keys=True, default=str))
