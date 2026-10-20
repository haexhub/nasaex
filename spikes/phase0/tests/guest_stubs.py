"""Stand-ins for the guest tools that the S2 guest scripts call (mount, findmnt, snapraid,
snapraid-btrfs, snapper, systemctl, btrfs, df), so the scripts' decisions run on the host.

Every stub appends its argv (and LC_ALL) as one JSON line to `calls.jsonl`. The mount table is a
JSON file {target: "ro" | "rw"}; `busy` lists targets whose read-only remount fails like a
filesystem with a file open for writing. Other behaviour comes from `stub.json`."""

import json
import os
import sys
from pathlib import Path

_COMMON = r"""import json, os, sys
from pathlib import Path
STUBS = Path(os.environ["STUB_DIR"])
CONF = json.loads((STUBS / "stub.json").read_text())
with open(STUBS / "calls.jsonl", "a") as log:
    log.write(json.dumps({"argv": [Path(sys.argv[0]).name, *sys.argv[1:]],
                          "lc_all": os.environ.get("LC_ALL")}) + "\n")
TABLE = STUBS / "mounts.json"
def table():
    return json.loads(TABLE.read_text())
def save(t):
    TABLE.write_text(json.dumps(t))
def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None
"""

STUBS = {
    "findmnt": r"""
t = table()
if "--target" in sys.argv:
    path = arg("--target").rstrip("/") or "/"
    hits = [m for m in t if path == m or path.startswith(m.rstrip("/") + "/")]
    print(max(hits, key=len) if hits else "/")
elif "--mountpoint" in sys.argv:
    target = arg("--mountpoint").rstrip("/")
    if target not in t:
        sys.exit(1)
    print(t[target] + ",relatime")
""",
    "mount": r"""
t = table()
args = sys.argv[1:]
if args[0] == "--bind":
    t[args[2].rstrip("/")] = "rw"
else:
    options, target = args[1].split(","), args[2].rstrip("/")
    if target not in t:
        print(f"mount: {target}: not mounted", file=sys.stderr); sys.exit(32)
    if "ro" in options and target in CONF.get("busy", []):
        print(f"mount: {target}: target is busy.", file=sys.stderr); sys.exit(32)
    t[target] = "ro" if "ro" in options else "rw"
save(t)
""",
    "umount": r"""
t = table()
t.pop(sys.argv[1].rstrip("/"), None)
save(t)
""",
    "sync": "",
    "touch": r"""
Path(sys.argv[-1]).touch()
""",
    "systemctl": "",
    "btrfs": "",
    "df": r"""
print("Avail"); print(CONF.get("avail", 4096))
""",
    "snapraid-btrfs": r"""
if "config" in sys.argv:
    print("parity /p/snapraid.parity")
    print("data arch0 /mnt/archive/arch0/.snapshots/3/snapshot/")
log = arg("-l")
if log:
    Path(log).write_text(CONF.get("log", ""))
args, command = sys.argv[1:], None
while args:
    if args[0] in ("-c", "-u", "-U", "-C", "-d", "-l"):
        args = args[2:]
    elif args[0].startswith("-"):
        args = args[1:]
    else:
        command = args[0]
        break
sys.exit(CONF.get("rc", {}).get(command, 0))
""",
    "snapraid": r"""
log = arg("-l")
if log:
    Path(log).write_text(CONF.get("diff_log", ""))
sys.exit(CONF.get("diff_rc", 0))
""",
    "snapper": r"""
config = arg("-c")
print(json.dumps({config: CONF.get("snapper", {}).get(config, [])}))
""",
}


def install(directory: Path, conf: dict | None = None, mounts: dict | None = None) -> Path:
    """Write the stubs into `directory` and return it (put it first in PATH)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in STUBS.items():
        path = directory / name
        # The interpreter by its own path: the nix build sandbox has no /usr/bin/env.
        path.write_text(f"#!{sys.executable}\n" + _COMMON + body)
        path.chmod(0o755)
    (directory / "stub.json").write_text(json.dumps(conf or {}))
    (directory / "mounts.json").write_text(json.dumps(mounts or {}))
    (directory / "calls.jsonl").write_text("")
    return directory


def configure(directory: Path, **conf) -> None:
    (directory / "stub.json").write_text(json.dumps(conf))


def calls(directory: Path, tool: str | None = None) -> list[list[str]]:
    lines = (directory / "calls.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    return [e["argv"] for e in entries if tool is None or e["argv"][0] == tool]


def lc_all(directory: Path, tool: str) -> list[str | None]:
    lines = (directory / "calls.jsonl").read_text().splitlines()
    return [e["lc_all"] for e in map(json.loads, lines) if e["argv"][0] == tool]


def mounts(directory: Path) -> dict:
    return json.loads((directory / "mounts.json").read_text())


def environment(directory: Path, **extra: str) -> dict:
    return {
        **os.environ,
        "PATH": f"{directory}:{os.environ['PATH']}",
        "STUB_DIR": str(directory),
        **extra,
    }
