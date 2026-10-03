from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_console import cli
from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.session_control import MAX_DELEGATION_DEPTH
from agent_console.session_control_api import session_control_routes


class ChildCapacityConfigTests(unittest.TestCase):
    def test_environment_default_and_explicit_limit(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().max_children_per_parent, 0)
        for raw, expected in [('', 0), ('invalid', 0), ('-1', 0), ('0', 0), ('7', 7)]:
            with self.subTest(raw=raw), patch.dict(os.environ, {'AGENT_CONSOLE_MAX_CHILDREN': raw}):
                self.assertEqual(Settings.from_env().max_children_per_parent, expected)


@unittest.skipUnless(subprocess.run(['sh', '-c', 'command -v tmux'], capture_output=True).returncode == 0, 'tmux required')
class ChildCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.workspace = root / 'workspace'; self.workspace.mkdir()
        profiles = root / 'profiles'; profiles.mkdir()
        for name in ['general', 'planner', 'coder', 'scout']:
            (profiles / (name + '.md')).write_text('# ' + name)
        self.socket = 'child-capacity-' + str(os.getpid()) + '-' + str(id(self))
        self.manager = SessionManager(Settings(workspace_root=self.workspace, state_dir=root/'state',
            database_path=root/'state/main.sqlite3', profile_dir=profiles, handoff_dir=root/'handoffs',
            worktree_root=self.workspace/'worktrees', tmux_socket=self.socket, legacy_tmux_socket_path=None))
        app = FastAPI(); app.include_router(session_control_routes(self.manager)); self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        subprocess.run(['tmux', '-L', self.socket, 'kill-server'], capture_output=True)
        self.temp.cleanup()

    def create(self, name, parent=None, profile='general'):
        return self.manager.create(tool='shell', profile=profile, name=name,
            repository=str(self.workspace), parent_session_id=parent, creator_surface='web')

    def control(self, parent, command, payload):
        token = 'child-capacity-fixture'
        with self.manager.database.connect() as db:
            db.execute('UPDATE sessions SET evidence_capability_hash=? WHERE id=?',
                (hashlib.sha256(token.encode()).hexdigest(), parent['id']))
        return self.client.post('/api/agent-sessions', json={'command':command, 'payload':payload},
            headers={'Authorization':'Bearer '+token, 'X-Agent-Console-Session':parent['id']})

    def delegate(self, parent, name, profile='scout'):
        return self.control(parent, 'delegate', {'tool':'shell','profile':profile,
            'task':'Bounded fixture task','child_name':name})

    def test_default_allows_more_than_three_manual_children(self):
        self.assertEqual(self.manager.settings.max_children_per_parent, 0)
        parent = self.create('manual-parent', profile='planner')
        for number in range(5):
            child = self.create('manual-child-' + str(number), parent['id'], profile='coder')
            self.assertEqual(child['parent_session_id'], parent['id'])
        root = self.manager.inspect('manual-parent')
        self.assertEqual((root['child_count'], root['total_child_count'], root['profile']), (5, 5, 'planner'))
        denied = self.delegate(parent, 'cannot-upgrade', profile='coder')
        self.assertEqual(denied.status_code, 403)

    def test_default_allows_more_than_three_capability_delegated_children(self):
        parent = self.create('agent-parent')
        emitted = []
        def transport(command, payload):
            response = self.control(parent, command, payload)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        with patch.dict(os.environ, {'AGENT_CONSOLE_REPORTING_URL':'http://fixture.invalid',
                'AGENT_CONSOLE_SESSION_ID':parent['id']}), \
                patch('agent_console.session_client.request', side_effect=transport), \
                patch('agent_console.cli.SessionManager', side_effect=AssertionError('no local writer fallback')), \
                patch('agent_console.cli.emit', side_effect=emitted.append):
            for number in range(5):
                result = cli.main(['delegate','scout','--parent',parent['id'],'--tool','shell',
                    '--task','Bounded fixture task','--name','agent-child-' + str(number)])
                self.assertEqual(result, 0)
                self.assertEqual(emitted[-1]['session']['parent_session_id'], parent['id'])
        root = self.manager.inspect('agent-parent')
        self.assertEqual((root['child_count'], root['total_child_count']), (5, 5))

    def test_owner_cli_child_inherits_project_by_name_or_id(self):
        project = self.manager.create_project('CLI project', repository=str(self.workspace))
        parent = self.manager.create(tool='shell', profile='planner', name='cli-parent',
            project_id=project['id'], repository=str(self.workspace), creator_surface='web')
        emitted = []
        markers = {key: '' for key in ('AGENT_CONSOLE_REPORTING_URL',
            'AGENT_CONSOLE_SESSION_ID', 'AGENT_CONSOLE_EVIDENCE_CAPABILITY')}
        with patch.dict(os.environ, markers), patch.object(cli, 'SessionManager', return_value=self.manager), \
                patch.object(cli, 'emit', side_effect=emitted.append):
            for number, ref in enumerate((parent['id'], parent['tmux_name'])):
                self.assertEqual(cli.main(['session', 'create', '--tool', 'shell', '--profile', 'coder',
                    '--parent', ref, '--name', 'cli-child-' + str(number)]), 0)
                self.assertEqual(emitted[-1]['parent_session_id'], parent['id'])
                self.assertEqual(emitted[-1]['project_id'], project['id'])
                self.assertEqual(emitted[-1]['repository'], str(self.workspace))
            self.assertEqual(cli.main(['session', 'create', '--tool', 'shell', '--parent', 'missing',
                '--name', 'not-created']), 2)
            self.assertFalse(self.manager.tmux.exists('not-created'))
        self.assertEqual(self.delegate(parent, 'cannot-escalate', profile='coder').status_code, 403)

    def test_managed_create_parent_uses_capability_and_partial_context_fails_closed(self):
        parent = self.create('cli-managed-parent')
        emitted = []
        def transport(command, payload):
            self.assertEqual(payload['name'], parent['id'])
            response = self.control(parent, command, payload)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        with patch.dict(os.environ, {'AGENT_CONSOLE_REPORTING_URL':'http://fixture.invalid',
                'AGENT_CONSOLE_SESSION_ID':parent['id']}), \
                patch('agent_console.session_client.request', side_effect=transport), \
                patch.object(cli, 'SessionManager', side_effect=AssertionError('no local writer')), \
                patch.object(cli, 'emit', side_effect=emitted.append):
            self.assertEqual(cli.main(['session', 'create', '--tool', 'shell', '--profile', 'scout',
                '--parent', parent['id'], '--task', 'Bounded task', '--name', 'cli-managed-child']), 0)
            self.assertEqual(emitted[-1]['session']['parent_session_id'], parent['id'])
            with patch.dict(os.environ, {'AGENT_CONSOLE_REPORTING_URL':''}):
                self.assertEqual(cli.main(['session', 'create', '--tool', 'shell', '--parent', parent['id']]), 2)

    def test_explicit_limit_reclaims_stopped_and_archived_children(self):
        self.manager.settings = replace(self.manager.settings, max_children_per_parent=2)
        parent = self.create('limited-parent')
        self.create('first', parent['id']); self.create('second', parent['id'])
        with self.assertRaisesRegex(RuntimeError, 'child-session limit reached'):
            self.create('third', parent['id'])
        self.manager.kill('first'); self.create('third', parent['id'])
        self.manager.archive('second', kill=True); self.create('fourth', parent['id'])
        root = self.manager.inspect('limited-parent')
        self.assertEqual((root['child_count'], root['total_child_count']), (2, 4))
        with self.assertRaisesRegex(RuntimeError, 'child-session limit reached'):
            self.create('fifth', parent['id'])

    def test_explicit_limit_is_a_managed_cli_capacity_error(self):
        self.manager.settings = replace(self.manager.settings, max_children_per_parent=2)
        parent = self.create('agent-limited-parent')
        for name in ['agent-first', 'agent-second']:
            response = self.delegate(parent, name)
            self.assertEqual(response.status_code, 200, response.text)
        response = self.delegate(parent, 'agent-third')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('child-session limit reached (2)', response.json()['detail'])
        self.manager.kill('agent-first')
        response = self.delegate(parent, 'agent-third')
        self.assertEqual(response.status_code, 200, response.text)

    def test_reserved_child_consumes_explicit_capacity(self):
        self.manager.settings = replace(self.manager.settings, max_children_per_parent=1)
        parent = self.create('reserved-parent')
        with self.manager.database.connect() as db:
            db.execute("INSERT INTO sessions(id,tmux_name,parent_session_id,status,managed,created_at) VALUES('reserved','reserved-child',?,'reserved',1,'2026-10-03')", (parent['id'],))
        with self.assertRaisesRegex(RuntimeError, 'child-session limit reached'):
            self.create('extra-child', parent['id'])

    def test_unlimited_children_preserves_global_admission(self):
        self.manager.settings = replace(self.manager.settings, max_managed_sessions=2)
        parent = self.create('global-parent'); self.create('global-child', parent['id'])
        with self.assertRaisesRegex(RuntimeError, 'managed-session limit reached'):
            self.create('global-extra', parent['id'])
        response = self.delegate(parent, 'global-agent-extra')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('managed-session limit reached', response.json()['detail'])

    def test_unlimited_children_preserves_depth_guard(self):
        parent = self.create('depth-root')
        for depth in range(MAX_DELEGATION_DEPTH):
            parent = self.create('depth-' + str(depth), parent['id'])
        with self.assertRaisesRegex(ValueError, 'descendant depth limit reached'):
            self.create('too-deep', parent['id'])
