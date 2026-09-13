#!/usr/bin/env python3
"""Install only current-release Console aliases; no legacy-source fallback."""
import argparse
from pathlib import Path
import sys

if not getattr(sys, "_agconsole_pinned", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.entrypoints import EntrypointError, install_entrypoints, bootstrap_entrypoints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--releases', type=Path, required=True)
    parser.add_argument('--bootstrap-source', type=Path)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if args.bootstrap_source and (args.database is None or args.config is None):
        parser.error('explicit bootstrap requires database and config paths')
    if not args.bootstrap_source and (args.database is not None or args.config is not None):
        parser.error('database and config are bootstrap-only')
    try:
        result = (bootstrap_entrypoints(args.bootstrap_source, args.home, args.state, args.releases, args.database, args.config)
                  if args.bootstrap_source else install_entrypoints(args.home, args.state, args.releases,
                        expected_selection=getattr(sys, "_agconsole_selection", None)))
        print('selected-release entrypoints installed: ' + str(len(result['aliases'])))
        return 0
    except (OSError, ValueError, EntrypointError):
        print('entrypoint installation unavailable; no legacy fallback', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
