import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.providers import LaunchSpec, TOOL_BINARIES
from agent_console.workbench_launch import LaunchCatalog
from agent_console.workflow_service import WorkflowService


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.workspace = root/'workspace'; self.workspace.mkdir()
        profiles = root/'profiles'; profiles.mkdir()
        (profiles/'general.md').write_text('# General\nComplete the bounded task.')
        self.socket = 'launch-test-' + str(os.getpid()) + '-' + str(id(self))
        self.manager = SessionManager(Settings(workspace_root=self.workspace, state_dir=root/'state',
            database_path=root/'state/main.sqlite3', profile_dir=profiles, handoff_dir=root/'handoffs',
            worktree_root=self.workspace/'worktrees', tmux_socket=self.socket, max_managed_sessions=4))
        self.catalog = LaunchCatalog(self.manager)
        self.request = {'tool':'shell', 'profile':'general', 'repository':str(self.workspace), 'task':'A bounded test'}

    def tearDown(self):
        self.manager.tmux.run('kill-server', check=False)
        self.temp.cleanup()

    def root(self, **changes):
        return self.manager.create(**(self.request | changes))

    def test_missing_launcher_still_blocks_preview_without_creating_a_session(self):
        missing = Path(self.temp.name) / 'missing-shell'
        with patch.dict(TOOL_BINARIES, {'shell': missing}):
            with self.assertRaisesRegex(ValueError, 'Tool launcher is unavailable'):
                self.catalog.preview(self.request)
        self.assertEqual(self.manager.list_sessions(), [])

    def test_recipe_preview_resolves_and_run_is_idempotent(self):
        recipe = self.catalog.save_recipe('Routine check', self.request, actor='operator')
        self.assertEqual(self.manager.list_sessions(), [])
        self.assertFalse(self.manager.settings.worktree_root.exists())
        preview = self.catalog.preview(recipe['request'])
        first = self.catalog.launch(recipe['request'], expected_hash=preview['hash'], request_key='recipe-one', actor='operator')
        second = self.catalog.launch(recipe['request'], expected_hash=preview['hash'], request_key='recipe-one', actor='operator')
        self.assertEqual(first['session_id'], second['session_id'])
        self.assertEqual(len(self.manager.list_sessions()), 1)
        self.assertEqual(self.catalog.configuration(first['session_id'])['latest']['request_id'], 'recipe-one')
        with self.assertRaisesRegex(ValueError, 'different launch'):
            self.catalog.launch(recipe['request'] | {'task':'different'}, expected_hash=preview['hash'], request_key='recipe-one', actor='operator')

    def test_recipe_revision_and_removal_do_not_touch_sessions(self):
        recipe = self.catalog.save_recipe('One', self.request, actor='operator')
        newer = self.catalog.save_recipe('Two', self.request | {'task':'Changed'}, actor='operator', recipe_id=recipe['id'], expected_revision=1)
        self.assertEqual(newer['revision'], 2)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.catalog.remove_recipe(recipe['id'], 1, actor='operator')
        self.catalog.remove_recipe(recipe['id'], 2, actor='operator')
        self.assertEqual(self.catalog.recipes(), [])
        self.assertEqual(self.manager.list_sessions(), [])

    def test_profile_drift_requires_new_review_before_launch(self):
        preview = self.catalog.preview(self.request)
        (self.manager.settings.profile_dir/'general.md').write_text('Changed role')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.catalog.launch(self.request, expected_hash=preview['hash'], request_key='drift', actor='operator')
        self.assertEqual(self.manager.list_sessions(), [])

    def test_receipt_retains_explicit_model_efforts_after_stop_without_secrets(self):
        home = self.manager.auth.codex_home('default'); (home/'auth.json').write_text('{"token":"not-to-be-returned"}')
        with patch.dict(TOOL_BINARIES, {'codex':Path('/bin/bash')}), patch.object(self.manager, '_launch_spec', return_value=LaunchSpec(['/bin/bash','-l'], {}, [])):
            session = self.root(tool='codex', model='gpt-test', reasoning_effort='high', plan_reasoning_effort='medium')
            self.manager.kill(session['tmux_name'])
        configuration = self.catalog.configuration(session['id'])
        config = configuration['latest']['config']
        self.assertEqual((config['model'], config['reasoning_effort'], config['plan_reasoning_effort']), ('gpt-test','high','medium'))
        text = json.dumps(configuration)
        self.assertNotIn('not-to-be-returned', text)
        self.assertNotIn('EVIDENCE_CAPABILITY', text)
        self.assertNotIn('environment', text)

    def test_continuation_keeps_dirty_worktree_and_delivers_selected_result_once(self):
        subprocess.run(['git','init','-q',str(self.workspace)], check=True)
        (self.workspace/'note.txt').write_text('original')
        subprocess.run(['git','-C',str(self.workspace),'add','note.txt'], check=True)
        subprocess.run(['git','-C',str(self.workspace),'-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','initial'], check=True)
        source = self.root(worktree=True)
        workspace = Path(source['worktree']); (workspace/'note.txt').write_text('uncommitted work')
        result = WorkflowService(self.manager).publish(source['id'], {'kind':'final','outcome':'pass','summary':'Preserved work', 'checks':[], 'artifacts':[], 'request_key':'source-result'}, 'operator')
        self.manager.kill(source['tmux_name'])
        config = self.catalog.configuration(source['id'])['latest']['config']
        request = config | {'task':'Continue the bounded task'}
        preview = self.catalog.preview(request, source_id=source['id'])
        self.assertEqual(preview['input_result_id'], result['id'])
        launched = self.catalog.launch(request, source_id=source['id'], expected_hash=preview['hash'], request_key='continue-one', actor='operator')
        target = self.manager.inspect(launched['name'])
        self.assertEqual(target['worktree'], source['worktree'])
        self.assertEqual((workspace/'note.txt').read_text(), 'uncommitted work')
        self.assertEqual(target['parent_session_id'], source['id'])
        with self.catalog.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox WHERE target_session_id=? AND result_id=?',(target['id'],result['id'])).fetchone()[0],1)
        with self.assertRaisesRegex(ValueError, 'using the preserved workspace'):
            self.catalog.preview(request, source_id=source['id'])

    def test_changed_role_or_live_source_cannot_silently_continue(self):
        source = self.root(); config = self.catalog.configuration(source['id'])['latest']['config']
        with self.assertRaisesRegex(ValueError, 'still running'):
            self.catalog.preview(config, source_id=source['id'])

        self.manager.kill(source['tmux_name'])
        (self.manager.settings.profile_dir/'general.md').write_text('different role')
        with self.assertRaisesRegex(ValueError, 'profile hash changed'):
            self.catalog.preview(config, source_id=source['id'])

    def test_changed_skill_content_cannot_silently_continue(self):
        from agent_console.skills import assign_skill
        root = Path(self.temp.name)/'skills'; skill = root/'bounded-guide'; skill.mkdir(parents=True)
        source_file = skill/'SKILL.md'
        source_file.write_text('---\nname: bounded-guide\ndescription: Bounded task guidance\nmetadata:\n  agent-console/version: "1"\n  agent-console/compatible_harnesses: [codex]\n  agent-console/approval: allow\n---\nInspect the selected files.\n')
        home = self.manager.auth.codex_home('default'); (home/'auth.json').write_text('{}')
        with patch.dict(os.environ, {'AGCONSOLE_SKILLS_ROOT':str(root)}), patch.dict(TOOL_BINARIES, {'codex':Path('/bin/bash')}), patch.object(self.manager, '_launch_spec', return_value=LaunchSpec(['/bin/bash','-l'], {}, [])):
            assign_skill(self.manager.database, 'general', 'bounded-guide', canonical_root=root)
            source = self.root(tool='codex', model='gpt-test', reasoning_effort='high')
            config = self.catalog.configuration(source['id'])['latest']['config']
            self.manager.kill(source['tmux_name'])
            source_file.write_text(source_file.read_text()+'Changed guidance.\n')
            with self.assertRaisesRegex(ValueError, 'skills changed'):
                self.catalog.preview(config, source_id=source['id'])

    def test_unacknowledged_created_session_recovers_without_duplicate_launch(self):
        source = self.root()
        result = WorkflowService(self.manager).publish(source['id'], {'kind':'final','outcome':'pass','summary':'Carry this result', 'checks':[], 'artifacts':[], 'request_key':'lost-ack-source'}, 'operator')
        self.manager.kill(source['tmux_name'])
        self.request = self.catalog.configuration(source['id'])['latest']['config'] | {'task':'Continue after lost acknowledgment'}
        preview = self.catalog.preview(self.request, source_id=source['id']); real_create = self.manager.create
        def lost_ack(**args):
            real_create(**args)
            raise RuntimeError('response was lost')
        with patch.object(self.manager, 'create', side_effect=lost_ack) as create:
            with self.assertRaisesRegex(RuntimeError, 'response was lost'):
                self.catalog.launch(self.request, expected_hash=preview['hash'], request_key='lost-ack', actor='operator', source_id=source['id'])
            recovered = self.catalog.launch(self.request, expected_hash=preview['hash'], request_key='lost-ack', actor='operator', source_id=source['id'])
            self.assertEqual(recovered['state'], 'created'); self.assertEqual(create.call_count, 1)
        self.assertEqual(len(self.manager.list_sessions()), 2)
        self.catalog.launch_status('lost-ack')
        with self.catalog.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox WHERE result_id=?', (result['id'],)).fetchone()[0], 1)

    def test_unknown_launch_is_not_retried_and_legacy_configuration_is_explicit(self):
        preview = self.catalog.preview(self.request)
        with patch.object(self.manager, 'create', side_effect=RuntimeError('lost before receipt')) as create:
            with self.assertRaises(RuntimeError):
                self.catalog.launch(self.request, expected_hash=preview['hash'], request_key='unknown', actor='operator')
            status = self.catalog.launch(self.request, expected_hash=preview['hash'], request_key='unknown', actor='operator')
            self.assertEqual(status['state'], 'unknown'); self.assertEqual(create.call_count, 1)
        source = self.root()
        with self.catalog.store.connect(write=True) as db:
            db.execute('DELETE FROM launch_configurations WHERE session_id=?', (source['id'],))
        self.assertIsNone(self.catalog.configuration(source['id'])['latest'])
        self.assertIn('unknown', self.catalog.configuration(source['id'])['notice'])

    def test_project_repository_is_used_for_skill_scope_in_preview(self):
        project = self.manager.create_project('Project', repository=str(self.workspace))
        view = self.catalog.preview({'tool':'shell','profile':'general','project_id':project['id']})
        self.assertEqual(view['config']['repository'],str(self.workspace))

    def test_receipt_is_invalidated_by_explicit_skill_refresh(self):
        source = self.root(); self.manager.restart(source['tmux_name'])
        self.assertIn('Explicit restart', self.catalog.configuration(source['id'])['latest']['invalidated'])
