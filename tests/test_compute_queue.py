from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from agent_console.compute_policy import GiB
from agent_console.compute_queue import ComputeQueue


def healthy(now):
    return dict(physical_host='n100', guest_id='lxc-115', enabled=True, online=True,
                operator_hold=False, sampled_at=now, healthy_since=now-300,
                host_cpu_count=4, host_memory_total_bytes=16*GiB,
                host_available_bytes=6*GiB, guest_available_bytes=2*GiB,
                guest_disk_free_bytes=10*GiB, memory_full_avg60=.1,
                io_some_avg60=.5, thinpool_data_percent=50, thinpool_metadata_percent=20)


class ComputeQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.now = 2000000000.0
        self.queue = ComputeQueue(self.path, clock=lambda: self.now)
        self.queue.set_hold(held=False, actor='test-owner')

    def enqueue(self, key='one', **changes):
        return self.queue.enqueue(**dict(kind='maintenance.report', execution_target='n100',
            source_digest='a'*64, input_digest='b'*64, request_key=key, actor='test-owner', **changes))

    def reserve(self):
        return self.queue.reserve(healthy(self.now))

    def test_spec_is_immutable_idempotent_and_survives_store_reopen(self):
        job = self.enqueue()
        self.assertEqual(job['id'], self.enqueue()['id'])
        with self.assertRaises(ValueError):
            self.queue.enqueue(kind='maintenance.report', execution_target='n100',
                source_digest='c'*64, input_digest='b'*64, request_key='one', actor='test-owner')
        reopened = ComputeQueue(self.path)
        self.assertEqual(reopened.inspect(job['id'])['input_digest'], 'b'*64)
        self.assertEqual(reopened.status()['counts'], {'queued': 1})

    def test_concurrent_reservation_is_atomic_across_dispatchers(self):
        self.enqueue('one')
        self.enqueue('two')
        other = ComputeQueue(self.path, clock=lambda: self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(q.reserve, healthy(self.now)) for q in (self.queue, other)]
            results = [future.result() for future in futures]
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.queue.status()['reservations']), 1)
        self.assertEqual(self.queue.status()['counts'], {'starting': 1, 'blocked': 1})

    def test_durable_hold_overrides_sample_taken_before_owner_pause(self):
        job = self.enqueue()
        sample = healthy(self.now)
        self.queue.set_hold(held=True, actor='test-owner')
        self.assertIsNone(self.queue.reserve(sample))
        self.assertEqual(self.queue.inspect(job['id'])['reason'], 'operator_hold')

    def test_expired_lease_keeps_host_reserved_until_evidenced_reconciliation(self):
        job = self.enqueue()
        attempt = self.reserve()
        self.now += 121
        self.queue.expire()
        self.assertEqual(self.queue.inspect(job['id'])['state'], 'unknown')
        self.enqueue('two')
        self.assertIsNone(self.reserve())
        for operation in (
            lambda: self.queue.heartbeat(attempt['id'], 1),
            lambda: self.queue.finish(attempt['id'], 1, report={'ok': True}),
            lambda: self.queue.retry(job['id'], actor='owner'),
            lambda: self.queue.reconcile_terminated(attempt['id'], evidence='', actor='owner'),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(len(self.queue.status()['reservations']), 1)
        self.queue.reconcile_terminated(attempt['id'], evidence='Verified old service process has exited', actor='owner')
        self.assertEqual(self.queue.status()['reservations'], [])
        self.assertIsNotNone(self.reserve())

    def test_generation_fencing_checkpoint_digest_and_completed_receipt(self):
        job = self.enqueue()
        first = self.reserve()
        self.queue.finish(first['id'], 1, failure='adapter_failed')
        with self.assertRaises(ValueError):
            self.queue.retry(job['id'], actor='owner')
        self.now += 300
        self.queue.retry(job['id'], actor='owner')
        second = self.reserve()
        self.assertEqual(second['generation'], 2)
        with self.assertRaises(ValueError):
            self.queue.finish(first['id'], 1, report={'stale': True})
        self.queue.heartbeat(second['id'], 2, checkpoint_digest='c'*64)
        self.queue.finish(second['id'], 2, report={'summary': 'bounded report'})
        receipt = self.queue.inspect(job['id'])
        self.assertEqual(receipt['state'], 'completed')
        last = receipt['attempts'][-1]
        self.assertEqual(last['checkpoint_digest'], 'c'*64)
        self.assertEqual(json.loads(last['result_json']), {'summary': 'bounded report'})
        self.assertEqual(len(last['result_digest']), 64)
        self.assertEqual(self.queue.status()['reservations'], [])
        with self.assertRaises(ValueError):
            self.queue.retry(job['id'], actor='owner')

    def test_oom_never_retries_same_budget_and_exit_137_is_not_oom_evidence(self):
        job = self.enqueue()
        attempt = self.reserve()
        with self.assertRaises(ValueError):
            self.queue.finish(attempt['id'], 1, failure='exit_137')
        self.queue.finish(attempt['id'], 1, failure='oom')
        self.now += 10000
        with self.assertRaises(ValueError):
            self.queue.retry(job['id'], actor='owner')

    def test_heartbeat_cannot_keep_attempt_alive_past_profile_deadline(self):
        attempt = self.reserve() if self.enqueue() else None
        for seconds in (100, 200, 299):
            self.now = attempt['started_at'] + seconds
            self.queue.heartbeat(attempt['id'], 1)
        self.now += 1
        with self.assertRaises(ValueError):
            self.queue.heartbeat(attempt['id'], 1)
        self.queue.expire()
        self.assertEqual(len(self.queue.status()['reservations']), 1)

    def test_retry_is_bounded_and_requires_termination(self):
        job = self.enqueue()
        for generation in (1, 2, 3):
            attempt = self.reserve()
            self.assertEqual(attempt['generation'], generation)
            self.queue.finish(attempt['id'], generation, failure='adapter_failed')
            self.now += 1800
            if generation < 3:
                self.queue.retry(job['id'], actor='owner')
        with self.assertRaises(ValueError):
            self.queue.retry(job['id'], actor='owner')

    def test_laptop_queue_is_durable_and_cannot_starve_local_report(self):
        for i in range(105):
            self.queue.enqueue(kind='yuyutei.collect', execution_target='agc-laptop',
                source_digest='a'*64, input_digest='b'*64, request_key=f'laptop-{i}', actor='owner')
        job = self.enqueue()
        self.assertEqual(self.reserve()['job_id'], job['id'])
        self.assertEqual(self.queue.status()['counts']['queued'], 105)

    def test_cancel_does_not_release_a_live_attempt(self):
        job = self.enqueue()
        self.reserve()
        with self.assertRaises(ValueError):
            self.queue.cancel(job['id'], actor='owner')
        unstarted = self.enqueue('second')
        self.assertEqual(self.queue.cancel(unstarted['id'], actor='owner')['state'], 'cancelled')
        self.assertEqual(len(self.queue.status()['reservations']), 1)

    def test_future_schema_fails_closed_without_erasing_records(self):
        job = self.enqueue()
        with self.queue.store.connect(write=True) as db:
            db.execute('UPDATE compute_schema SET version=99')
        with self.assertRaisesRegex(ValueError, 'unsupported compute schema'):
            ComputeQueue(self.path)
        self.assertEqual(self.queue.inspect(job['id'])['state'], 'queued')


if __name__ == '__main__':
    unittest.main()
