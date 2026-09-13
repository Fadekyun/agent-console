#!/usr/bin/env python3
"""Authorized maintenance only: validate source before a release selection/rollback."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.admission import admission_lock
from agent_console.schema_compatibility import prepare_database_for_release, release_schema_version, SchemaCompatibilityError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--config-dir', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    try:
        with admission_lock(args.state_dir):
            result = prepare_database_for_release(args.database, release_schema_version(args.release), args.config_dir)
        print(f"schema transition prepared: {result['previous_schema']} -> {result['target_schema']}")
        return 0
    except (OSError, SchemaCompatibilityError) as error:
        print(f"schema transition refused: {error}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
