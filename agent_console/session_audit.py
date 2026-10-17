"""Durable audit attribution and conservative, read-only legacy name recovery."""
import json


def session_audit(connection, session, *, before=2147483647, limit=50):
    rows = [dict(row) for row in connection.execute(
        'SELECT id,created_at AS at,action,actor,target,outcome,details_json FROM audit_events ORDER BY id DESC')]
    for row in rows:
        try:
            row['details'] = json.loads(row.pop('details_json'))
        except (ValueError, TypeError):
            row['details'] = {}
        if not isinstance(row['details'], dict):
            row['details'] = {}
    # Reverse chronological name intervals end at the rename that left that name.
    # A creation boundary prevents borrowing an older occupant's events.
    name, end = session['tmux_name'], 2147483647
    start = session['created_at']
    intervals = []
    for _ in range(128):
        incoming = next((row for row in rows if row['id'] < end and row['at'] >= start
                         and row['action'] == 'session.renamed' and row['outcome'] == 'success'
                         and row['details'].get('new_name') == name
                         and row['details'].get('session_id') in (None, session['id'])), None)
        lower = incoming['id'] if incoming else 0
        occupants = [row for row in rows if lower < row['id'] < end
                     and row['at'] >= start and row['target'] == name
                     and row['action'] == 'session.created']
        # Multiple creations or an explicit different identity make legacy
        # attribution uncertain. Durable events remain readable regardless.
        ambiguous = len(occupants) > 1 or any(
            row['details'].get('session_id') not in (None, session['id'])
            or row['at'] != start for row in occupants)
        if not ambiguous:
            intervals.append((name, lower, end))
        if incoming is None or ambiguous:
            break
        name, end = incoming['target'], incoming['id'] + 1
    result = []
    for row in rows:
        if row['id'] >= before:
            continue
        identity = row['details'].get('session_id')
        matched = identity == session['id'] or row['target'] == session['id']
        if identity is None and row['at'] >= start:
            matched |= any(row['target'] == name and lower < row['id'] < upper
                           for name, lower, upper in intervals)
        if matched:
            result.append({key: row[key] for key in ('id', 'at', 'action', 'actor', 'outcome')})
            if len(result) == limit:
                break
    return result
