"""Issue146 regressions using inert shells and private tmux/SQLite fixtures."""
import hashlib
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from agent_console.environment import read_private
from agent_console.workflow_service import authenticate_session
from test_core import SessionIntegrationTests as _Fixture


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class Lifecycle146Tests(unittest.TestCase):
    setUp = _Fixture.setUp
    tearDown = _Fixture.tearDown

    def create(self, name, project_id=None):
        return self.manager.create(tool='shell', profile='general', name=name,
                                   repository=str(self.workspace), project_id=project_id)

    def test_stopped_name_recreation_rotates_capability_without_changing_durable_id(self):
        before = self.create('reused-capability')
        snapshot = self.manager.settings.state_dir/'environment-launches'/f"{before['id']}.json"
        old_cap = read_private(snapshot)['environment']['AGENT_CONSOLE_EVIDENCE_CAPABILITY']
        self.manager.kill(before['tmux_name'])
        after = self.create(before['tmux_name'])
        new_cap = read_private(snapshot)['environment']['AGENT_CONSOLE_EVIDENCE_CAPABILITY']
        self.assertEqual(after['id'], before['id'])
        self.assertNotEqual(hashlib.sha256(old_cap.encode()).digest(), hashlib.sha256(new_cap.encode()).digest())
        with self.manager.database.connect() as conn:
            row = conn.execute('SELECT evidence_capability_hash FROM sessions WHERE id=?', (after['id'],)).fetchone()
        self.assertEqual(row['evidence_capability_hash'], hashlib.sha256(new_cap.encode()).hexdigest())
        self.assertEqual(authenticate_session(self.manager, after['id'], new_cap)['id'], after['id'])
        with self.assertRaises(PermissionError): authenticate_session(self.manager, after['id'], old_cap)

    def test_stopped_name_recreation_persists_new_project_and_then_clears_it(self):
        a = self.manager.create_project('Project A', repository=str(self.workspace))
        b = self.manager.create_project('Project B', repository=str(self.workspace))
        current = self.create('reused-project', a['id'])
        for project_id in (b['id'], None):
            with self.subTest(project_id=project_id):
                self.manager.kill(current['tmux_name'])
                current = self.create(current['tmux_name'], project_id)
                self.assertEqual(current['project_id'], project_id)
                snapshot = read_private(self.manager.settings.state_dir/'environment-launches'/f"{current['id']}.json")
                self.assertEqual(snapshot['environment']['AGENT_CONSOLE_PROJECT_ID'], project_id or '')
                projects = {p['id']:p['session_count'] for p in self.manager.list_projects()}
                self.assertEqual(projects[a['id']], 0)
                self.assertEqual(projects[b['id']], 1 if project_id else 0)

    def test_archive_after_kill_and_repeated_archive_preserve_saved_output(self):
        session = self.create('stopped-output')
        with patch.object(self.manager.tmux, 'capture', return_value=('kept private output\n', False)):
            stopped = self.manager.kill(session['tmux_name'])
        original = Path(stopped['archived_transcript'])
        for _ in range(2):
            archived = self.manager.archive(session['tmux_name'])
            self.assertEqual(archived['archived_transcript'], str(original))
            self.assertEqual(original.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.manager.review_session(session['tmux_name'])['content'], 'kept private output\n')

    def unmanaged(self, name):
        self.manager.tmux.run('new-session', '-d', '-s', name, 'sleep', '60')
        self.manager.reconcile()
        return self.manager.inspect(name)

    def test_archive_stopped_session_without_output_does_not_record_nonexistent_file(self):
        session = self.unmanaged('stopped-no-output')
        self.manager.tmux.kill(session['tmux_name'])
        archived = self.manager.archive(session['tmux_name'])
        self.assertEqual(archived['status'], 'archived')
        self.assertIsNone(archived['archived_transcript'])
        self.assertEqual(self.manager.review_session(session['tmux_name'])['source'], 'unavailable')

    def test_denied_unmanaged_archive_kill_has_no_archive_side_effects(self):
        before = self.unmanaged('denied-unmanaged')
        transcripts = set((self.manager.settings.state_dir/'transcripts').iterdir())
        with patch.object(self.manager.tmux, 'capture', return_value=('must not capture\n', False)) as capture, patch.object(self.manager.tmux, 'kill') as kill:
            with self.assertRaises(PermissionError): self.manager.archive(before['tmux_name'], kill=True)
            capture.assert_not_called(); kill.assert_not_called()
        after = self.manager.inspect(before['tmux_name'])
        self.assertEqual(after['status'], before['status'])
        self.assertEqual(after['archived_transcript'], before['archived_transcript'])
        self.assertTrue(after['running'])
        self.assertEqual(set((self.manager.settings.state_dir/'transcripts').iterdir()), transcripts)

    def test_explicit_unmanaged_archive_kill_remains_supported(self):
        session = self.unmanaged('allowed-unmanaged')
        with patch.object(self.manager.tmux, 'capture', return_value=('allowed output\n', False)):
            archived = self.manager.archive(session['tmux_name'], kill=True, allow_unmanaged=True)
        self.assertEqual(archived['status'], 'archived')
        self.assertFalse(archived['running'])
        self.assertEqual(Path(archived['archived_transcript']).read_text(), 'allowed output\n')
        self.assertEqual(self.manager.review_session(session['tmux_name'])['content'], 'allowed output\n')

    def test_unmanaged_archive_without_kill_remains_supported(self):
        session = self.unmanaged('archive-no-kill')
        archived = self.manager.archive(session['tmux_name'])
        self.assertEqual(archived['status'], 'archived')
        self.assertTrue(archived['running'])


# Avoid collecting the imported fixture's entire test suite.
del _Fixture
