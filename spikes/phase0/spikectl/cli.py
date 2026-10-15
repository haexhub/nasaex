"""spikectl command line: freeze, verify, verdict (the run verb is added with US6).

Scaffolding for the Phase 0 spikes, not product code."""

import argparse
import sys
from pathlib import Path

from spikectl.freeze import FreezeError, freeze
from spikectl.model import SPIKES, find_root
from spikectl.verdict import current_verdicts, format_matrix
from spikectl.verify import GROUPS, verify


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spikectl", description="Phase 0 verdict model tooling")
    parser.add_argument(
        "--root", type=Path, help="repository root (default: from the current directory)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    freeze_cmd = commands.add_parser("freeze", help="freeze the newest criteria file of a spike")
    freeze_cmd.add_argument("spike", choices=SPIKES)

    verify_cmd = commands.add_parser("verify", help="check lock, records, history and hygiene")
    verify_cmd.add_argument("--only", action="append", choices=GROUPS, help="run only this group")
    verify_cmd.add_argument("--base", help="git ref whose frozen.lock the current lock must extend")
    verify_cmd.add_argument(
        "--forbid", action="append", default=None, help="extra user or host name to reject"
    )

    commands.add_parser("verdict", help="print verdicts and the advisory gate matrix")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root or find_root(Path.cwd())

    if args.command == "freeze":
        try:
            entry = freeze(root, args.spike)
        except FreezeError as error:
            print(f"freeze: {error}", file=sys.stderr)
            return 1
        print(f"froze {entry.line()}")
        return 0

    if args.command == "verify":
        names = None if args.forbid is None else tuple(args.forbid)
        findings = verify(
            root,
            only=tuple(args.only) if args.only else None,
            forbidden_names=names,
            base_ref=args.base,
        )
        for finding in findings:
            print(f"{finding.check}: {finding.message}")
        print("verify: ok" if not findings else f"verify: {len(findings)} finding(s)")
        return 1 if findings else 0

    verdicts = current_verdicts(root)
    print(format_matrix(verdicts))
    return 1 if any(v.errors for v in verdicts.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
