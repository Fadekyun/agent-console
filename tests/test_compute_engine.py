import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent_console import cli
from agent_console.compute_engine import ComputeEngine
from agent_console.compute_cli import run
from agent_console.managed_context import MARKERS
from test_compute_queue import healthy


class ComputeEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.now = 2000000000.0
        self.sampler = Mock()
        self.sampler.sample.side_effect = lambda **kw: {**healthy(self.now), **kw}
        self.engine = ComputeEngine(self.path, enabled=True, sampler=self.sampler, clock=lambda: self.now)
        self.engine.queue.set_hold(held=False, actor='owner')

    def enqueue(self):
        return self.engine.queue.enqueue(kind='maintenance.report', execution_target='n100',
            source_digest='a'*64, input_digest='b'*64, request_key='one', actor='owner')

    def test_local_report_completes_once_without_production_execution(self):
        job = self.enqueue()
        with patch('agent_console.compute_sampler.subprocess.Popen') as launch:
            receipt = self.engine.tick()
            self.assertIsNone(self.engine.tick())
        launch.assert_not_called()
        self.assertEqual(receipt['id'], job['id'])
        result = json.loads(receipt['attempts'][0]['result_json'])
        self.assertEqual(result['execution'], 'in_process_read_only')
        self.assertIs(result['production_writes'], False)
        self.assertEqual(self.engine.status()['counts'], {'completed': 1})

    def test_dispatch_is_disabled_by_default_and_owner_hold_resets_warmup(self):
        job = self.enqueue()
        self.engine.enabled = False
        self.engine.tick()
        self.sampler.sample.assert_called_with(enabled=False, operator_hold=False, now=self.now)
        self.assertEqual(self.engine.queue.inspect(job['id'])['reason'], 'worker_disabled')
        self.engine.enabled = True
        self.engine.queue.set_hold(held=True, actor='owner')
        self.engine.tick()
        self.sampler.sample.assert_called_with(enabled=False, operator_hold=True, now=self.now)
        self.assertEqual(self.engine.queue.inspect(job['id'])['reason'], 'operator_hold')

    def test_unexpected_adapter_failure_retains_receipt_and_never_replays(self):
        job = self.enqueue()
        with patch('agent_console.compute_engine.maintenance_report', side_effect=RuntimeError('fault')):
            with self.assertRaises(RuntimeError):
                self.engine.tick()
        self.assertEqual(len(self.engine.status()['reservations']), 1)
        self.now += 121
        reopened = ComputeEngine(self.path, clock=lambda: self.now)
        self.assertEqual(reopened.status()['counts'], {'unknown': 1})
        self.assertEqual(reopened.queue.inspect(job['id'])['reason'], 'reconciliation_required')
        self.assertIsNone(reopened.tick())
        self.assertEqual(len(reopened.status()['reservations']), 1)

    def test_status_keeps_disabled_reclaimable_laptop_and_unstarted_jobs(self):
        self.enqueue()
        status = self.engine.status()
        laptop = next(w for w in status['workers'] if w['worker_id'] == 'agc-laptop')
        self.assertFalse(laptop['enabled'])
        self.assertTrue(laptop['ram_reclaimable'])
        self.assertEqual(laptop['ram_ceiling_bytes'], 16000000000)
        self.assertEqual(status['counts'], {'queued': 1})
        self.sampler.sample.assert_not_called()

    def test_managed_cli_denies_all_actions_before_store_or_payload_access(self):
        commands = [['status'], ['tick'], ['inspect', 'j'], ['enqueue', '--stdin'],
                    ['hold', '--stdin'], ['retry', 'j'], ['cancel', 'j'],
                    ['reconcile', 'a', '--stdin']]
        for marker in MARKERS:
            for words in commands:
                with self.subTest(marker=marker, words=words), patch.dict(os.environ, {marker: 'sentinel'}):
                    args = cli.parser().parse_args(['compute', *words])
                    factory = Mock()
                    with patch('sys.stdin') as stdin, self.assertRaises(PermissionError):
                        run(args, settings_factory=factory)
                    factory.assert_not_called()
                    stdin.read.assert_not_called()
                    stdin.buffer.read.assert_not_called()

    def test_owner_cli_persists_queue_without_starting_sampler(self):
        clean = {key: '' for key in MARKERS}
        clean['AGENT_CONSOLE_COMPUTE_ENABLED'] = ''
        request = dict(kind='maintenance.report', execution_target='n100',
            source_digest='a'*64, input_digest='b'*64, request_key='cli-one')
        with patch.dict(os.environ, clean), patch('sys.stdin', io.StringIO(json.dumps(request))), \
                patch('agent_console.compute_sampler.subprocess.Popen') as launch:
            args = cli.parser().parse_args(['compute', 'enqueue', '--stdin'])
            receipt = run(args, settings_factory=lambda: SimpleNamespace(state_dir=self.path))
            args = cli.parser().parse_args(['compute', 'status'])
            status = run(args, settings_factory=lambda: SimpleNamespace(state_dir=self.path))
        launch.assert_not_called()
        self.assertEqual(receipt['state'], 'queued')
        self.assertFalse(status['enabled'])


if __name__ == '__main__':
    unittest.main()
