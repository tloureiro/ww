#!/usr/bin/env python3
"""Offline checks; --quick omits behavioral tests for an optional commit hook."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not shutil.which("fish"):
        parser.exit(1, "check: fish is required so completion checks cannot silently skip.\n")
    try:
        fish_version = subprocess.run(["fish", "--version"], text=True, capture_output=True, check=True).stdout.strip()
        fish_major = int(fish_version.split()[-1].split(".")[0])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError) as exc:
        parser.exit(1, f"check: cannot identify fish version: {exc}\n")
    if fish_major < 4:
        parser.exit(1, "check: fish 4 or newer is required for the completion key bindings.\n")
    print(fish_version, flush=True)
    print(f"Checking with Python {sys.version.split()[0]}", flush=True)
    try:
        for path in [root / "ww", *sorted(root.glob("tricks/*/run.py")),
                     *sorted(root.glob("tests/*.py")), Path(__file__).resolve()]:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        commands = [["fish", "--no-config", "-n", "completions/ww.fish"],
                    ["sh", "-n", "completions/fish-install.sh"],
                    ["sh", "-n", ".githooks/pre-commit"]]
        if not args.quick:
            commands.append([sys.executable, "-W", "default", "-m", "unittest", "discover", "-s", "tests", "-q"])
        for command in commands:
            print("+ " + " ".join(command), flush=True)
            subprocess.run(command, cwd=root, check=True)
    except (OSError, SyntaxError, UnicodeError, subprocess.CalledProcessError) as exc:
        print(f"check: {exc}", file=sys.stderr)
        return 1
    print("Checks passed" + (" (quick; behavioral tests omitted)" if args.quick else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
