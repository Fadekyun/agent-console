"""Bounded, read-only Codex tool history from an identity-bound native rollout."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from .environment import read_private
from .logging_config import redact_secrets

MAX_SCAN = 16 * 1024 * 1024
MAX_LINE = 4 * 1024 * 1024
MAX_OUTPUT = 256 * 1024
IDENTITY = re.compile(r'^sess-[0-9a-f]{32}$')


class HistoryUnavailable(ValueError):
    pass


def _rollout(settings, session):
    if session.get('tool') not in {'codex', 'codex-pro'}:
        raise HistoryUnavailable('Tool history is currently available for Codex sessions only.')
    if session.get('execution_kind', 'interactive') != 'interactive' or not IDENTITY.fullmatch(session['id']):
        raise HistoryUnavailable('Native tool history is unavailable for this session type.')
    state = settings.state_dir
    binding = read_private(state / 'native-sessions' / (session['id'] + '.json'))
    snapshot = read_private(state / 'environment-launches' / (session['id'] + '.json')) or {}
    if binding is not None and not isinstance(binding, dict) or not isinstance(snapshot, dict):
        raise HistoryUnavailable('Pinned native metadata is invalid.')
    if not isinstance(snapshot.get('environment', {}), dict):
        raise HistoryUnavailable('Pinned native environment is invalid.')
    home_value = binding.get('home') if binding else snapshot.get('environment', {}).get('CODEX_HOME')
    if not isinstance(home_value, str) or not home_value:
        raise HistoryUnavailable('No pinned native home is recorded for this session.')
    home = Path(home_value).resolve()
    if not any(home.is_relative_to((state / root).resolve()) for root in ('tool-overlays', 'provider-state')):
        raise HistoryUnavailable('Global or external native homes need explicit recovery selection.')
    cwd = session.get('worktree') or session.get('repository')
    try:
        with sqlite3.connect((home / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            if binding:
                if binding.get('session_id') != session['id']:
                    raise HistoryUnavailable('Native conversation identity does not match.')
                rows = db.execute('SELECT id,cwd,rollout_path FROM threads WHERE id=? AND source=?',
                                  (binding.get('thread_id'), 'cli')).fetchall()
            else:
                rows = db.execute('SELECT id,cwd,rollout_path FROM threads WHERE cwd=? AND source=?', (cwd, 'cli')).fetchall()
        if len(rows) != 1 or not cwd or Path(rows[0]['cwd']).resolve() != Path(cwd).resolve():
            raise HistoryUnavailable('Native conversation selection is missing or ambiguous.')
        row = rows[0]
        path = Path(row['rollout_path']).resolve()
        if not path.is_relative_to(home / 'sessions'):
            raise HistoryUnavailable('Native history is outside the pinned session home.')
        handle = path.open('rb')
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            handle.close()
            raise HistoryUnavailable('Native history is not a regular file.')
        try:
            header = json.loads(handle.readline(MAX_LINE + 1))
        except Exception:
            handle.close()
            raise
        if not isinstance(header, dict) or header.get('type') != 'session_meta' or not isinstance(header.get('payload'), dict) or header['payload'].get('id') != row['id']:
            handle.close()
            raise HistoryUnavailable('Native history identity does not match.')
        return handle, row['id']
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        raise HistoryUnavailable('Native history cannot be verified for this session.') from None


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(item.get('text', '') for item in value if isinstance(item, dict) and isinstance(item.get('text'), str))
    return json.dumps(value, ensure_ascii=False)


def _exit_codes(text):
    codes = []

    def visit(value):
        if isinstance(value, dict):
            code = value.get('exit_code')
            if isinstance(code, int) and not isinstance(code, bool):
                codes.append(code)
            for key, item in value.items():
                if key not in {'output', 'text'}:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for line in text.splitlines():
        try:
            visit(json.loads(line))
        except (ValueError, TypeError):
            match = re.search(r'Process exited with code (-?\d+)', line)
            if match:
                codes.append(int(match[1]))
    return codes[:100]


def tool_calls(settings, session, *, before=None, thread=None, limit=50):
    try:
        handle, identity = _rollout(settings, session)
    except (HistoryUnavailable, ValueError, OSError):
        return {'available': False, 'notice': 'Verified native tool history is unavailable. Codex needs one identity-bound conversation in its pinned home; other providers are not yet supported.', 'calls': [], 'next_cursor': None}
    with handle:
        size = os.fstat(handle.fileno()).st_size
        if thread is not None and thread != identity:
            raise ValueError('Native conversation changed; refresh tool history')
        end = size if before is None else before
        if not 0 <= end <= size:
            raise ValueError('Invalid tool history cursor')
        # Keep bounded look-ahead on older pages: parallel calls can return
        # after the next call's cursor, so their output may be on a newer page.
        scan_end = min(size, end + MAX_LINE)
        start = max(0, scan_end - MAX_SCAN)
        handle.seek(start)
        raw = handle.read(scan_end - start)
    lines = raw.splitlines(keepends=True)
    position, records = start, []
    for number, line in enumerate(lines):
        offset = position
        position += len(line)
        if number == 0 and start or len(line) > MAX_LINE or not line.endswith(b'\n'):
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(record, dict) and record.get('type') == 'response_item' and isinstance(record.get('payload'), dict):
            records.append((offset, record))
    outputs, calls, seen = {}, [], set()
    cursor = start + len(lines[0]) if start and lines else 0
    for offset, record in reversed(records):
        payload = record['payload']
        kind, call_id = payload.get('type'), payload.get('call_id')
        if not isinstance(call_id, str):
            continue
        if kind in {'function_call_output', 'custom_tool_call_output'}:
            outputs[call_id] = (offset, record)
        elif kind in {'function_call', 'custom_tool_call'} and offset < end and call_id not in seen:
            seen.add(call_id)
            output_offset, output = outputs.get(call_id, (None, None))
            text = _text(output['payload'].get('output', '')) if output else ''
            duration = None
            if output:
                try:
                    duration = max(0, (datetime.fromisoformat(output['timestamp'].replace('Z', '+00:00')) - datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))).total_seconds())
                except (ValueError, KeyError, TypeError):
                    pass
            safe = redact_secrets(text)
            calls.append({'id': call_id, 'tool': str(payload.get('name', 'unknown'))[:100],
                          'at': record.get('timestamp'), 'duration_seconds': duration,
                          'status': 'returned' if output else 'output unavailable',
                          'exit_codes': _exit_codes(text), 'output_offset': output_offset,
                          'preview': safe[:2048], 'truncated': len(safe) > 2048})
            cursor = offset
            if len(calls) >= limit:
                break
    return {'available': True, 'thread': identity, 'calls': calls,
            'next_cursor': cursor if cursor > 0 else None,
            'notice': 'Read-only native records. Nonzero command exits can be expected, such as searches with no matches. Missing output is not proof that a tool is still running.'}


def tool_output(settings, session, *, offset, call_id, thread):
    handle, identity = _rollout(settings, session)
    with handle:
        if identity != thread or not 0 <= offset < os.fstat(handle.fileno()).st_size:
            raise ValueError('Native tool output changed; refresh history')
        if offset:
            handle.seek(offset - 1)
            if handle.read(1) != b'\n':
                raise ValueError('Invalid native output offset')
        handle.seek(offset)
        raw = handle.readline(MAX_LINE + 1)
    if len(raw) > MAX_LINE:
        raise ValueError('Native output exceeds the bounded reader limit')
    try:
        record = json.loads(raw)
        payload = record.get('payload', {}) if isinstance(record, dict) else {}
    except (ValueError, UnicodeError):
        raise ValueError('Native output is incomplete') from None
    if not isinstance(payload, dict) or payload.get('type') not in {'function_call_output', 'custom_tool_call_output'} or payload.get('call_id') != call_id:
        raise ValueError('Native output identity does not match')
    text = redact_secrets(_text(payload.get('output', '')))
    encoded = text.encode('utf-8')
    return {'text': encoded[:MAX_OUTPUT].decode('utf-8', errors='ignore'), 'truncated': len(encoded) > MAX_OUTPUT}
