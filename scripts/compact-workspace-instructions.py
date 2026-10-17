#!/usr/bin/env python3
"""Preserve the complete operations record and install a current small entrypoint."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import tempfile

MARKER = '<!-- console-workspace-entrypoint:v1 -->'


def compact(root, *, write=False):
    root = Path(root).resolve()
    path = root / 'AGENTS.md'
    original = path.read_bytes()
    text = original.decode('utf-8')
    if MARKER in text:
        return {'changed': False, 'notice': 'Workspace entrypoint is already compact.'}
    digest = hashlib.sha256(original).hexdigest()
    relative = Path('docs/operations-history') / ('workspace-' + digest[:16] + '.md')
    retained = []
    for title in ('Guardrails', 'AGC (Actually Gaming Cardshop) — Agent Working Rules',
                  'n100 Hardening Final Check (Mandatory Before Security Sign-Off)'):
        match = re.search(r'^## ' + re.escape(title) + r'\n.*?(?=^## |\Z)', text, re.M | re.S)
        if match:
            retained.append(match.group().strip())
    for start, end in (('<!-- BEGIN N100 AGENT CONSOLE -->', '<!-- END N100 AGENT CONSOLE -->'),
                       ('<!-- BEGIN AGENT CONSOLE GITHUB IDENTITY -->', '<!-- END AGENT CONSOLE GITHUB IDENTITY -->')):
        if start in text and end in text:
            retained.append(text[text.index(start):text.index(end) + len(end)])
    entry = f'''# Workspace agent entrypoint
{MARKER}

## Current navigation

- Agent Console runs in CT115 on N100 at `http://192.168.1.115:3210`. From an authorized host use `ssh n100` and `sudo pct exec 115 -- ...`.
- Resolve `/home/agentstage/.local/share/agent-console/releases/current` to find the selected source; inspect `/api/deploy/current` or `/healthz` rather than trusting a historical SHA. The legacy CT109 console is historical.
- Canonical skills and instructions live under `/home/fadekyun/codex` on N100. CT115 keeps scoped mirrors under `/home/agentstage/codex`. Read the relevant skill and repository `AGENTS.md` before acting.
- Start with one session. Preserve existing work, native conversations, credentials and permission boundaries. Use isolated worktrees for code changes. Existing explicit user authorization carries forward.
- AGC site/display work is distinct from unrelated workloads; general jobs remain on N100. Do not activate a laptop worker or change production systems merely to resolve a local build/test problem.

## Project operations and retained rules

The [complete operations manual]({relative.as_posix()}) preserves the previous instruction file byte for byte (SHA-256 `{digest}`). **Before project or infrastructure mutations, read its applicable project section and guardrails. All policy instructions remain applicable.** Dated deployment facts require current verification; they are not current configuration.

Use the manual's headings to locate Bushi/Bandai automation (CT102–104), AGC (CT106), travel (CT108), Home Assistant (CT969), FileHub (CT114), laptop presence, PiKVM, provider integration and operational recovery. Avoid rereading unrelated history into every task.

'''
    entry += '\n\n'.join(retained) + '\n'
    if write:
        archive = root / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists() and archive.read_bytes() != original:
            raise ValueError('Existing operations archive differs; preserve both versions')
        if not archive.exists():
            with archive.open('xb') as output:
                output.write(original)
            archive.chmod(0o600)
        descriptor, temporary = tempfile.mkstemp(prefix='.AGENTS-', dir=root)
        try:
            os.fchmod(descriptor, path.stat().st_mode & 0o777)
            with os.fdopen(descriptor, 'w') as output:
                output.write(entry)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'changed': write, 'before_bytes': len(original), 'after_bytes': len(entry.encode()),
            'archive': str(relative), 'sha256': digest}


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--write', action='store_true', help='archive and replace the workspace entrypoint')
    args = parser.parse_args()
    print(json.dumps(compact(args.root, write=args.write)))
