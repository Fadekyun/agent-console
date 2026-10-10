#!/usr/bin/env python3
"""Validate local canonical skill content without sync, assignment, DB or provider calls."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.skills import validate_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if not args.root.is_dir():
        print(json.dumps({'ok': False, 'problems': ['canonical root unavailable']}))
        return 2
    try:
        problems = validate_catalog(args.root)
    except (OSError, ValueError):
        problems = ['canonical skill validation unavailable']
    print(json.dumps({'ok': not problems, 'problems': problems}, sort_keys=True))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
