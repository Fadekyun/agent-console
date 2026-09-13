#!/usr/bin/env python3
"""Summarize one existing `agentctl session inspect` JSON result, without tools/DB."""
import json
import sys

MAX_INPUT = 262144
FIELDS = ('id', 'tmux_name', 'profile', 'tool', 'execution_kind', 'status',
          'attention_state', 'running', 'live_state', 'observed_status',
          'observed_at', 'observation_source', 'state_disagreement',
          'parent_session_id', 'child_count', 'total_child_count')


def digest(value):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not isinstance(value.get('tmux_name'), str):
        raise ValueError('one session inspection object is required')
    result = {key: value[key] for key in FIELDS if key in value}
    if any(isinstance(item, (dict, list)) for item in result.values()):
        raise ValueError('session metadata must be scalar')
    return {'source': 'session-inspect', 'session': result,
            'receipt_status': 'not-established-by-session-metadata'}


def main():
    try:
        text = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(text) > MAX_INPUT: raise ValueError('input too large')
        print(json.dumps(digest(json.loads(text)), sort_keys=True))
        return 0
    except (ValueError, UnicodeError):
        print('session digest unavailable: invalid or oversized inspection input', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
