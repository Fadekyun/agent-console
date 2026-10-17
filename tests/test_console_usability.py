import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_console.cli import main
from agent_console.logging_config import JsonFormatter
from agent_console.workbench_overview import overview


class UsabilityTests(unittest.TestCase):
    def test_profiles_alias_uses_guarded_inspection(self):
        with patch('agent_console.cli.read_route', return_value=[] ) as read, \
                patch('agent_console.cli.SessionManager') as writer, patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['profiles', 'list']), 0)
        self.assertEqual(read.call_args.args[1], ('profile', 'list'))
        writer.assert_not_called()

    def test_restart_alias_reaches_the_existing_handler(self):
        with patch('agent_console.cli.managed_context', return_value=None), \
                patch('agent_console.cli.SessionManager') as factory, \
                patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['session', 'restart', 'example']), 0)
        factory.return_value.restart.assert_called_once_with('example')

    def test_empty_exception_has_type_traceback_and_redacted_context(self):
        try:
            raise RuntimeError('password=fixture-secret-value')
        except RuntimeError:
            import sys
            record = logging.LogRecord('test', logging.ERROR, __file__, 1, 'failed', (), sys.exc_info())
        record.session_id = 'sess-example'
        data = json.loads(JsonFormatter().format(record))
        self.assertEqual(data['exception_type'], 'RuntimeError')
        self.assertEqual(data['process_id'], os.getpid())
        self.assertIn('RuntimeError', data['traceback'])
        self.assertNotIn('fixture-secret-value', json.dumps(data))
        self.assertEqual(data['session_id'], 'sess-example')


class OverviewTests(unittest.TestCase):
    def snapshot(self):
        nodes, sessions, groups = [], [], []
        for index in range(81):
            identity = 'session-' + str(index)
            node = {'id': identity, 'native_id': identity, 'native_name': identity, 'root_id': identity,
                    'title': identity, 'task': 'x' * 2000 + (' needle' if index == 80 else ''),
                    'hidden': index == 79, 'mechanical': 'running' if index == 0 else 'stopped',
                    'waiting': False, 'needs_attention': index in (0, 80), 'attempts': [],
                    'profile': 'general', 'tool': 'codex', 'project_id': None, 'project_name': None}
            nodes.append(node)
            sessions.append({'id': identity, 'tmux_name': identity})
            groups.append({'root_id': identity})
        return {'nodes': nodes, 'sessions': sessions, 'groups': groups, 'aliases': {}, 'readiness': {}}

    def test_initial_response_omits_history_and_bounds_briefs(self):
        view = overview(self.snapshot())
        self.assertEqual(len(view['nodes']), 1)
        self.assertEqual(len(view['nodes'][0]['task']), 300)
        self.assertEqual(view['page']['history_total'], 79)
        self.assertEqual(view['page']['active_attention'], 1)
        self.assertEqual(view['page']['historical_attention'], 1)
        self.assertLess(len(json.dumps(view)), 5000)

    def test_history_pages_do_not_overlap_and_selected_details_are_retained(self):
        first = overview(self.snapshot(), history_limit=20)
        second = overview(self.snapshot(), history_limit=20, history_offset=20)
        self.assertFalse({n['id'] for n in first['nodes'][1:]} & {n['id'] for n in second['nodes'][1:]})
        detail = overview(self.snapshot(), selected='session-80')
        selected = next(n for n in detail['nodes'] if n['id'] == 'session-80')
        self.assertGreater(len(selected['task']), 2000)
        self.assertTrue(detail['groups'][-1]['context_only'])

    def test_search_covers_unloaded_full_briefs_and_hidden_filter(self):
        view = overview(self.snapshot(), query='needle', history_limit=20)
        self.assertEqual([n['id'] for n in view['nodes']], ['session-80'])
        self.assertIn('session-80', view['page']['matched_ids'])
        self.assertEqual(overview(self.snapshot(), include_hidden=True)['page']['history_total'], 80)
