"""Late launch failure restores metadata and per-launch receipts in private fixtures."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import agent_console.manager as manager_module
from agent_console.environment import read_private
from agent_console.skill_registry import read_deliveries
from agent_console.workbench_launch import LaunchCatalog
from agent_console.workflow_service import authenticate_session
import test_core


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class CreateRollbackTests(unittest.TestCase):
    setUp = test_core.SessionIntegrationTests.setUp
    tearDown = test_core.SessionIntegrationTests.tearDown

    def create(self, name, **kwargs):
        return self.manager.create(tool='shell', profile=kwargs.pop('profile', 'general'), name=name,
                                   repository=str(self.workspace), **kwargs)

    def row(self, name):
        with self.manager.database.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE tmux_name=?', (name,)).fetchone()
            return dict(row) if row else None

    def receipts(self, identity):
        with LaunchCatalog(self.manager).store.connect() as db:
            configurations = [tuple(row) for row in db.execute(
                'SELECT * FROM launch_configurations WHERE session_id=? ORDER BY sequence', (identity,))]
        return configurations, read_deliveries(self.manager.settings.state_dir, identity)['deliveries']

    @contextmanager
    def failing_stage(self, stage, name):
        """Raise after the real helper writes, proving committed side effects undo."""
        seen = {}
        def failed():
            current = self.row(name)
            seen.update(current)
            raise RuntimeError(f'fixture {stage} failed after write')
        if stage == 'delivery':
            real = manager_module.record_delivery
            def fail(*args, **kwargs):
                real(*args, **kwargs)
                failed()
            target = patch('agent_console.manager.record_delivery', side_effect=fail)
        elif stage == 'catalog':
            real = LaunchCatalog.record
            def fail(catalog, *args, **kwargs):
                real(catalog, *args, **kwargs)
                failed()
            target = patch.object(LaunchCatalog, 'record', autospec=True, side_effect=fail)
        else:
            real = self.manager.database.audit
            def fail(*args, **kwargs):
                real(*args, **kwargs)
                if args[0] == 'session.created':
                    failed()
            target = patch.object(self.manager.database, 'audit', side_effect=fail)
        with target:
            yield seen

    def test_fresh_failure_removes_row_and_only_its_receipts(self):
        for stage in ('delivery', 'catalog', 'audit'):
            with self.subTest(stage=stage):
                name = f'fresh-{stage}'
                with self.failing_stage(stage, name) as attempted:
                    with self.assertRaisesRegex(RuntimeError, f'fixture {stage} failed'):
                        self.create(name)
                self.assertIsNone(self.row(name))
                self.assertFalse(self.manager.tmux.exists(name))
                self.assertEqual(self.receipts(attempted['id']), ([], []))
                self.assertFalse(Path(attempted['launcher_path']).exists())
                self.assertFalse((self.manager.settings.state_dir/'environment-launches'/f"{attempted['id']}.json").exists())
                with self.manager.database.connect() as db:
                    successful = db.execute("SELECT COUNT(*) FROM audit_events WHERE action='session.created' AND target=? AND outcome='success'", (name,)).fetchone()[0]
                    self.assertEqual(successful, 0)

    def test_stopped_reuse_restores_entire_row_old_capability_files_and_receipt_history(self):
        old_project = self.manager.create_project('Original', repository=str(self.workspace))
        new_project = self.manager.create_project('Replacement', repository=str(self.workspace))
        for stage in ('delivery', 'catalog', 'audit'):
            with self.subTest(stage=stage):
                name = f'reuse-{stage}'
                session = self.create(name, project_id=old_project['id'], task='original task')
                self.manager.kill(name)
                self.manager.set_attention(name, state='blocked', note='keep original note')
                before = self.row(name)
                files = [Path(session['launcher_path']), self.manager.settings.state_dir/'contexts'/f'{name}.md',
                         self.manager.settings.state_dir/'environment-launches'/f"{session['id']}.json",
                         Path(before['archived_transcript'])]
                file_bytes = {path: path.read_bytes() for path in files}
                original_cap = read_private(files[2])['environment']['AGENT_CONSOLE_EVIDENCE_CAPABILITY']
                previous_receipts = self.receipts(session['id'])
                with self.failing_stage(stage, name) as attempted:
                    with self.assertRaisesRegex(RuntimeError, f'fixture {stage} failed'):
                        self.create(name, project_id=new_project['id'], profile='bugfix', task='replacement task')
                self.assertNotEqual(attempted['evidence_capability_hash'], before['evidence_capability_hash'])
                self.assertEqual(self.row(name), before)
                self.assertFalse(self.manager.tmux.exists(name))
                self.assertEqual({path: path.read_bytes() for path in files}, file_bytes)
                self.assertEqual(self.receipts(session['id']), previous_receipts)
                self.assertEqual(authenticate_session(self.manager, session['id'], original_cap)['id'], session['id'])

    def test_failed_database_rollback_is_explicit_after_process_and_receipt_cleanup(self):
        name = 'rollback-denied'
        with self.manager.database.connect() as db:
            db.execute("CREATE TRIGGER prevent_fixture_delete BEFORE DELETE ON sessions "
                       "WHEN OLD.tmux_name='rollback-denied' BEGIN SELECT RAISE(ABORT,'fixture denies rollback'); END")
        with self.failing_stage('audit', name) as attempted:
            with self.assertRaisesRegex(RuntimeError, 'rollback incomplete: session metadata') as failure:
                self.create(name)
        self.assertIn('fixture audit failed', str(failure.exception.__cause__))
        self.assertIn('fixture denies rollback', '\n'.join(failure.exception.__notes__))
        self.assertFalse(self.manager.tmux.exists(name))
        self.assertEqual(self.receipts(attempted['id']), ([], []))
        self.assertIsNotNone(self.row(name), 'failed compensation must be reported, not silently claimed successful')

    def test_unconfirmed_process_stop_retains_new_launch_state_and_reports_failure(self):
        name = 'private-stop-failure'
        session = self.create(name, task='old stopped task')
        self.manager.kill(name)
        previous = self.row(name)
        old_receipts = self.receipts(session['id'])
        with self.failing_stage('audit', name) as attempted, \
             patch.object(self.manager.tmux, 'kill', side_effect=RuntimeError('fixture refuses stop')):
            with self.assertRaisesRegex(RuntimeError, 'process stop unconfirmed') as failure:
                self.create(name, profile='bugfix', task='new running task')
        self.assertIn('fixture audit failed', str(failure.exception.__cause__))
        self.assertTrue(self.manager.tmux.exists(name))
        self.assertEqual(self.row(name), attempted)
        self.assertNotEqual(attempted['evidence_capability_hash'], previous['evidence_capability_hash'])
        snapshot = read_private(self.manager.settings.state_dir/'environment-launches'/f"{session['id']}.json")
        current_cap = snapshot['environment']['AGENT_CONSOLE_EVIDENCE_CAPABILITY']
        self.assertEqual(authenticate_session(self.manager, session['id'], current_cap)['id'], session['id'])
        self.assertTrue(Path(attempted['launcher_path']).exists())
        receipts = self.receipts(session['id'])
        self.assertEqual([len(part) for part in receipts], [len(part)+1 for part in old_receipts])
        self.manager.kill(name)

    def test_file_rollback_failures_are_reported_and_other_compensation_continues(self):
        old_project = self.manager.create_project('Before rollback', repository=str(self.workspace))
        new_project = self.manager.create_project('Attempted replacement', repository=str(self.workspace))
        for stage in ('launcher', 'environment', 'launcher-and-metadata'):
            with self.subTest(stage=stage):
                name = 'file-failure-' + stage
                session = self.create(name, task='old task', project_id=old_project['id'])
                self.manager.kill(name)
                previous = self.row(name)
                launcher = Path(session['launcher_path'])
                context = self.manager.settings.state_dir/'contexts'/f'{name}.md'
                environment = self.manager.settings.state_dir/'environment-launches'/f"{session['id']}.json"
                old_launcher, old_context = launcher.read_bytes(), context.read_bytes()
                old_environment = read_private(environment)
                previous_receipts = self.receipts(session['id'])
                if stage == 'launcher-and-metadata':
                    with self.manager.database.connect() as db:
                        db.execute("CREATE TRIGGER deny_old_capability BEFORE UPDATE ON sessions "
                                   "WHEN OLD.tmux_name='file-failure-launcher-and-metadata' "
                                   "AND OLD.evidence_capability_hash!=NEW.evidence_capability_hash "
                                   f"AND NEW.evidence_capability_hash='{previous['evidence_capability_hash']}' "
                                   "BEGIN SELECT RAISE(ABORT,'fixture metadata compensation denied'); END")
                real_write_bytes, real_write_private = Path.write_bytes, manager_module.write_private
                def write_bytes(path, content):
                    if stage.startswith('launcher') and path == launcher and content == old_launcher:
                        raise OSError('fixture launcher restoration denied')
                    return real_write_bytes(path, content)
                def write_private(path, content):
                    if stage == 'environment' and path == environment and content == old_environment:
                        raise OSError('fixture environment restoration denied')
                    return real_write_private(path, content)
                with self.failing_stage('audit', name), \
                     patch.object(Path, 'write_bytes', autospec=True, side_effect=write_bytes), \
                     patch('agent_console.manager.write_private', side_effect=write_private):
                    with self.assertRaisesRegex(RuntimeError, 'rollback incomplete') as failure:
                        self.create(name, profile='bugfix', task='new task', project_id=new_project['id'])
                self.assertIn(stage.split('-')[0], str(failure.exception))
                self.assertIn('fixture audit failed', str(failure.exception.__cause__))
                notes = '\n'.join(failure.exception.__notes__)
                if stage == 'launcher-and-metadata':
                    self.assertIn('session metadata', str(failure.exception))
                    self.assertIn('fixture metadata compensation denied', notes)
                    self.assertNotEqual(self.row(name)['evidence_capability_hash'], previous['evidence_capability_hash'])
                else:
                    self.assertEqual(self.row(name), previous)
                self.assertFalse(self.manager.tmux.exists(name))
                self.assertEqual(context.read_bytes(), old_context)
                self.assertEqual(self.receipts(session['id']), previous_receipts)
                self.assertFalse((self.manager.settings.state_dir/'skills-isolated'/name).exists())
                self.assertFalse((self.manager.settings.state_dir/'tool-overlays'/name).exists())
                if stage == 'environment':
                    self.assertIn('fixture environment restoration denied', notes)
                    self.assertEqual(launcher.read_bytes(), old_launcher)
                    self.assertNotEqual(read_private(environment), old_environment)
                else:
                    self.assertIn('fixture launcher restoration denied', notes)
                    self.assertNotEqual(launcher.read_bytes(), old_launcher)
                    self.assertEqual(read_private(environment), old_environment)
