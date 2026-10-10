"""Bind native Codex conversations to immutable Console identities.

Provider databases are read only. Legacy homes remain in place, including rollout
paths outside the home. No mtime-based selection or fresh-session fallback.
"""
from __future__ import annotations

import json
import re
import shlex
import sqlite3
import sys
from pathlib import Path

from .environment import read_private, write_private

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
SESSION_ID = re.compile(r'^sess-[0-9a-f]{32}$')


class RecoveryUnavailable(ValueError):
    pass


def binding_path(state_dir, session_id):
    if not SESSION_ID.fullmatch(session_id):
        raise RecoveryUnavailable('Invalid Console session identity')
    return state_dir / 'native-sessions' / (session_id + '.json')


def launcher_command(text):
    match = re.search(r'^exec (.*)', text, re.MULTILINE | re.DOTALL)
    if not match:
        raise RecoveryUnavailable('Pinned launcher has no command')
    argv = shlex.split(match.group(1))
    for index, value in enumerate(argv):
        if Path(value).name == 'environment_bootstrap.py':
            return argv[:index + 2], argv[index + 2:]
    return [], argv


def codex_resume_argv(argv, thread_id):
    try:
        start = next(i for i, value in enumerate(argv) if Path(value).name in {'codex', 'codex.js'})
    except StopIteration:
        raise RecoveryUnavailable('Pinned native Codex command is unavailable') from None
    command = argv[start:]
    result = [command[0]]
    valued = {'-c', '--config', '-C', '--cd', '-m', '--model', '-s', '--sandbox',
              '-a', '--ask-for-approval', '-p', '--profile', '-i', '--image', '--local-provider',
              '--enable', '--disable'}
    boolean = {'--approve-for-me', '--no-alt-screen', '--search', '--full-auto',
               '--dangerously-bypass-approvals-and-sandbox', '--oss'}
    index = 1
    while index < len(command):
        token = command[index]
        if token in valued:
            if index + 1 >= len(command):
                raise RecoveryUnavailable('Incomplete pinned Codex option')
            result.extend(command[index:index + 2]); index += 2
        elif token in boolean or (token.startswith('--') and '=' in token):
            result.append(token); index += 1
        elif token in {'resume', '--last', '--all'} or UUID.fullmatch(token):
            index += 1
        elif token.startswith('-'):
            raise RecoveryUnavailable('Unsupported pinned Codex option; review recovery command')
        else:
            # The original task/continuation prompt is already in native history.
            index += 1
    return result + ['resume', thread_id]


def _verify(home, thread_id, cwd):
    if not UUID.fullmatch(thread_id):
        raise RecoveryUnavailable('Invalid native conversation identity')
    try:
        with sqlite3.connect((home / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT id, source, cwd, rollout_path FROM threads WHERE id=?', (thread_id,)).fetchone()
        if row is None or row['source'] != 'cli' or Path(row['cwd']).resolve() != Path(cwd).resolve():
            raise RecoveryUnavailable('Native conversation does not match this session')
        rollout = Path(row['rollout_path'])
        with rollout.open() as handle:
            metadata = json.loads(handle.readline())
        if metadata.get('type') != 'session_meta' or metadata.get('payload', {}).get('id') != thread_id:
            raise RecoveryUnavailable('Native conversation history is missing or mismatched')
        return str(rollout)
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        if isinstance(exc, RecoveryUnavailable):
            raise
        raise RecoveryUnavailable('Native history unavailable; preserve files and select recovery') from None


def resolve_binding(state_dir, session, exports, text, *, persist=False):
    if session.get('tool') not in {'codex', 'codex-pro'}:
        raise RecoveryUnavailable('This provider needs manual native recovery')
    path = binding_path(state_dir, session['id'])
    binding = read_private(path)
    cwd = session.get('worktree') or session.get('repository')
    if not cwd:
        raise RecoveryUnavailable('Session repository is unavailable')
    if binding:
        if binding.get('session_id') != session['id']:
            raise RecoveryUnavailable('Recovery identity mismatch')
        home = Path(binding['home'])
        thread_id = binding['thread_id']
    else:
        value = exports.get('CODEX_HOME')
        if not value:
            raise RecoveryUnavailable('Pinned native home is unavailable')
        home = Path(value)
        roots = [state_dir / 'tool-overlays', state_dir / 'provider-state']
        if not any(home.resolve().is_relative_to(root.resolve()) for root in roots):
            raise RecoveryUnavailable('Global provider homes require explicit recovery selection')
        _, argv = launcher_command(text)
        explicit = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == 'resume' and UUID.fullmatch(argv[i + 1])]
        try:
            with sqlite3.connect((home / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
                candidates = [r[0] for r in conn.execute("SELECT id FROM threads WHERE source='cli' AND cwd=?", (cwd,))]
        except (OSError, sqlite3.Error):
            raise RecoveryUnavailable('Native history unavailable; preserve files and select recovery') from None
        if explicit:
            if len(set(explicit)) != 1:
                raise RecoveryUnavailable('Native recovery selection is ambiguous')
            thread_id = explicit[0]
        elif len(candidates) == 1:
            thread_id = candidates[0]
        else:
            raise RecoveryUnavailable('Native recovery selection is missing or ambiguous')
        binding = {'session_id': session['id'], 'home': str(home), 'thread_id': thread_id}
    binding['rollout_path'] = _verify(home, thread_id, cwd)
    if persist:
        write_private(path, binding)
    return binding


def resume_launcher(text, binding):
    prefix, argv = launcher_command(text)
    match = re.search(r'^exec ', text, re.MULTILINE)
    return text[:match.start()] + 'exec ' + shlex.join(prefix + codex_resume_argv(argv, binding['thread_id'])) + '\n'


def refresh_launcher_runtime(text):
    """Old launch assets may outlive their release's Python environment.

    Refresh only Console's bootstrap/interpreter. The native command, snapshot,
    conversation, credential references and permission flags remain pinned.
    """
    prefix, argv = launcher_command(text)
    if not prefix:
        return text
    prefix = [sys.executable, '-I', str(Path(__file__).with_name('environment_bootstrap.py')), prefix[-1]]
    match = re.search(r'^exec ', text, re.MULTILINE)
    return text[:match.start()] + 'exec ' + shlex.join(prefix + argv) + '\n'
