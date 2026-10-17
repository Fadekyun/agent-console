import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from agent_console.environment import write_private
from agent_console.tool_history import tool_calls, tool_output


class ToolHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = SimpleNamespace(state_dir=self.root)
        self.session = {'id': 'sess-' + 'a' * 32, 'tool': 'codex-pro', 'repository': '/fixture', 'execution_kind': 'interactive'}
        self.home = self.root / 'tool-overlays' / 'old-name' / 'codex-home'
        self.home.mkdir(parents=True)
        (self.home / 'sessions').mkdir()
        self.rollout = self.home / 'sessions' / 'rollout.jsonl'
        self.thread = 'native-thread'
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT, source TEXT, cwd TEXT, rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?)', (self.thread, 'cli', '/fixture', str(self.rollout)))
        write_private(self.root / 'environment-launches' / (self.session['id'] + '.json'),
                      {'environment': {'CODEX_HOME': str(self.home)}})
        self.records = [{'type': 'session_meta', 'payload': {'id': self.thread}}]

    def record(self, kind, call_id='call-one', **data):
        self.records.append({'type': 'response_item', 'timestamp': '2026-10-17T00:00:01Z' if kind.endswith('output') else '2026-10-17T00:00:00Z',
                             'payload': {'type': kind, 'call_id': call_id, **data}})

    def save(self):
        self.rollout.write_text(''.join(json.dumps(row) + '\n' for row in self.records))

    def test_metadata_nested_exit_redaction_and_download(self):
        self.record('custom_tool_call', name='exec', input='password=do-not-display')
        self.record('custom_tool_call_output', output=[{'type': 'input_text', 'text': json.dumps({'exit_code': 1, 'output': 'password=hidden-result'})}])
        self.save()
        result = tool_calls(self.settings, self.session)
        call = result['calls'][0]
        self.assertEqual((call['tool'], call['exit_codes'], call['duration_seconds']), ('exec', [1], 1.0))
        self.assertNotIn('hidden-result', json.dumps(result))
        self.assertNotIn('do-not-display', json.dumps(result))
        output = tool_output(self.settings, self.session, offset=call['output_offset'], call_id=call['id'], thread=result['thread'])
        self.assertNotIn('hidden-result', output['text'])

    def test_pagination_and_missing_output_are_explicit(self):
        for i in range(5):
            self.record('function_call', str(i), name='command')
        self.save()
        first = tool_calls(self.settings, self.session, limit=2)
        second = tool_calls(self.settings, self.session, before=first['next_cursor'], thread=first['thread'], limit=2)
        self.assertEqual([c['id'] for c in first['calls']], ['4', '3'])
        self.assertEqual([c['id'] for c in second['calls']], ['2', '1'])
        self.assertEqual(first['calls'][0]['status'], 'output unavailable')

    def test_renamed_session_uses_its_pinned_home(self):
        self.session['tmux_name'] = 'new-name'
        self.record('function_call', name='example')
        self.save()
        self.assertTrue(tool_calls(self.settings, self.session)['available'])

    def test_parallel_outputs_survive_page_boundaries(self):
        self.record('function_call', 'first', name='exec')
        self.record('function_call', 'second', name='exec')
        self.record('function_call_output', 'first', output='first result')
        self.record('function_call_output', 'second', output='second result')
        self.save()
        page = tool_calls(self.settings, self.session, limit=1)
        older = tool_calls(self.settings, self.session, before=page['next_cursor'], thread=page['thread'], limit=1)
        self.assertEqual([call['id'] for call in older['calls']], ['first'])
        self.assertEqual(older['calls'][0]['preview'], 'first result')
        self.assertEqual(older['calls'][0]['status'], 'returned')

    def test_ambiguous_or_external_conversations_are_unavailable(self):
        self.save()
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?)', ('another', 'cli', '/fixture', str(self.rollout)))
        self.assertFalse(tool_calls(self.settings, self.session)['available'])

    def test_supported_provider_and_identity_are_required(self):
        for values in ({'tool': 'pi'}, {'execution_kind': 'integration-plan'}, {'id': '../other'}):
            self.assertFalse(tool_calls(self.settings, {**self.session, **values})['available'])

    def test_tampered_cursor_output_or_header_is_rejected(self):
        self.record('function_call', name='exec')
        self.record('function_call_output', output='safe')
        self.save()
        page = tool_calls(self.settings, self.session)
        with self.assertRaises(ValueError):
            tool_calls(self.settings, self.session, thread='another')
        with self.assertRaises(ValueError):
            tool_output(self.settings, self.session, offset=page['calls'][0]['output_offset'], call_id='another', thread=page['thread'])
        self.records[0]['payload']['id'] = 'wrong'
        self.save()
        self.assertFalse(tool_calls(self.settings, self.session)['available'])

    def test_large_output_is_bounded_and_original_preserved(self):
        self.record('function_call', name='exec')
        self.record('function_call_output', output='z' * 400000)
        self.save()
        size = self.rollout.stat().st_size
        page = tool_calls(self.settings, self.session)
        self.assertEqual(len(page['calls'][0]['preview']), 2048)
        output = tool_output(self.settings, self.session, offset=page['calls'][0]['output_offset'], call_id='call-one', thread=page['thread'])
        self.assertEqual(len(output['text']), 256 * 1024)
        self.assertTrue(output['truncated'])
        self.assertEqual(self.rollout.stat().st_size, size)
        download = tool_output(self.settings, self.session, offset=page['calls'][0]['output_offset'],
                               call_id='call-one', thread=page['thread'], max_bytes=4 * 1024 * 1024)
        self.assertEqual(len(download['text']), 400000)
        self.assertFalse(download['truncated'])

    def test_unicode_output_obeys_the_byte_limit_without_broken_characters(self):
        self.record('function_call', name='exec')
        self.record('function_call_output', output='界' * 100000)
        self.save()
        page = tool_calls(self.settings, self.session)
        call = page['calls'][0]
        output = tool_output(self.settings, self.session, offset=call['output_offset'], call_id=call['id'], thread=page['thread'])
        self.assertLessEqual(len(output['text'].encode('utf-8')), 256 * 1024)
        self.assertTrue(output['truncated'])
        self.assertNotIn('\ufffd', output['text'])
