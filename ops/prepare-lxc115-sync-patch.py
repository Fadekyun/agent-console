#!/usr/bin/env python3
"""Render an inert exact-source patch; never install or invoke the host helper."""
import argparse
import difflib
import hashlib
from pathlib import Path

EXPECTED = '53d223dc58bece4434dfb467a2269895edc5865286cb877612a0c75cf5dbb1f2'
OLD_PATH = '/home/agentstage/.local/bin:/opt/agent-console/releases/2fb140e/scripts:/home/agentstage/.local/share/agent-console/venv/bin:/usr/local/bin:/usr/bin:/bin'
NEW_PATH = '/home/agentstage/.local/bin:/home/agentstage/bin:/home/agentstage/.local/share/agent-console/venv/bin:/usr/local/bin:/usr/bin:/bin'


def render(original: bytes) -> str:
    if hashlib.sha256(original).hexdigest() != EXPECTED:
        raise ValueError('authoritative helper source hash mismatch')
    text = original.decode()
    if text.count(OLD_PATH) != 1:
        raise ValueError('unexpected managed PATH source')
    text = text.replace(OLD_PATH, NEW_PATH)
    start = text.index('install_agentctl_link() {\n')
    end = text.index('\nrestart_service() {', start)
    body = Path(__file__).with_name('lxc115-entrypoint-delegate.py').read_text()
    replacement = '''install_agentctl_link() {
  # Selected release only. Missing installer during rollout fails closed.
  pct_exec runuser -u "$AGENT_USER" -- /usr/bin/python3 -I -B - "$AGENT_HOME" <<'ENTRYPOINT_PY'
''' + body + '''ENTRYPOINT_PY
}
'''
    return text[:start] + replacement + text[end:]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--diff', action='store_true')
    a = p.parse_args()
    original = a.source.read_bytes()
    modified = render(original)
    if a.diff:
        modified = ''.join(difflib.unified_diff(original.decode().splitlines(True), modified.splitlines(True),
                           fromfile='agent-console-lxc115-sync-context.original',
                           tofile='agent-console-lxc115-sync-context.proposed'))
    # New inert artifact only; no overwrite/apply/restart option.
    with a.output.open('x', encoding='utf-8') as output:
        output.write(modified)
    print('inert helper patch prepared; not installed or executed')


if __name__ == '__main__':
    main()
