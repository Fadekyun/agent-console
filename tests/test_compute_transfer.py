"""No live IO: routing races, immutable retries and external-write fencing."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import unittest

from agent_console.compute_queue import ComputeQueue
from agent_console.compute_publication import ComputePublications
from agent_console.workflow_store import canonical
import test_compute_emergency as emergency
import test_compute_remote as remote


class TransferTests(unittest.TestCase):
    setUp = remote.RemoteTests.setUp

    def enqueue(self, **changes):
        manifest = emergency.emergency_manifest(self.now)
        manifest['kind'] = 'bushi.run'
        manifest.pop('emergency')
        request = dict(kind='bushi.run', execution_target='agc-laptop',
            source_digest=remote.SHA, input_digest='b'*64, request_key='test:paid',
            actor='producer:test', manifest=manifest, priority=20, deadline=int(self.now+1100))
        request.update(changes)
        self.original = request
        return self.queue.enqueue(**request)

    def request(self, job, **changes):
        value = dict(request_key='operator:transfer-one', expected_manifest_sha256=job['manifest_digest'],
            deadline=int(self.now+600), reason='Laptop unavailable before paid action cutoff', actor='owner')
        value.update(changes)
        return value

    def transfer(self, job, **changes):
        return self.queue.transfer_emergency(job['id'], **self.request(job, **changes))

    def test_same_job_claim_and_original_enqueue_survive_transfer_restart_and_start(self):
        job = self.enqueue(not_before=int(self.now-10))
        self.assertIsNone(self.queue.reserve(remote.laptop(self.now, online=False)))
        receipt = self.transfer(job)
        self.queue = ComputeQueue(self.path, clock=lambda:self.now)
        current = self.queue.inspect(job['id'])
        self.assertEqual(current['transfers'], [receipt])
        for key in ('id','actor','request_key','spec_hash','source_digest','input_digest','not_before','dependencies_json'):
            self.assertEqual(current[key], job[key])
        self.assertEqual((current['kind'], current['execution_target'], current['priority']), ('bushi.emergency','n100',90))
        expected = {**self.original['manifest'], 'kind':'bushi.emergency',
                    'emergency':{'urgent':True,'deadline':int(self.now+600)}}
        self.assertEqual(json.loads(current['manifest_json']), expected)
        self.assertEqual(receipt['to_manifest_sha256'], hashlib.sha256(canonical(expected).encode()).hexdigest())
        with self.queue.store.connect() as db:
            record = db.execute('SELECT * FROM compute_transfers').fetchone()
            self.assertEqual(hashlib.sha256(record['original_spec_json'].encode()).hexdigest(),job['spec_hash'])
            self.assertEqual(record['original_manifest_json'],job['manifest_json'])
            self.assertEqual(db.execute('SELECT job_id FROM compute_business_claims').fetchone()[0],job['id'])
        self.assertEqual(self.queue.enqueue(**self.original)['transfers'],[receipt])
        with self.assertRaises(PermissionError):self.queue.enqueue(**{**self.original,'actor':'producer:other'})
        with self.assertRaisesRegex(ValueError,'different immutable'):
            self.queue.enqueue(**{**self.original,'manifest':expected,'kind':'bushi.emergency','execution_target':'n100'})
        self.assertIsNone(self.queue.reserve(remote.laptop(self.now)))
        attempt = self.queue.reserve(emergency.emergency_telemetry(self.now))
        self.assertEqual(attempt['job_id'],job['id'])
        self.assertEqual(self.queue.status()['resource_reservations'][0]['resource'],'bushi-organizer')
        self.now += 2000
        self.assertEqual(self.queue.transfer_emergency(job['id'],**self.request(job,deadline=int(self.now-1400))),receipt)
        self.queue.mark_external_unknown(attempt['id'],1,terminated=True)
        self.assertEqual(len(self.queue.status()['resource_reservations']),1)

    def test_deadlines_and_actor_cannot_bypass_original_bounds(self):
        job = self.enqueue(not_before=int(self.now+100))
        for changes in ({'deadline':int(self.now)}, {'deadline':int(self.now+1201)},
                        {'deadline':int(self.now+1150)}, {'deadline':int(self.now+100)},
                        {'deadline':True}, {'expected_manifest_sha256':'f'*64}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.transfer(job,**changes)
        with self.assertRaises(PermissionError):self.transfer(job,actor='producer:test')
        self.assertEqual(self.queue.inspect(job['id'])['transfers'],[])

    def test_any_attempt_terminal_state_or_missing_claim_prevents_transfer(self):
        job = self.enqueue()
        attempt = self.queue.reserve(remote.laptop(self.now))
        self.queue.finish(attempt['id'],1,failure='adapter_failed')
        # Even a separately re-queued historical job can never transfer.
        with self.queue.store.connect(write=True) as db:
            db.execute("UPDATE compute_jobs SET state='queued',generation=0 WHERE id=?",(job['id'],))
        with self.assertRaisesRegex(ValueError,'prior attempt'):self.transfer(job)

    def test_cancelled_and_noneligible_jobs_are_not_transferred(self):
        job = self.enqueue()
        self.queue.cancel(job['id'],actor='owner')
        with self.assertRaises(ValueError):self.transfer(job)
        with self.queue.store.connect(write=True) as db:
            db.execute("UPDATE compute_jobs SET state='queued' WHERE id=?",(job['id'],))
            db.execute('DELETE FROM compute_business_claims')
        with self.assertRaisesRegex(ValueError,'business claim'):self.transfer(job)

    def test_publication_evidence_prevents_transfer_even_without_attempt(self):
        job = self.enqueue()
        ComputePublications(self.queue)
        with self.queue.store.connect(write=True) as db:
            db.execute("INSERT INTO compute_publications VALUES('p',?,'target','publication-key','digest','producer:test','unknown','token',0,0,NULL,NULL)",(job['id'],))
        with self.assertRaisesRegex(ValueError,'publication evidence'):self.transfer(job)

    def test_idempotent_operator_retries_conflict_on_every_changed_intent(self):
        job = self.enqueue()
        receipt = self.transfer(job)
        self.assertEqual(self.transfer(job),receipt)
        for changes in ({'deadline':int(self.now+700)}, {'reason':'Different'}, {'actor':'other-owner'},
                        {'request_key':'operator:another'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.transfer(job,**changes)
        self.assertEqual(len(self.queue.inspect(job['id'])['transfers']),1)

    def test_reserve_vs_transfer_serializes_with_one_physical_attempt(self):
        job = self.enqueue()
        def move():
            try:return self.transfer(job)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            transfer = pool.submit(move)
            reserve = pool.submit(self.queue.reserve,remote.laptop(self.now))
            receipt, attempt = transfer.result(), reserve.result()
        self.assertNotEqual(receipt is None,attempt is None)
        if receipt:
            self.assertIsNotNone(self.queue.reserve(emergency.emergency_telemetry(self.now)))
        else:
            self.assertIsNone(self.queue.reserve(emergency.emergency_telemetry(self.now)))
        self.assertEqual(len(self.queue.inspect(job['id'])['attempts']),1)

    def test_failed_event_write_rolls_back_manifest_route_and_receipt(self):
        job = self.enqueue()
        from unittest.mock import patch
        with patch.object(self.queue,'_event',side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):self.transfer(job)
        current = self.queue.inspect(job['id'])
        self.assertEqual(current['kind'],'bushi.run')
        self.assertEqual(current['manifest_digest'],job['manifest_digest'])
        self.assertEqual(current['transfers'],[])


if __name__ == '__main__':unittest.main()
