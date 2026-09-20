# SPDX-License-Identifier: LGPL-3.0-only
"""Command-line interface for libweft."""

import argparse
import json
import sys
from pathlib import Path

from . import decompile, extract, rebuild
from .schedule import Uncuttable
from .si import Unmodelled
from .ss import SSError
from .verify import roundtrip
from .weave import Refused


def main():
    parser = argparse.ArgumentParser(
        prog="weft", description="LEGO Island SI ↔ Weaver SS"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help in [
        ("unweave", "SI to editable SS and media"),
        ("weave", "project directory to SI"),
        ("extract", "SI to standalone media files"),
    ]:
        command = commands.add_parser(name, help=help)
        command.add_argument("input")
        command.add_argument("output")
    command = commands.add_parser("verify", help="rebuild and compare every byte")
    command.add_argument("files", nargs="+")
    command.add_argument(
        "--log", help="original Weaver log (.Log or .Log.gz); one SI only"
    )
    command.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "verify" and args.log and len(args.files) != 1:
        parser.error("--log requires exactly one SI file")
    try:
        if args.command == "verify":
            files = []
            for name in args.files:
                path = Path(name)
                files.extend(
                    sorted(p for p in path.rglob("*") if p.suffix.upper() == ".SI")
                    if path.is_dir()
                    else [path]
                )
            if not files:
                parser.error("no SI files found")
            if args.log and len(files) != 1:
                parser.error("--log requires exactly one SI file")
        if args.command == "unweave":
            decompile(args.input).write(args.output)
        elif args.command == "weave":
            data = rebuild(args.input)
            with open(args.output, "xb") as file:
                file.write(data)
        elif args.command == "extract":
            print(f"{len(extract(args.input, args.output))} files")
        else:
            reports = []
            for path in files:
                report = roundtrip(path, args.log)
                report["file"] = str(path)
                reports.append(report)
                if not args.json:
                    print(f"{path}: identical ({report['bytes']:,} bytes)")
                    if "log" in report:
                        print(f"  {report['log']['emissions']:,} log emissions matched")
            if args.json:
                print(json.dumps(reports, indent=2))
    except (
        OSError,
        ValueError,
        KeyError,
        Unmodelled,
        Uncuttable,
        SSError,
        Refused,
    ) as error:
        parser.exit(1, f"weft: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
