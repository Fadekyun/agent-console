"""Regression coverage for release isolation and portable maintenance adapters."""
import asyncio
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import unittest
import httpx
from unittest import mock

from agent_console import maintenance
from agent_console.deployer import DeploymentMode, ProductionServiceRunner, ServiceConfig


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_configuration_round_trip_preserves_unknown_keys_comments_and_literals(self):
        path = self.root / 'runtime.env'
        old = "# Operator settings\nCUSTOM_TOKEN='literal $(touch /tmp/never-run)'\nAGENT_CONSOLE_MAX_SESSIONS=6\n"
        path.write_text(old)
        maintenance.merge_environment(path, {'AGENT_CONSOLE_PORT': '3210'})
        self.assertTrue(path.read_text().startswith(old))
        self.assertEqual(maintenance.read_environment(path)['CUSTOM_TOKEN'], 'literal $(touch /tmp/never-run)')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_explicit_changed_setting_replaces_duplicate_assignment(self):
        path = self.root / 'runtime.env'
        path.write_text('PORT=1\nPORT=2\n# keep\n')
        maintenance.merge_environment(path, {'PORT': '3'})
        self.assertEqual(path.read_text(), 'PORT=3\n# keep\n')

    def test_malformed_update_leaves_file_unchanged(self):
        path = self.root / 'runtime.env'; path.write_text('GOOD=1\n')
        with self.assertRaises(ValueError):
            maintenance.merge_environment(path, {'GOOD': 'a\nb'})
        self.assertEqual(path.read_text(), 'GOOD=1\n')

    def test_failed_atomic_replace_keeps_original(self):
        path = self.root / 'runtime.env'; path.write_text('GOOD=1\n')
        with mock.patch('os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                maintenance.merge_environment(path, {'GOOD': '2'})
        self.assertEqual(path.read_text(), 'GOOD=1\n')
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_profile_refresh_changes_only_exact_old_defaults(self):
        configured = self.root / 'profiles'; old = self.root / 'old/agent-profiles'; new = self.root / 'new/agent-profiles'
        for folder in (configured, old, new): folder.mkdir(parents=True)
        for name in ('coder.md', 'reviewer.md'):
            (old / name).write_text('old default'); (new / name).write_text('new default')
        (configured / 'coder.md').write_text('old default')
        (configured / 'reviewer.md').write_text('operator customization')
        report = maintenance.update_bundled_profiles(configured, old, new)
        self.assertEqual(report['updated'], ['coder.md'])
        self.assertEqual(report['customized'], ['reviewer.md'])
        self.assertEqual((configured / 'reviewer.md').read_text(), 'operator customization')
        self.assertEqual((configured / '.available-updates/new/reviewer.md').read_text(), 'new default')

    def test_runtime_failure_never_changes_previous_dependencies(self):
        old = self.root / 'old/.runtime/bin/python'
        old.parent.mkdir(parents=True); old.write_text('previous interpreter')
        release = self.root / 'new'; (release / 'web').mkdir(parents=True)
        (release / 'web/requirements.txt').write_text('example==1\n')
        with mock.patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['venv'])):
            with self.assertRaises(subprocess.CalledProcessError):
                maintenance.prepare_runtime(release)
        self.assertEqual(old.read_text(), 'previous interpreter')
        self.assertFalse((release / '.runtime').exists())

    def test_successful_runtime_is_reused_without_reinstall(self):
        release = self.root / 'release'; (release / 'web').mkdir(parents=True)
        (release / 'web/requirements.txt').write_text('example==1\n')
        def run(command, **kwargs):
            if command[1:3] == ['-m', 'venv']:
                python = Path(command[-1]) / 'bin/python'
                python.parent.mkdir(parents=True); python.write_text('interpreter')
            return subprocess.CompletedProcess(command, 0)
        with mock.patch('subprocess.run', side_effect=run) as process:
            first = maintenance.prepare_runtime(release)
            count = process.call_count
            self.assertEqual(first, maintenance.prepare_runtime(release))
            self.assertEqual(count, process.call_count)

    def test_release_interpreter_selected_with_legacy_fallback(self):
        release = self.root / 'release'; state = self.root / 'state'
        self.assertEqual(maintenance.runtime_python(release, state), state / 'venv/bin/python')
        python = release / '.runtime/bin/python'; python.parent.mkdir(parents=True); python.touch()
        self.assertEqual(maintenance.runtime_python(release, state), python)

    def test_session_lifecycle_and_new_sessions_do_not_fail_preservation(self):
        before = [{'id': 'a', 'created_at': 'now', 'managed': True, 'running': True, 'tmux_name': 'before'}]
        after = [{'id': 'a', 'created_at': 'now', 'managed': True, 'running': False, 'tmux_name': 'renamed'}, {'id': 'b', 'created_at': 'later'}]
        self.assertTrue(maintenance.sessions_preserved(before, after))
        self.assertFalse(maintenance.sessions_preserved(before, after[1:]))
        self.assertFalse(maintenance.sessions_preserved(before, [dict(before[0], created_at='changed')]))

    def test_launch_agent_round_trip_and_service_commands(self):
        with mock.patch.dict(os.environ, {'AGENT_CONSOLE_SERVICE_BACKEND': 'launchd'}):
            path = maintenance.write_launch_agent(self.root, self.root / 'runner with spaces', self.root / 'state')
            document = plistlib.loads(path.read_bytes())
            self.assertEqual(document['ProgramArguments'], [str(self.root / 'runner with spaces')])
            self.assertTrue(document['RunAtLoad'])
            self.assertNotIn('AGENT_CONSOLE_DB', document['EnvironmentVariables'])
            with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as run:
                maintenance.service_action('enable', home=self.root)
                self.assertEqual(run.call_args.args[0][1], 'bootstrap')
                maintenance.service_action('restart')
                self.assertEqual(run.call_args.args[0][1:3], ['kickstart', '-k'])

    def test_foreground_adapter_never_calls_host_service_manager(self):
        with mock.patch.dict(os.environ, {'AGENT_CONSOLE_SERVICE_BACKEND': 'foreground'}), mock.patch('subprocess.run') as run:
            maintenance.service_action('enable')
            with self.assertRaisesRegex(ValueError, 'supervisor'):
                maintenance.service_action('restart')
            run.assert_not_called()

    def test_health_requires_service_pid_and_exact_release(self):
        response = mock.MagicMock(); response.status = 200; response.read.return_value = b'ok\n'
        response.headers = {'X-Agent-Console-Pid': '42', 'X-Agent-Console-Release': 'release-good'}
        response.__enter__.return_value = response
        opener = mock.Mock(); opener.open.return_value = response
        with mock.patch.object(maintenance, 'service_pid', return_value=42), mock.patch('urllib.request.build_opener', return_value=opener):
            self.assertTrue(maintenance.verify_health('127.0.0.1', 3210, self.root / 'release-good'))
            self.assertFalse(maintenance.verify_health('127.0.0.1', 3210, self.root / 'release-other'))
            response.headers['X-Agent-Console-Pid'] = '99'
            self.assertFalse(maintenance.verify_health('127.0.0.1', 3210, self.root / 'release-good'))


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.runner = ProductionServiceRunner(ServiceConfig(deployment_mode=DeploymentMode.STAGING))
        self.addCleanup(self.runner.stop_canary)
        self.process = mock.Mock(pid=42); self.process.poll.return_value = None

    def test_environment_isolated_and_cleaned_after_stop(self):
        with mock.patch.dict(os.environ, {'AGENT_CONSOLE_DB': '/live/db', 'OPENAI_API_KEY': 'sentinel', 'AGENT_CONSOLE_CONFIG_DIR': '/live/config'}), mock.patch('subprocess.Popen', return_value=self.process) as launch, mock.patch('socket.socket'), mock.patch.object(self.runner, 'check_health', return_value=True):
            self.assertTrue(self.runner.start_canary(Path('/candidate'), '127.0.0.1', 33991))
        env = launch.call_args.kwargs['env']
        isolated = Path(env['HOME'])
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotEqual(env['AGENT_CONSOLE_DB'], '/live/db')
        self.assertTrue(env['AGENT_CONSOLE_DB'].startswith(str(isolated)))
        self.assertEqual(env['AGENT_CONSOLE_CANARY'], '1')
        self.assertEqual(env['AGENT_CONSOLE_DEPLOYMENT_MODE'], 'disabled')
        self.runner.stop_canary()
        self.assertFalse(isolated.exists())
        self.process.terminate.assert_called_once()

    def test_occupied_port_never_launches_candidate(self):
        with mock.patch('socket.socket') as network, mock.patch('subprocess.Popen') as launch:
            network.return_value.__enter__.return_value.bind.side_effect = OSError('already occupied')
            self.assertFalse(self.runner.start_canary(Path('/candidate'), '127.0.0.1', 33991))
            launch.assert_not_called()

    def test_public_bind_rejected(self):
        with self.assertRaisesRegex(ValueError, 'loopback'):
            self.runner.start_canary(Path('/candidate'), '0.0.0.0', 33991)

    def test_dead_candidate_cannot_pass_an_unrelated_http_200(self):
        self.process.poll.return_value = 1
        with mock.patch('socket.socket'), mock.patch('subprocess.Popen', return_value=self.process), mock.patch('httpx.get') as get:
            self.assertFalse(self.runner.start_canary(Path('/candidate'), '127.0.0.1', 33991))
            get.assert_not_called()

    def test_health_requires_nonce_and_process_identity(self):
        self.runner._canary_process = self.process
        self.runner._canary_address = ('127.0.0.2', 33991)
        self.runner._canary_identity = 'candidate-nonce'
        response = mock.Mock(status_code=200, text='ok\n', headers={})
        with mock.patch('httpx.get', return_value=response) as get:
            self.assertFalse(self.runner.check_health(port=33991))
            response.headers = {'X-Agent-Console-Identity': 'candidate-nonce', 'X-Agent-Console-Pid': '42'}
            self.assertTrue(self.runner.check_health(port=33991))
            self.assertIn('127.0.0.2:33991', get.call_args.args[0])
            response.headers['X-Agent-Console-Pid'] = '99'
            self.assertFalse(self.runner.check_health(port=33991))

    def test_timeout_terminates_process_and_removes_private_state(self):
        with mock.patch('socket.socket'), mock.patch('subprocess.Popen', return_value=self.process), mock.patch.object(self.runner, 'check_health', return_value=False), mock.patch('time.sleep'):
            self.assertFalse(self.runner.start_canary(Path('/candidate'), '127.0.0.1', 33991))
        self.assertIsNone(self.runner._canary_directory)
        self.process.terminate.assert_called_once()

    def test_canary_middleware_blocks_api_and_websocket_without_handler(self):
        async def check(scope):
            downstream = mock.AsyncMock(); send = mock.AsyncMock()
            await maintenance.CanaryOnlyMiddleware(downstream)(scope, mock.AsyncMock(), send)
            return downstream, send
        for scope in ({'type': 'http', 'path': '/api/sessions', 'method': 'POST'}, {'type': 'websocket'}):
            downstream, send = asyncio.run(check(scope))
            downstream.assert_not_called(); self.assertTrue(send.called)
        downstream, _ = asyncio.run(check({'type': 'http', 'path': '/healthz', 'method': 'GET'}))
        downstream.assert_awaited_once()
