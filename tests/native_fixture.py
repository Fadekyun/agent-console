import json
import sqlite3
from pathlib import Path
from agent_console.manager import _launcher_exports

THREAD = '01a106f0-0efb-7892-b871-e7e8b167bd78'


def seed_native(session, thread_id=THREAD):
    home = Path(_launcher_exports(Path(session['launcher_path']).read_text())['CODEX_HOME'])
    rollout = home / 'sessions' / (thread_id + '.jsonl')
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': thread_id}}) + '\n')
    with sqlite3.connect(home / 'state_5.sqlite') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, source TEXT, cwd TEXT, rollout_path TEXT)')
        conn.execute('INSERT OR REPLACE INTO threads VALUES(?,?,?,?)',
            (thread_id, 'cli', session.get('worktree') or session['repository'], str(rollout)))
    return home, rollout
