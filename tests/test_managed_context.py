import contextlib
import io
import itertools
import json
import os
import unittest
from unittest.mock import Mock, patch

from agent_console import cli, session_client, workflow_cli
from agent_console.managed_context import CONTEXT_ERROR, MARKERS, managed_context

VALUES = ('http://console-fixture.invalid:3210', 'fixture-session-id', 'fixture-private-capability',
          'fixture-session-name', '/fixture-private-context')
SESSION_COMMANDS = [
    ['session', 'list'], ['session', 'inspect', 'peer'], ['session', 'tree', '--json'],
    ['session', 'review', 'peer', '--json'], ['session', 'context', 'peer'],
    ['session', 'relatives', '--current'], ['session', 'group', 'list'],
    ['session', 'attention', '--current', '--state', 'ready_for_review'],
    ['session', 'create', '--tool', 'shell'], ['session', 'interrupt', 'peer'],
    ['session', 'restart-agent', 'peer'], ['session', 'kill', 'peer', '--yes'],
    ['delegate', 'verifier', '--parent', 'fixture-session-id', '--task', 'Check'],
]
WORKFLOW_COMMANDS = [
    ['workflow', 'results', 'fixture-session-id'], ['workflow', 'result', 'result-fixture'],
    ['workflow', 'connections', '--current'], ['workflow', 'inbox', '--current'],
    ['workflow', 'ack', 'item-fixture', '--state', 'consumed', '--current'],
    ['workflow', 'send', 'result-fixture', '--to', 'peer', '--request-key', 'fixture-key'],
    ['workflow', 'publish', '--current', '--kind', 'final', '--outcome', 'pass',
     '--summary-file', '/fixture-must-not-be-read', '--request-key', 'fixture-key'],
    ['workflow', 'propose', '--current', '--task', 'Check', '--reason', 'Separate check',
     '--expected-output', 'Findings', '--tool', 'codex', '--profile', 'verifier', '--request-key', 'fixture-key'],
]


def marker_combinations():
    for present in itertools.product((False, True), repeat=len(MARKERS)):
        if any(present) and not all(present[:3]):
            yield {key: value if keep else '' for key, value, keep in zip(MARKERS, VALUES, present)}


class ManagedContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load command schemas before observing request-input access. Import-time
        # package metadata reads are not reads of the requested summary file.
        cli.parser()

    def invoke(self, words):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(words)
        return code, out.getvalue(), err.getvalue()

    def test_all_partial_marker_combinations_refuse_before_local_read_writer_input_or_network(self):
        for markers in marker_combinations():
            for words in SESSION_COMMANDS + WORKFLOW_COMMANDS:
                with self.subTest(markers=[key for key, value in markers.items() if value], command=words[:3]), \
                     patch.dict(os.environ, markers, clear=True), \
                     patch.object(cli, 'SessionManager') as cli_manager, \
                     patch('agent_console.manager.SessionManager') as manager, \
                     patch.object(cli, 'read_route') as reader, \
                     patch.object(session_client, 'request') as session_request, \
                     patch.object(workflow_cli, 'remote_run') as workflow_request, \
                     patch('pathlib.Path.open') as file_input, \
                     patch('urllib.request.urlopen') as urlopen, \
                     patch('urllib.request.build_opener') as opener:
                    code, out, error = self.invoke(words)
                    self.assertEqual(code, 2)
                    if '--json' in words:
                        self.assertEqual(json.loads(out)['error']['code'], 'inspection-unavailable')
                    else:
                        self.assertEqual(out, '')
                    self.assertIn(CONTEXT_ERROR, error)
                    for value in VALUES:
                        self.assertNotIn(value, error)
                    for mocked in (cli_manager, manager, reader, session_request, workflow_request, file_input, urlopen, opener):
                        mocked.assert_not_called()

    def test_direct_transports_check_partial_context_before_summary_input_or_http(self):
        args = cli.parser().parse_args(WORKFLOW_COMMANDS[-2])
        for markers in marker_combinations():
            with self.subTest(markers=tuple(key for key, value in markers.items() if value)), \
                 patch.dict(os.environ, markers, clear=True), patch('pathlib.Path.open') as source, \
                 patch('urllib.request.urlopen') as http, patch('urllib.request.build_opener') as opener:
                with self.assertRaisesRegex(PermissionError, 'configured AGENT_CONSOLE_REPORTING_URL'):
                    workflow_cli.remote_run(args)
                with self.assertRaises(PermissionError):
                    session_client.request('read', {})
                source.assert_not_called(); http.assert_not_called(); opener.assert_not_called()

    def test_invalid_address_and_whitespace_markers_are_value_blind(self):
        variants = ['secret-not-a-url', 'https://username:secret@host', 'http://host:secret-port',
                    'http://[invalid-secret', 'file:///private/secret', 'http://host/?token=secret', '  ']
        for value in variants:
            with self.subTest(kind=variants.index(value)), patch.dict(os.environ, dict(zip(MARKERS, (value, *VALUES[1:]))), clear=True):
                with self.assertRaises(PermissionError) as error:
                    managed_context()
                self.assertEqual(str(error.exception), CONTEXT_ERROR)
        with patch.dict(os.environ, dict(zip(MARKERS, (VALUES[0], ' ', VALUES[2]))), clear=True):
            with self.assertRaises(PermissionError):
                managed_context()

    def test_valid_session_context_routes_reads_and_controls_without_local_access(self):
        for words in [SESSION_COMMANDS[0], SESSION_COMMANDS[1], SESSION_COMMANDS[7], SESSION_COMMANDS[8]]:
            with self.subTest(command=words[:3]), patch.dict(os.environ, dict(zip(MARKERS, VALUES)), clear=True), \
                 patch.object(cli, 'SessionManager') as manager, patch.object(cli, 'read_route') as reader, \
                 patch.object(session_client, 'request', return_value={'ok': True}) as remote:
                code, _, error = self.invoke(words)
                self.assertEqual((code, error), (0, ''))
                remote.assert_called_once(); manager.assert_not_called(); reader.assert_not_called()

    def test_valid_session_transport_uses_bound_context_headers(self):
        opener = Mock(); opener.open.return_value = io.BytesIO(b'{"ok": true}')
        with patch.dict(os.environ, dict(zip(MARKERS, VALUES)), clear=True), \
             patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(session_client.request('read', {'route': ['session', 'list']}), {'ok': True})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, VALUES[0] + '/api/agent-sessions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + VALUES[2])
        self.assertEqual(request.get_header('X-agent-console-session'), VALUES[1])

    def test_valid_workflow_uses_http_and_never_constructs_local_manager(self):
        args = cli.parser().parse_args(['workflow', 'inbox', '--current'])
        with patch.dict(os.environ, dict(zip(MARKERS, VALUES)), clear=True), \
             patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"items": []}')) as http, \
             patch('agent_console.manager.SessionManager') as manager:
            self.assertEqual(workflow_cli.run(args), {'items': []})
            manager.assert_not_called()
        request = http.call_args.args[0]
        self.assertEqual(request.full_url, VALUES[0] + '/api/agent-workflow')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + VALUES[2])
        self.assertEqual(request.get_header('X-agent-console-session'), VALUES[1])

    def test_unmarked_local_reads_and_owner_writer_commands_keep_their_routes(self):
        for markers in ({}, dict.fromkeys(MARKERS, '')):
            with patch.dict(os.environ, markers, clear=True), patch.object(cli, 'read_route', return_value=[]) as reader, \
                 patch.object(cli, 'SessionManager') as manager, patch.object(session_client, 'request') as remote:
                self.assertEqual(self.invoke(['session', 'list'])[0], 0)
                reader.assert_called_once(); manager.assert_not_called(); remote.assert_not_called()
            with patch.dict(os.environ, markers, clear=True), patch.object(cli, 'SessionManager') as manager, \
                 patch('agent_console.owner_cli.run', return_value=[]) as owner:
                self.assertEqual(self.invoke(['project', 'list'])[0], 0)
                manager.assert_called_once(); owner.assert_called_once()

    def test_unmarked_workflow_and_explicit_injected_manager_remain_local(self):
        args = cli.parser().parse_args(['workflow', 'results', 'fixture'])
        for injected, markers in [(None, {}), (Mock(), {MARKERS[1]: VALUES[1]})]:
            with self.subTest(injected=injected is not None), patch.dict(os.environ, markers, clear=True), \
                 patch('agent_console.manager.SessionManager') as constructor, \
                 patch.object(workflow_cli, 'WorkflowService') as service, patch.object(workflow_cli, 'remote_run') as remote:
                service.return_value.session.return_value = {'id': 'fixture'}
                service.return_value.store.results.return_value = []
                self.assertEqual(workflow_cli.run(args, manager=injected), {'results': []})
                service.assert_called_once_with(injected if injected is not None else constructor.return_value)
                if injected is not None:
                    constructor.assert_not_called()
                else:
                    constructor.assert_called_once()
                remote.assert_not_called()

    def test_owner_manage_still_rejects_any_managed_marker_before_input(self):
        for markers in marker_combinations():
            with patch.dict(os.environ, markers, clear=True), patch.object(cli, 'SessionManager') as manager, \
                 patch('agent_console.operator_workflow_cli.payload') as payload:
                code, _, error = self.invoke(['workflow', 'manage', 'control', 'root', '--stdin'])
                self.assertEqual(code, 2); self.assertIn('local human owner terminal', error)
                manager.assert_not_called(); payload.assert_not_called()
