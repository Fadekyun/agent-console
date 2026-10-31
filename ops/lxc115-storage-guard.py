#!/usr/bin/env python3
"""Periodic storage guard for the Console host (CT115).

Run from cron or a systemd timer as the Console owner. It reclaims only
regenerable state, never touches a live session, records one line per run and
publishes ``storage-status.json`` next to the Console database.

    lxc115-storage-guard.py --check     # report only
    lxc115-storage-guard.py             # reclaim at warn/critical
    lxc115-storage-guard.py --tier2     # also reclaim aged provider history

Exit status: 0 healthy/warn, 2 storage critical or blocked, 1 unexpected error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_console import storage, storage_guard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='report without deleting')
    parser.add_argument('--tier2', action='store_true',
                        help='also reclaim aged tool overlays and provider state')
    parser.add_argument('--json', action='store_true', help='print the full report')
    parser.add_argument('--state-dir', default=os.getenv(
        'AGENT_CONSOLE_STATE_DIR', '/home/agentstage/.local/share/agent-console'))
    parser.add_argument('--tmp-dir', default='/tmp')
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    report = storage_guard.run(
        state_dir, tmp_dir=args.tmp_dir,
        tier2=True if args.tier2 else None,
        apply_actions=not args.check,
    )
    if not args.check:
        storage_guard.write_status(state_dir, report)

    line = (f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
            f"level={report.level} free={report.as_dict()['free_gib']}GiB "
            f"planned={len(report.actions)} removed={report.removed} "
            f"freed={report.as_dict()['freed_gib']}GiB "
            f"mode={'check' if args.check else 'apply'} errors={len(report.errors)}")
    logs = state_dir / 'logs'
    if logs.is_dir():
        with open(logs / 'storage-guard.log', 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    print(json.dumps(report.as_dict(), indent=1) if args.json else line)
    if report.errors:
        for error in report.errors[:10]:
            print(f'error: {error}', file=sys.stderr)
    return 2 if report.level in {'critical', 'blocked'} else 0


if __name__ == '__main__':
    sys.exit(main())
