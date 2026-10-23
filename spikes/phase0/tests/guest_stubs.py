"""Stand-ins for the guest tools that the S2 guest scripts call (mount, findmnt, snapraid,
snapraid-btrfs, snapper, systemctl, btrfs, df), so the scripts' decisions run on the host.

Every stub appends its argv (and LC_ALL) as one JSON line to `calls.jsonl`. The mount table is a
JSON file {target: "ro" | "rw"}; `busy` lists targets whose read-only remount fails like a
filesystem with a file open for writing, `absent` paths that do not exist. `state.json` keeps what
the stubs create (the source device of a mount, the label mkfs gave a device) and `uuids`, the
filesystem UUID of a target that differs from the one of its source. Other behaviour comes from
`stub.json`."""

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
STATE = STUBS / "state.json"
def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"sources": {}, "labels": {}}
def keep(s):
    STATE.write_text(json.dumps(s))
def command(words):
    return next((a for a in sys.argv[1:] if a in words), None)
def respond(entry):
    log = arg("-l")
    if log:
        Path(log).write_text(entry.get("log", ""))
    sys.exit(entry.get("rc", 0))
"""

STUBS = {
    "findmnt": r"""
t = table()
if "--target" in sys.argv:
    path = arg("--target").rstrip("/") or "/"
    if path in CONF.get("absent", []):  # a path that does not exist: exit 1 without a word
        sys.exit(1)
    hits = [m for m in t if path == m or path.startswith(m.rstrip("/") + "/")]
    print(max(hits, key=len) if hits else "/")
elif "--mountpoint" in sys.argv:
    target = arg("--mountpoint").rstrip("/")
    if target not in t:
        sys.exit(1)
    if arg("-o") == "SOURCE":
        print(state()["sources"].get(target, "?"))
    elif arg("-o") == "UUID":  # of the filesystem: of its device, or of the fstab mount
        s = state()
        source = s["sources"].get(target, target).split("[")[0]
        print(s.get("uuids", {}).get(target, "uuid:" + source))
    else:
        print(t[target] + ",relatime")
""",
    "mount": r"""
t = table()
args = sys.argv[1:]
options = args[1].split(",") if args[0] == "-o" else []
places = args[2:] if args[0] == "-o" else args
if args[0] == "--bind":
    t[args[2].rstrip("/")] = "rw"
elif len(places) == 1 and "remount" not in options:  # a target of the fstab
    t[places[0].rstrip("/")] = "rw"
elif len(places) == 2:  # DEVICE TARGET
    t[places[1].rstrip("/")] = "rw"
    s = state()
    subvol = options[0].split("=")[1] if options and options[0].startswith("subvol=") else ""
    s["sources"][places[1].rstrip("/")] = os.path.realpath(places[0]) + (
        f"[/{subvol}]" if subvol else "")
    keep(s)
else:
    target = places[0].rstrip("/")
    if target not in t:
        print(f"mount: {target}: not mounted", file=sys.stderr); sys.exit(32)
    if "ro" in options and target in CONF.get("busy", []):
        print(f"mount: {target}: target is busy.", file=sys.stderr); sys.exit(32)
    t[target] = "ro" if "ro" in options else "rw"
save(t)
""",
    "umount": r"""
t = table()
if sys.argv[-1].rstrip("/") in CONF.get("umount_fails", []) and "-l" not in sys.argv:
    print(f"umount: {sys.argv[-1]}: target is busy.", file=sys.stderr); sys.exit(32)
t.pop(sys.argv[-1].rstrip("/"), None)
save(t)
""",
    "blkid": r"""
device = os.path.realpath(sys.argv[-1])
if "-p" in sys.argv:
    sys.exit(0 if device in CONF.get("formatted", []) else 2)
print(state()["labels"].get(device, ""))
""",
    "blockdev": r"""
print(CONF.get("sizes", {}).get(os.path.realpath(sys.argv[-1]), 0))
""",
    "mkfs.btrfs": r"""
s = state()
s["labels"][os.path.realpath(sys.argv[-1])] = arg("-L")
keep(s)
""",
    "udevadm": "",
    "dd": r"""
sys.exit(CONF.get("dd_rc", 0))
""",
    "spike-snapshots-init": "",
    "sync": "",
    "touch": r"""
Path(sys.argv[-1]).touch()
""",
    "systemctl": "",
    "btrfs": r"""
if sys.argv[1:3] == ["device", "stats"]:
    sys.exit(CONF.get("stats_rc", 0))
if sys.argv[1:3] == ["subvolume", "show"]:
    sys.exit(1 if sys.argv[-1] in CONF.get("not_subvolume", []) else 0)
""",
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
if command(("fix",)) in CONF.get("srb", {}):
    respond(CONF["srb"]["fix"])
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
word = command(("fix", "check", "sync", "scrub", "status", "diff"))
if word in CONF.get("snapraid", {}):
    respond(CONF["snapraid"][word])
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
