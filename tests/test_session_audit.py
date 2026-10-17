import json
from pathlib import Path
import tempfile
import unittest

from agent_console.database import Database
from agent_console.session_audit import session_audit


class SessionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'audit.sqlite3')
        self.db.migrate()
        self.start = '2026-01-01T00:00:00+00:00'
        self.insert_session('original', 'A')

    def insert_session(self, identity, name, created=None):
        with self.db.connect() as db:
            db.execute('INSERT INTO sessions(id,tmux_name,tool,profile,created_at,last_activity,status,managed) VALUES(?,?,?,?,?,?,?,1)',
                       (identity, name, 'shell', 'general', created or self.start, created or self.start, 'process-exited'))

    def rename_row(self, identity, name):
        with self.db.connect() as db:
            db.execute('UPDATE sessions SET tmux_name=? WHERE id=?', (name, identity))

    def legacy(self, action, target, details=None, at=None):
        with self.db.connect() as db:
            db.execute('INSERT INTO audit_events(created_at,actor,surface,action,target,outcome,details_json) VALUES(?,?,?,?,?,?,?)',
                       (at or self.start, 'test', 'CLI', action, target, 'success', json.dumps(details or {})))

    def history(self, identity='original', before=2147483647):
        with self.db.connect() as db:
            session = db.execute('SELECT * FROM sessions WHERE id=?', (identity,)).fetchone()
            return session_audit(db, session, before=before)

    def test_new_events_survive_repeated_rename_and_name_reuse(self):
        self.db.audit('session.created', 'A', 'success')
        self.rename_row('original', 'B')
        self.db.audit('session.renamed', 'A', 'success', session_id='original', details={'new_name': 'B'})
        self.insert_session('replacement', 'A')
        self.db.audit('session.created', 'A', 'success')
        self.rename_row('original', 'C')
        self.db.audit('session.renamed', 'B', 'success', session_id='original', details={'new_name': 'C'})
        self.assertEqual([e['action'] for e in self.history()], ['session.renamed', 'session.renamed', 'session.created'])
        self.assertEqual(len(self.history('replacement')), 1)

    def test_legacy_rename_recovers_original_events_without_later_occupant(self):
        self.legacy('session.created', 'A')
        self.legacy('session.attached', 'A')
        self.legacy('session.renamed', 'A', {'new_name': 'B'})
        self.rename_row('original', 'B')
        self.insert_session('replacement', 'A', '2026-01-02T00:00:00+00:00')
        self.legacy('session.created', 'A', at='2026-01-02T00:00:00+00:00')
        self.assertEqual(len(self.history()), 3)
        self.assertEqual(len(self.history('replacement')), 1)

    def test_legacy_repeat_names_and_pagination(self):
        self.legacy('session.created', 'A')
        self.legacy('session.renamed', 'A', {'new_name': 'B'})
        self.legacy('session.attached', 'B')
        self.legacy('session.renamed', 'B', {'new_name': 'A'})
        full = self.history()
        self.assertEqual(len(full), 4)
        self.assertEqual(self.history(before=full[1]['id']), full[2:])

    def test_ambiguous_legacy_occupants_are_not_claimed(self):
        self.legacy('session.created', 'A')
        self.legacy('session.created', 'A')
        self.legacy('session.attached', 'A')
        self.assertEqual(self.history(), [])
        self.db.audit('session.attached', 'A', 'success')
        self.assertEqual(len(self.history()), 1)

    def test_conflicting_identity_is_never_borrowed_by_name(self):
        self.legacy('session.attached', 'A', {'session_id': 'another'})
        self.assertEqual(self.history(), [])

    def test_audit_does_not_mutate_supplied_details(self):
        details = {'note': 'example', 'session_id': 'wrong'}
        self.db.audit('session.attention.updated', 'A', 'success', details=details)
        self.assertEqual(details['session_id'], 'wrong')
        self.assertEqual(len(self.history()), 1)
