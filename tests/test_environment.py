from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from agent_console.environment import EnvironmentStore, credential_values, read_private, write_private
from agent_console.environment_api import environment_routes
from agent_console.environment_bootstrap import __file__ as bootstrap
from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.providers import LaunchSpec


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EnvironmentStore(self.root / 'config')

    def tearDown(self):
        self.temp.cleanup()

    def test_precedence_disable_suppress_delete_and_write_only_description(self):
        self.store.put('API_KEY', value='global-sentinel')
        self.store.put('API_KEY', value='project-sentinel', project_id='project-one')
        baseline = {'API_KEY': 'host-sentinel'}
        self.assertEqual(self.store.resolve('project-one', baseline=baseline)[0]['API_KEY'], 'project-sentinel')
        self.store.put('API_KEY', state='disabled', project_id='project-one')
        self.assertEqual(self.store.resolve('project-one', baseline=baseline)[0]['API_KEY'], 'global-sentinel')
        self.store.put('API_KEY', state='suppressed', project_id='project-one')
        self.assertNotIn('API_KEY', self.store.resolve('project-one', baseline=baseline)[0])
        self.store.delete('API_KEY', project_id='project-one')
        self.assertEqual(self.store.resolve('project-one', baseline=baseline)[0]['API_KEY'], 'global-sentinel')
        self.store.delete('API_KEY')
        self.assertEqual(self.store.resolve('project-one', baseline=baseline)[0]['API_KEY'], 'host-sentinel')
        self.assertNotIn('sentinel', json.dumps(self.store.describe('project-one')))

    def test_literal_values_private_files_atomic_revisions_and_reserved_controls(self):
        value = "'\n$(touch /tmp/never-environment-sentinel); `whoami` $HOME"
        self.store.put('APP_VALUE', value=value)
        self.assertEqual(self.store.resolve(baseline={})[0]['APP_VALUE'], value)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        for key in ['AGENT_CONSOLE_SESSION_ID', 'AGCONSOLE_CODEX_BIN', 'PATH', 'HOME', 'BASH_ENV', 'LD_PRELOAD', 'CODEX_HOME', 'NODE_OPTIONS', 'bad name', '../x']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.put(key, value='sentinel')
        for value in ['x\0x', 'x' * 32769, 12]:
            with self.assertRaises(ValueError):
                self.store.put('API_KEY', value=value)
        self.store.put('EMPTY', value='')
        self.assertEqual(self.store.resolve(baseline={})[0]['EMPTY'], '')
        self.assertEqual(self.store.revision(), 2)

    def test_concurrent_edits_do_not_lose_variables(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: self.store.put(f'KEY_{n}', value=str(n)), range(8)))
        values, revision = self.store.resolve(baseline={})
        self.assertEqual(values, {f'KEY_{n}': str(n) for n in range(8)})
        self.assertEqual(revision, 8)

    def test_quota_and_surrogate_failures_preserve_previous_revision(self):
        self.store.put('FIRST', value='x' * 32000)
        self.store.put('SECOND', value='y' * 32000)
        revision = self.store.revision()
        for name, value in [('THIRD', 'z' * 4000), ('INVALID', '\ud800')]:
            with self.assertRaises(ValueError):
                self.store.put(name, value=value)
        self.assertEqual(self.store.revision(), revision)
        self.assertNotIn('THIRD', self.store.resolve(baseline={})[0])
        self.store.put('PROJECT', value='ok', project_id='other')
        self.assertEqual(self.store.revision(), revision)

    def test_symlink_and_world_readable_store_fail_closed(self):
        self.store.put('KEY', value='sentinel')
        self.store.path.chmod(0o644)
        with self.assertRaises(ValueError): self.store.resolve()
        self.store.path.unlink()
        self.store.path.symlink_to(self.root / 'elsewhere')
        with self.assertRaises(OSError): self.store.resolve()

    def test_existing_credentials_are_data_not_shell(self):
        credential = self.root / 'credentials.env'
        credential.write_text("export API_KEY='host secret $(whoami)'\nEMPTY=\n# comment\n")
        self.assertEqual(credential_values([credential])['API_KEY'], 'host secret $(whoami)')
        self.store.put('API_KEY', value='override')
        self.assertEqual(self.store.resolve(baseline={}, secret_files=[credential])[0]['API_KEY'], 'override')
        credential.write_text('source /tmp/secret\n')
        with self.assertRaisesRegex(ValueError, 'literal KEY=value'):
            self.store.resolve(secret_files=[credential])

    def test_bootstrap_exact_environment_without_tmux_secret_leakage(self):
        snapshot = self.root / 'runtime' / 'launch.json'
        value = "$(false)\n'\" exact literal"
        write_private(snapshot, {'environment': {'APP_KEY': value, 'PATH': os.environ['PATH'], 'AGENT_CONSOLE_SESSION_NAME': 'old'}, 'launcher_keys': ['AGENT_CONSOLE_SESSION_NAME']})
        child = subprocess.run([sys.executable, bootstrap, str(snapshot), sys.executable, '-c', 'import os,json;print(json.dumps(dict(os.environ)))'],
                               env={'STALE_TMUX_SECRET': 'must-not-leak', 'AGENT_CONSOLE_SESSION_NAME': 'renamed'}, capture_output=True, text=True, check=True)
        actual = json.loads(child.stdout)
        self.assertEqual(actual['APP_KEY'], value)
        self.assertEqual(actual['AGENT_CONSOLE_SESSION_NAME'], 'renamed')
        self.assertNotIn('STALE_TMUX_SECRET', actual)

    def test_resolver_never_inherits_parent_identity_or_capability(self):
        values, _ = self.store.resolve(baseline={'AGENT_CONSOLE_SESSION_ID': 'parent', 'AGENT_CONSOLE_EVIDENCE_CAPABILITY': 'secret', 'AGENT_CONSOLE_STATE_DIR': '/state'})
        self.assertEqual(values, {'AGENT_CONSOLE_STATE_DIR': '/state'})


class EnvironmentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        settings = Settings(workspace_root=self.root, state_dir=self.root/'state', database_path=self.root/'state/db.sqlite',
                            profile_dir=self.root/'profiles', handoff_dir=self.root/'handoff', worktree_root=self.root/'worktrees', tmux_socket='unused')
        self.manager = SessionManager(settings)
        self.app = FastAPI()
        def identity(request: Request):
            if request.headers.get('owner') != 'yes': raise HTTPException(403)
            return SimpleNamespace(actor='owner', access_surface='test')
        self.app.include_router(environment_routes(self.manager, identity, self.root))
        self.client = TestClient(self.app)
        self.headers = {'owner': 'yes'}

    def tearDown(self):
        self.temp.cleanup()

    def test_identity_required_and_validation_never_echoes_values(self):
        for method in ['get', 'put', 'delete']:
            path = '/api/environment' + ('' if method == 'get' else '/API_KEY')
            result = getattr(self.client, method)(path)
            self.assertEqual(result.status_code, 403)
        self.assertEqual(self.client.get('/api/environment', headers={'Authorization': 'Bearer session-capability'}).status_code, 403)
        response = self.client.put('/api/environment/API_KEY', headers=self.headers, json={'value': 'private-sentinel'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('private-sentinel', response.text)
        for payload in [{'value': {'secret': 'private-sentinel'}}, {'value': 'private-sentinel\0'}, {'value': 'private-sentinel', 'unexpected': 1}]:
            response = self.client.put('/api/environment/API_KEY', headers=self.headers, json=payload)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('private-sentinel', response.text)
        self.assertNotIn(b'private-sentinel', self.manager.database.path.read_bytes())

    def test_launcher_keeps_values_and_capabilities_out_of_script(self):
        self.manager.environment.put('APP_SECRET', value='private-sentinel')
        script = self.manager._write_launcher('fixture', 'sess-fixture', LaunchSpec([sys.executable, '-c', 'pass'], {}, []), evidence_capability='private-capability')
        body = script.read_text()
        self.assertNotIn('private-sentinel', body)
        self.assertNotIn('private-capability', body)
        snapshot = read_private(self.root/'state/environment-launches/sess-fixture.json')
        self.assertEqual(snapshot['environment']['APP_SECRET'], 'private-sentinel')
        self.assertEqual(snapshot['environment']['AGENT_CONSOLE_EVIDENCE_CAPABILITY'], 'private-capability')
        self.assertNotIn('AGENT_CONSOLE_EVIDENCE_CAPABILITY', snapshot['launcher_keys'])
        subprocess.run(['bash', str(script)], check=True)

class EnvironmentLifecycleTests(unittest.TestCase):
    def setUp(self):
        from test_core import SessionIntegrationTests
        SessionIntegrationTests.setUp(self)
        self.root = Path(self.temp.name)
        self.configure_commandcode = lambda: SessionIntegrationTests.configure_commandcode(self)

    def tearDown(self):
        from test_core import SessionIntegrationTests
        SessionIntegrationTests.tearDown(self)

    def test_real_launch_child_and_restart_rotate_values_without_ambient_tmux_leaks(self):
        import time
        output = self.root / 'values.json'
        script = 'import os,json,time;open(' + repr(str(output)) + ',"w").write(json.dumps({k:os.getenv(k) for k in ["APP_VALUE","AGENT_CONSOLE_SESSION_NAME"]}));time.sleep(90)'
        spec = LaunchSpec([sys.executable, '-c', script], {}, [])
        self.manager.environment.put('APP_VALUE', value='first')
        with patch.object(self.manager, '_launch_spec', return_value=spec):
            session = self.manager.create(tool='shell', profile='general', name='env-parent')
            for _ in range(60):
                if output.exists(): break
                time.sleep(.05)
            self.assertEqual(json.loads(output.read_text())['APP_VALUE'], 'first')
            self.manager.environment.put('APP_VALUE', value='rotated')
            self.assertEqual(json.loads(output.read_text())['APP_VALUE'], 'first')
            self.manager.restart('env-parent')
            for _ in range(60):
                if json.loads(output.read_text())['APP_VALUE'] == 'rotated': break
                time.sleep(.05)
            self.assertEqual(json.loads(output.read_text())['APP_VALUE'], 'rotated')
            child = self.manager.delegate(parent='env-parent', tool='shell', profile='planner', task='Inspect environment')['session']
            snapshot = read_private(self.manager.settings.state_dir/'environment-launches'/f"{child['id']}.json")
            self.assertEqual(snapshot['environment']['APP_VALUE'], 'rotated')
            self.assertEqual(snapshot['environment']['AGENT_CONSOLE_PARENT_SESSION_ID'], session['id'])
            self.manager.environment.path.chmod(0o644)
            with patch.object(self.manager.tmux, 'restart') as stop:
                with self.assertRaises(ValueError): self.manager.restart('env-parent')
                stop.assert_not_called()
            self.assertTrue(self.manager.tmux.exists('env-parent'))

    def test_mcp_and_harness_share_resolved_values_and_restart_removes_mcp(self):
        from agent_console.providers import TOOL_BINARIES
        self.configure_commandcode()
        data = self.manager.auth._read()
        data['contexts']['pi']['commandcode-main'] = data['contexts']['hermes']['commandcode-main'].copy()
        self.manager.auth._write(data)
        executable = self.root/'fixture-pi'
        executable.write_text('#!/bin/sh\nexec sleep 90\n')
        executable.chmod(0o700)
        self.manager.environment.put('BUSHI_MCP_TOKEN', value='managed-mcp-sentinel')
        with patch.dict(TOOL_BINARIES, {'pi': executable}):
            session = self.manager.create(tool='pi', profile='general', name='env-pi')
            native = self.manager.settings.state_dir/'contexts/env-pi-pi/mcp.json'
            self.assertIn('bushi', json.loads(native.read_text())['mcpServers'])
            self.assertNotIn('managed-mcp-sentinel', native.read_text())
            snapshot = read_private(self.manager.settings.state_dir/'environment-launches'/f"{session['id']}.json")
            self.assertEqual(snapshot['environment']['BUSHI_MCP_TOKEN'], 'managed-mcp-sentinel')
            self.manager.environment.put('BUSHI_MCP_TOKEN', value='', state='enabled')
            self.manager.restart('env-pi')
            self.assertFalse(json.loads(native.read_text())['mcpServers']['bushi']['enabled'])
