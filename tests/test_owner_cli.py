import contextlib
import errno
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch, Mock

from agent_console import cli, session_client
from agent_console.config import Settings
from agent_console.manager import SessionManager

MARKERS = ('AGENT_CONSOLE_REPORTING_URL', 'AGENT_CONSOLE_SESSION_ID', 'AGENT_CONSOLE_EVIDENCE_CAPABILITY')


class OwnerCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = SessionManager(Settings(workspace_root=self.root, state_dir=self.root/'state',
            database_path=self.root/'state/db.sqlite', profile_dir=self.root/'profiles',
            handoff_dir=self.root/'handoff', worktree_root=self.root/'worktrees', tmux_socket='unused'))
        self.env = patch.dict(os.environ, {key: '' for key in MARKERS})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def invoke(self, words, value=''):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'SessionManager', return_value=self.manager), patch('sys.stdin', io.StringIO(value)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(words)
        return code, out.getvalue(), err.getvalue()

    def ok(self, words, value=''):
        code, out, err = self.invoke(words, value)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_project_crud_validation_and_environment_cleanup(self):
        project = self.ok(['project', 'create', 'Example', '--repository', str(self.root), '--description', 'Brief'])
        pid = project['id']
        self.assertEqual(self.ok(['project', 'list'])[0]['id'], pid)
        self.assertEqual(self.ok(['project', 'show', pid])['name'], 'Example')
        self.assertEqual(self.ok(['project', 'update', pid, '--name', 'Renamed', '--status', 'paused'])['status'], 'paused')
        self.assertEqual(self.invoke(['project', 'create', 'x'*201])[0], 2)
        self.assertEqual(self.invoke(['project', 'create', 'Escape', '--repository', '/'])[0], 2)
        self.ok(['environment', 'set', 'APP_KEY', '--project', pid, '--stdin'], 'private-sentinel')
        self.ok(['project', 'delete', pid])
        self.assertEqual(self.invoke(['project', 'show', pid])[0], 2)
        self.assertNotIn(pid, self.manager.environment._read()['scopes'])

    def test_assign_unassign_respects_backend_guards(self):
        project = self.ok(['project', 'create', 'Example', '--repository', str(self.root)])
        pid = project['id']
        with self.manager.database.connect() as conn:
            conn.execute("INSERT INTO sessions(id,tmux_name,tool,profile,repository,status,managed,created_at) VALUES('sess-fixture','fixture','shell','general',?,'stopped',1,'now')", (str(self.root),))
        self.ok(['project', 'assign', pid, 'fixture'])
        self.assertEqual(self.ok(['project', 'show', pid])['sessions'][0]['tmux_name'], 'fixture')
        self.assertEqual(self.invoke(['project', 'delete', pid])[0], 2)
        other = self.ok(['project', 'create', 'Other', '--repository', str(self.root)])
        self.assertEqual(self.invoke(['project', 'assign', other['id'], 'fixture'])[0], 2)
        self.assertEqual(self.invoke(['project', 'unassign', other['id'], 'fixture'])[0], 2)
        self.ok(['project', 'unassign', pid, 'fixture'])
        self.ok(['project', 'update', pid, '--status', 'paused'])
        self.assertEqual(self.invoke(['project', 'assign', pid, 'fixture'])[0], 2)
        self.ok(['project', 'delete', pid])

    def test_environment_states_scope_and_value_blind_output(self):
        pid = self.ok(['project', 'create', 'Example'])['id']
        secret = 'private-sentinel\n$(literal)\n'
        for scope in ([], ['--project', pid]):
            result = self.ok(['environment', 'set', 'APP_KEY', '--stdin', *scope], secret)
            self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(self.manager.environment.resolve(pid if scope else None, baseline={})[0]['APP_KEY'], secret)
            self.ok(['environment', 'disable', 'APP_KEY', *scope])
            self.ok(['environment', 'enable', 'APP_KEY', *scope])
            self.assertNotIn('private-sentinel', json.dumps(self.ok(['environment', 'list', *scope])))
        self.ok(['environment', 'suppress', 'APP_KEY', '--project', pid])
        self.assertNotIn('APP_KEY', self.manager.environment.resolve(pid, baseline={})[0])
        self.ok(['environment', 'unset', 'APP_KEY', '--project', pid])
        self.assertEqual(self.manager.environment.resolve(pid, baseline={})[0]['APP_KEY'], secret)
        self.assertEqual(self.invoke(['environment', 'suppress', 'APP_KEY'])[0], 2)
        self.assertEqual(self.invoke(['environment', 'enable', 'MISSING'])[0], 2)
        self.assertEqual(self.manager.environment.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b'private-sentinel', self.manager.database.path.read_bytes())
        self.ok(['environment', 'unset', 'APP_KEY'])
        self.assertNotIn('APP_KEY', self.manager.environment.resolve(baseline={})[0])

    def test_invalid_secrets_and_scopes_are_value_blind_and_do_not_write(self):
        for name, secret in [('PATH', 'private-sentinel'), ('KEY', 'private-sentinel\0'), ('KEY', 'private-sentinel'+'x'*32769)]:
            code, out, err = self.invoke(['environment', 'set', name, '--stdin'], secret)
            self.assertEqual(code, 2)
            self.assertNotIn('private-sentinel', out+err)
        self.assertFalse(self.manager.environment.path.exists())
        self.assertEqual(self.invoke(['environment', 'set', 'KEY', '--stdin', '--project', 'proj-missing'], 'private-sentinel')[0], 2)
        self.assertFalse(self.manager.environment.path.exists())
        self.assertEqual(self.invoke(['environment', 'set', 'KEY'])[0], 2)
        stream = io.StringIO(); stream.isatty = lambda: True
        with patch.object(cli, 'SessionManager', return_value=self.manager), patch('sys.stdin', stream), patch('agent_console.owner_cli.getpass.getpass', return_value='hidden-sentinel') as prompt, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['environment', 'set', 'KEY']), 0)
        prompt.assert_called_once()
        self.assertNotIn('hidden-sentinel', out.getvalue())
        self.assertEqual(self.manager.environment.resolve(baseline={})[0]['KEY'], 'hidden-sentinel')

    def test_hidden_prompt_failure_and_invalid_utf8_do_not_write(self):
        import getpass
        stream = io.StringIO(); stream.isatty = lambda: True
        with patch.object(cli, 'SessionManager', return_value=self.manager), patch('sys.stdin', stream), patch('agent_console.owner_cli.getpass.getpass', side_effect=getpass.GetPassWarning('hidden-sentinel')), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['environment', 'set', 'KEY']), 2)
        self.assertNotIn('hidden-sentinel', err.getvalue())
        stream = io.TextIOWrapper(io.BytesIO(b'private-sentinel\xff'), encoding='utf-8')
        with patch.object(cli, 'SessionManager', return_value=self.manager), patch('sys.stdin', stream), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['environment', 'set', 'KEY', '--stdin']), 2)
        self.assertNotIn('private-sentinel', err.getvalue())
        self.assertFalse(self.manager.environment.path.exists())

    def test_help_documents_owner_scope_and_safe_input(self):
        for words, expected in [(['--help'], 'project'), (['environment', 'set', '--help'], '--stdin'), (['project', '--help'], 'unassign')]:
            with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as raised:
                cli.main(words)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn(expected, out.getvalue())

    def test_accidental_argv_secret_is_not_echoed(self):
        for words in [['environment', 'set', 'KEY', 'private-sentinel'], ['environment', 'set', 'KEY', '--value', 'private-sentinel']]:
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                cli.main(words)
            self.assertNotIn('private-sentinel', err.getvalue())

    def test_managed_owner_commands_reject_before_manager_or_secret_input(self):
        commands = [['project', 'list'], ['project', 'show', 'p'], ['project', 'create', 'Example'],
            ['project', 'update', 'p', '--name', 'Example'], ['project', 'delete', 'p'],
            ['project', 'assign', 'p', 's'], ['project', 'unassign', 'p', 's'],
            ['environment', 'list'], ['environment', 'set', 'KEY', '--stdin'],
            *[['environment', action, 'KEY'] for action in ('unset', 'enable', 'disable', 'suppress')]]
        for marker in MARKERS:
            with patch.dict(os.environ, {marker: 'managed'}):
                for words in commands:
                    with self.subTest(marker=marker, words=words), patch.object(cli, 'SessionManager') as manager, contextlib.redirect_stderr(io.StringIO()) as err:
                        self.assertEqual(cli.main(words), 2)
                        manager.assert_not_called()
                        self.assertIn('unsupported in managed sessions', err.getvalue())


class SessionNetworkErrorTests(unittest.TestCase):
    def test_network_permission_denials_are_actionable_and_redacted(self):
        for exception in (PermissionError(errno.EACCES, 'payload-secret'), urllib.error.URLError(PermissionError(errno.EPERM, 'payload-secret')), urllib.error.URLError(OSError(errno.EACCES, 'payload-secret'))):
            opener = Mock(); opener.open.side_effect = exception
            with patch.dict(os.environ, dict(zip(MARKERS, ['http://endpoint-secret', 'session', 'token-secret']))), patch('urllib.request.build_opener', return_value=opener), self.assertRaises(RuntimeError) as raised:
                session_client.request('read', {})
            message = str(raised.exception)
            self.assertIn('sandbox', message)
            self.assertIn('authorized network access', message)
            for secret in ('payload-secret', 'endpoint-secret', 'token-secret'):
                self.assertNotIn(secret, message)

    def test_remote_error_payload_is_not_echoed(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('http://endpoint-secret', 403, 'token-secret', {}, io.BytesIO(b'{"detail":"payload-secret"}'))
        with patch.dict(os.environ, dict(zip(MARKERS, ['http://endpoint-secret', 'session', 'token-secret']))), patch('urllib.request.build_opener', return_value=opener), self.assertRaises(ValueError) as raised:
            session_client.request('read', {})
        self.assertIn('HTTP 403', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))
