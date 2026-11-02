#!/usr/bin/env python3
"""Bi-weekly storage proposal and approval loop for the Console host.

The guard decides what is eligible and the proposal layer makes that decision
cryptographically specific. This script joins them to the operator:

    propose:  plan -> (DeepSeek summary) -> (Sol critique) -> Discord card with an
              approval link that names the proposal digest
    poll:     ask n8n which digests were approved, execute exactly those, report
              the result back to Discord and mark the approval consumed

Nothing here invents a deletion path. Every delete happens through
``storage_proposal.execute``, which refuses edited manifests, expired proposals,
drifted state, foreign state roots and replays.

Configuration lives in ``~/.config/agent-console/storage-loop.env`` (mode 600):

    N8N_LOOP_BASE_URL            http://192.168.1.73   (API calls from this host)
    N8N_PUBLIC_BASE_URL          https://n8n.example    (used inside the approval link)
    N8N_LOOP_APPROVE_PATH        ac-storage-approve-<token>
    N8N_LOOP_LIST_PATH           ac-storage-approved-<token>
    N8N_LOOP_MARK_PATH           ac-storage-mark-<token>
    DISCORD_WEBHOOK_URL          channel webhook used for notifications
    DISCORD_OWNER_ID             the only approver accepted at execution time
    OPENROUTER_API_KEY           used for the cheap summary model
    OPENROUTER_MODEL             default deepseek/deepseek-v4.1-flash-1017
    STORAGE_LOOP_SOL             1 to run the local Sol critique (default 1)

Usage:

    ac-storage-loop.py --propose [--dry-run] [--tier2]
    ac-storage-loop.py --poll [--dry-run]

Exit status: 0 nothing to do or success, 1 an action failed, 2 refused/blocked.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_console import storage, storage_proposal  # noqa: E402

ENV_FILE = Path(os.getenv('STORAGE_LOOP_ENV', Path.home() / '.config/agent-console/storage-loop.env'))
LOG_FILE = Path(os.getenv('AGENT_CONSOLE_STATE_DIR', Path.home() / '.local/share/agent-console')) / 'logs/storage-loop.log'
SOL_TIMEOUT = 900


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def settings() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            if key.startswith('export '):
                key = key[7:].strip()
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request(url: str, *, method: str = 'GET', payload: dict | None = None,
            headers: dict | None = None, timeout: int = 30) -> str:
    data = None
    merged = {'Accept': 'application/json'}
    if headers:
        merged.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode()
        merged['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=merged, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode('utf-8', 'replace')


def deepseek_summary(config: dict[str, str], proposal: dict) -> str:
    """Ask the cheap model to explain the plan in operator language."""
    key = config.get('OPENROUTER_API_KEY')
    if not key:
        return 'summary unavailable: OPENROUTER_API_KEY is not configured'
    model = config.get('OPENROUTER_MODEL', 'deepseek/deepseek-v4.1-flash-1017')
    actions = proposal['actions']
    lines = [
        f"host={proposal['host']} state_root={proposal['state_root']}",
        f"level={proposal['level']} free_gib={proposal['free_gib']}",
        f"actions={len(actions)} planned_gib={round(proposal['planned_bytes'] / 1024 ** 3, 2)}",
        "paths:",
    ]
    for action in actions[:80]:
        lines.append(f"  - {action['kind']} tier{action['tier']} "
                     f"{round(action['bytes'] / 1024 ** 2, 1)}MiB {action['path']}")
    prompt = (
        'You are summarising a proposed disk cleanup for the operator of a small homelab '
        'console host. Be concrete and short (max 120 words, no preamble). State: what is '
        'being removed, what the operator stops being able to do afterwards, and whether the '
        'plan looks safe for the listed kinds. Do not suggest new paths.\n\n'
        + '\n'.join(lines)
    )
    body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 320, 'temperature': 0.2}
    try:
        raw = request('https://openrouter.ai/api/v1/chat/completions', method='POST', payload=body,
                      headers={'Authorization': f'Bearer {key}'}, timeout=120)
        reply = json.loads(raw)['choices'][0]['message']['content'].strip()
        return reply or 'summary unavailable: empty completion'
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as exc:
        return f'summary unavailable: {type(exc).__name__}'


def sol_review(config: dict[str, str], proposal: dict) -> str:
    """Local Sol critique of the concrete plan; advisory only."""
    if config.get('STORAGE_LOOP_SOL', '1') != '1':
        return 'skipped (STORAGE_LOOP_SOL=0)'
    brief = {
        'digest': proposal['digest'],
        'host': proposal['host'],
        'state_root': proposal['state_root'],
        'policy': proposal['policy'],
        'actions': proposal['actions'],
        'planned_gib': round(proposal['planned_bytes'] / 1024 ** 3, 2),
    }
    prompt = (
        'You are reviewing a proposed disk cleanup before a human approves it. The list below '
        'comes from a deterministic planner and cannot be changed by you. The planner already '
        'enforces, and you should assume it enforced: current/canary releases plus the newest '
        'three are kept; live sessions (reserved/attached/detached) and their overlays and '
        'contexts are excluded; nothing written in the last hour is eligible; paths are '
        'contained in the state root; harness assessments keep the newest two plus the one '
        'latest.json names. Reply with at most 90 words: VERDICT (ok / object) then, only if '
        'you object, the specific path and the rule it violates. Do not object merely because '
        'you cannot see the planner code, and do not propose additional paths.\n\n'
        + json.dumps(brief, indent=1)[:12000]
    )
    command = ['codex', 'exec', '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort="low"',
               '-c', 'approval_policy="never"', '-s', 'read-only', '--skip-git-repo-check',
               '-C', '/tmp', '-']
    try:
        result = subprocess.run(command, input=prompt, capture_output=True, text=True,
                                timeout=SOL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'review unavailable: {type(exc).__name__}'
    if result.returncode:
        return f'review unavailable: exit {result.returncode}'
    text = result.stdout.strip().splitlines()
    tail = [line for line in text if line.strip()][-6:]
    return ' '.join(tail)[:900] or 'review unavailable: empty output'


def approve_url(config: dict[str, str], digest: str) -> str:
    # The link is opened by the operator, not by this host: prefer the public
    # hostname from anywhere, while API calls stay on the LAN address.
    base = (config.get('N8N_PUBLIC_BASE_URL') or config.get('N8N_LOOP_BASE_URL', '')).rstrip('/')
    path = config.get('N8N_LOOP_APPROVE_PATH', '')
    owner = config.get('DISCORD_OWNER_ID', '')
    query = urllib.parse.urlencode({'digest': digest, 'approver': owner})
    return f'{base}/webhook/{path}?{query}'


def loop_url(config: dict[str, str], key: str) -> str:
    return f"{config.get('N8N_LOOP_BASE_URL', '').rstrip('/')}/webhook/{config.get(key, '')}"


def post_discord(config: dict[str, str], content: str, *, dry_run: bool,
                 quiet: bool = False) -> bool:
    if quiet:
        return True
    if dry_run:
        print('--- discord card (dry run) ---')
        print(content)
        return True
    url = config.get('DISCORD_WEBHOOK_URL')
    if not url:
        log('discord: DISCORD_WEBHOOK_URL is not configured')
        return False
    try:
        request(url, method='POST', payload={'content': content[:1900]}, timeout=30)
        return True
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f'discord: post failed: {type(exc).__name__}')
        return False


def propose(args: argparse.Namespace) -> int:
    config = settings()
    state = Path(args.state_dir)
    proposal = storage_proposal.build(state, tmp_dir=args.tmp_dir,
                                      tier2=bool(args.tier2), host=None)
    if not proposal['actions']:
        log(f"propose: nothing eligible (level={proposal['level']} "
            f"free={proposal['free_gib']}GiB)")
        return 0
    path = storage_proposal.write(state, proposal)
    digest = proposal['digest']
    summary = deepseek_summary(config, proposal)
    verdict = sol_review(config, proposal)
    objections = verdict.lower().startswith('object') or 'object' in verdict.lower()[:24]
    card = [
        f"**Storage cleanup proposal** — {len(proposal['actions'])} items, "
        f"{round(proposal['planned_bytes'] / 1024 ** 3, 2)} GiB",
        f"`{digest[:16]}…` · level {proposal['level']} · {proposal['free_gib']} GiB free now · "
        f"expires in 7 days",
        '',
        f"Summary: {summary}",
        '',
        f"Sol: {verdict}",
    ]
    if objections:
        card.append('')
        card.append('Sol objected — approval link withheld. Reply here to adjust the policy.')
    else:
        card.append('')
        card.append(f'Approve: {approve_url(config, digest)}')
        card.append('Approving executes exactly this digest; a changed plan is refused.')
    log(f"propose: digest={digest} actions={len(proposal['actions'])} "
        f"gib={round(proposal['planned_bytes'] / 1024 ** 3, 2)} "
        f"level={proposal['level']} objections={objections} file={path.name}")
    post_discord(config, '\n'.join(card), dry_run=args.dry_run, quiet=args.no_post)
    return 0


def poll(args: argparse.Namespace) -> int:
    config = settings()
    state = Path(args.state_dir)
    owner = config.get('DISCORD_OWNER_ID', '')
    try:
        raw = request(loop_url(config, 'N8N_LOOP_LIST_PATH'), timeout=30)
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f'poll: approvals unavailable: {type(exc).__name__}')
        return 1
    try:
        rows = json.loads(raw or '[]')
    except ValueError:
        log('poll: approvals response was not JSON')
        return 1
    pending = [row for row in rows if isinstance(row, dict) and row.get('digest')]
    if not pending:
        log('poll: no approved proposals')
        return 0
    failed = 0
    for row in pending:
        digest = row['digest']
        approver = str(row.get('approver') or '')
        if owner and approver != owner:
            log(f'poll: digest={digest[:16]}… refused: approver {approver!r} is not the owner')
            mark(config, digest, 'refused', 'approver is not the configured owner', args.dry_run)
            continue
        if args.dry_run:
            log(f'poll: digest={digest[:16]}… would execute (dry run)')
            continue
        try:
            report = storage_proposal.execute(state, digest)
        except storage_proposal.ProposalError as exc:
            log(f'poll: digest={digest[:16]}… refused: {type(exc).__name__}: {exc}')
            mark(config, digest, 'refused', f'{type(exc).__name__}: {exc}'[:400], args.dry_run)
            post_discord(config, f"**Storage cleanup refused** `{digest[:16]}…`: "
                                 f"{type(exc).__name__} — nothing was deleted.",
                     dry_run=args.dry_run, quiet=args.no_post)
            failed += 1
            continue
        note = f"removed={report.removed} freed={report.as_dict()['freed_gib']}GiB errors={len(report.errors)}"
        mark(config, digest, 'executed', note, args.dry_run)
        log(f'poll: digest={digest[:16]}… {note}')
        post_discord(config, f"**Storage cleanup done** `{digest[:16]}…` — {note}; "
                             f"{report.as_dict()['free_gib']} GiB free now.",
                     dry_run=args.dry_run, quiet=args.no_post)
    return 1 if failed else 0


def mark(config: dict[str, str], digest: str, status: str, note: str, dry_run: bool) -> None:
    url = loop_url(config, 'N8N_LOOP_MARK_PATH')
    query = urllib.parse.urlencode({'digest': digest, 'status': status, 'note': note[:400]})
    if dry_run:
        print(f'--- would mark {digest[:16]}… {status} ---')
        return
    try:
        request(f'{url}?{query}', timeout=30)
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f'mark: failed for {digest[:16]}…: {type(exc).__name__}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--propose', action='store_true', help='plan and notify; deletes nothing')
    parser.add_argument('--poll', action='store_true', help='execute approved digests')
    parser.add_argument('--dry-run', action='store_true',
                        help='print the plan and intended actions; delete and post nothing')
    parser.add_argument('--no-post', action='store_true',
                        help='execute normally but stay quiet on Discord (for checks)')
    parser.add_argument('--tier2', action='store_true', help='include provider-history tier')
    parser.add_argument('--state-dir', default=os.getenv(
        'AGENT_CONSOLE_STATE_DIR', str(Path.home() / '.local/share/agent-console')))
    parser.add_argument('--tmp-dir', default='/tmp')
    args = parser.parse_args()
    if args.propose == args.poll:
        parser.error('choose exactly one of --propose or --poll')
    if not Path(args.state_dir).is_dir():
        print(f'state dir not found: {args.state_dir}', file=sys.stderr)
        return 2
    return propose(args) if args.propose else poll(args)


if __name__ == '__main__':
    sys.exit(main())
