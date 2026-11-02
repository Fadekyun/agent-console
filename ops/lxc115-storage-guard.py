#!/usr/bin/env python3
"""Periodic storage guard for the Console host (CT115).

Run from cron or a systemd timer as the Console owner. It reclaims only
regenerable state, never touches a live session, records one line per run and
publishes ``storage-status.json`` next to the Console database.

    lxc115-storage-guard.py --check                 # report only
    lxc115-storage-guard.py                         # retention + reclaim now
    lxc115-storage-guard.py --propose               # write a digest-bound proposal
    lxc115-storage-guard.py --apply-plan <digest>   # execute exactly that proposal

The proposal path is how anything destructive should run: a proposal is approved
outside this tool (Discord), and execution refuses on an edited manifest, an
expired proposal, changed state, or a digest that was already executed.

Exit status: 0 healthy/warn, 2 storage critical or blocked, 1 when a cleanup step
failed (deletion errors or a concurrent run), so a timer cannot report success
after doing nothing.
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

from agent_console import storage, storage_guard, storage_proposal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='report without deleting')
    parser.add_argument('--tier2', action='store_true',
                        help='also reclaim aged tool overlays and provider state')
    parser.add_argument('--json', action='store_true', help='print the full report')
    parser.add_argument('--propose', action='store_true',
                        help='plan without deleting and write a digest-bound proposal')
    parser.add_argument('--apply-plan', metavar='DIGEST_OR_PATH',
                        help='execute exactly the proposal identified by this digest')
    parser.add_argument('--state-dir', default=os.getenv(
        'AGENT_CONSOLE_STATE_DIR', '/home/agentstage/.local/share/agent-console'))
    parser.add_argument('--tmp-dir', default='/tmp')
    args = parser.parse_args()

    state_dir = Path(args.state_dir)

    if args.propose:
        proposal = storage_proposal.build(state_dir, tmp_dir=args.tmp_dir,
                                          tier2=bool(args.tier2))
        path = storage_proposal.write(state_dir, proposal)
        summary = {
            'digest': proposal['digest'], 'proposal': str(path),
            'actions': len(proposal['actions']),
            'planned_gib': round(proposal['planned_bytes'] / 1024 ** 3, 2),
            'expires_in_days': round((proposal['expires_at'] - proposal['generated_at']) / 86400, 1),
            'level': proposal['level'], 'free_gib': proposal['free_gib'],
            'warnings': proposal['warnings'],
        }
        print(json.dumps(summary if args.json else summary, indent=1))
        return 0

    if args.apply_plan:
        try:
            report = storage_proposal.execute(state_dir, args.apply_plan)
        except storage_proposal.ProposalError as exc:
            print(json.dumps({'refused': str(exc), 'type': type(exc).__name__}, indent=1))
            return 2
        print(json.dumps(report.as_dict(), indent=1) if args.json
              else f"applied: removed={report.removed} freed={report.as_dict()['freed_gib']}GiB "
                   f"errors={len(report.errors)}")
        return 1 if report.errors else 0

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
    if report.errors:
        return 1
    return 2 if report.level in {'critical', 'blocked'} else 0


if __name__ == '__main__':
    sys.exit(main())
