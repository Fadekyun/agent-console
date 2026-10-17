import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_console.compute_artifacts import ComputeArtifacts
from agent_console.compute_engine import ComputeEngine
from agent_console.compute_manifest import validate_manifest
from agent_console.compute_policy import admission, GiB
from agent_console.compute_producer_api import producer_routes
from agent_console.compute_publication import ComputePublications
from agent_console.compute_queue import ComputeQueue
from agent_console.compute_transport import WorkerUnavailable, binding
from test_compute_queue import healthy

NOW = 1792227600.0  # Saturday in Singapore
DATA = b'exact reviewed source archive'
SHA = hashlib.sha256(DATA).hexdigest()


def manifest(kind='agc.build', production_writes=False):
    return {'version': 1, 'kind': kind, 'source_sha': 'a' * 40,
            'source_artifact': {'sha256': SHA, 'size': len(DATA)},
            'input_sha256': 'b' * 64, 'parameters': {}, 'production_writes': production_writes}


def laptop(now=NOW, **changes):
    value = dict(physical_host='agc-laptop', guest_id='AGC-Worker', enabled=True,
                 operator_hold=False, online=True, sampled_at=now, healthy_since=now-300,
                 host_cpu_count=16, host_memory_total_bytes=32*GiB, host_available_bytes=24*GiB,
                 guest_available_bytes=15_000_000_000, guest_disk_free_bytes=150*GiB,
                 host_disk_total_bytes=500*GiB, host_disk_free_bytes=300*GiB,
                 worker_memory_bytes=GiB, worker_disk_used_bytes=10*GiB,
                 host_cpu_percent=10, host_commit_percent=30, on_ac=True, policy_verified=True, worker_ready=True,
                 capabilities=['agc.build','bushi.run','yuyutei.collect'])
    value.update(changes)
    return value


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.now = NOW
        self.queue = ComputeQueue(self.path, clock=lambda: self.now)
        self.queue.set_hold(held=False, actor='owner')
        self.artifacts = ComputeArtifacts(self.path)
        self.artifacts.put(SHA, size=len(DATA), offset=0,
                           data=base64.b64encode(DATA).decode(), final=True)

    def enqueue(self, key='one', kind='agc.build', actor='producer:test', **changes):
        return self.queue.enqueue(kind=kind, execution_target='agc-laptop', source_digest=SHA,
            input_digest='b'*64, request_key=key, actor=actor, manifest=manifest(kind), **changes)

    def test_schedule_staff_priority_and_verified_actual_capacity(self):
        self.assertIsNone(admission('agc.build', laptop(), [], NOW))
        for changes, expected in [({'host_cpu_count': 8}, 'host_cpu_capacity'),
                                  ({'on_ac': False}, 'waiting_for_ac'),
                                  ({'policy_verified': False}, 'worker_policy_unverified'),
                                  ({'host_available_bytes': 10*GiB}, 'staff_memory_reserve'),
                                  ({'host_cpu_percent': 90}, 'staff_cpu_priority'),
                                  ({'host_commit_percent': 85}, 'staff_memory_priority'),
                                  ({'worker_memory_bytes': 8*GiB}, 'worker_memory_ceiling'),
                                  ({'host_disk_free_bytes': 80*GiB}, 'staff_disk_reserve')]:
            self.assertEqual(admission('agc.build', laptop(**changes), [], NOW), expected)

    def test_dependencies_deadlines_and_priority_are_durable(self):
        first = self.enqueue('first')
        second = self.enqueue('second', dependencies=[first['id']], priority=100)
        expired = self.enqueue('expired', deadline=self.now-1, priority=100)
        later = self.enqueue('later', not_before=self.now+100, priority=100)
        attempt = self.queue.reserve(laptop(self.now))
        self.assertEqual(attempt['job_id'], first['id'])
        self.assertEqual(self.queue.inspect(second['id'])['reason'], 'dependency_not_completed')
        self.assertEqual(self.queue.inspect(expired['id'])['reason'], 'deadline_passed_requires_review')
        self.assertEqual(self.queue.inspect(later['id'])['reason'], 'not_before')
        self.queue.finish(attempt['id'], attempt['generation'], report={'ok': True})
        self.assertEqual(ComputeQueue(self.path, clock=lambda: self.now).reserve(laptop(self.now))['job_id'], second['id'])
        with self.assertRaises(ValueError):
            self.enqueue('second', dependencies=[first['id']], priority=99)

    def test_cross_producer_dependency_and_production_replay_are_rejected(self):
        job = self.enqueue()
        with self.assertRaises(PermissionError):
            self.enqueue('other', actor='producer:other', dependencies=[job['id']])
        m = manifest('bushi.run', production_writes=True)
        write = self.queue.enqueue(kind='bushi.run', execution_target='agc-laptop',source_digest=SHA,
            input_digest='b'*64,request_key='write',actor='producer:test',manifest=m,priority=100)
        attempt = self.queue.reserve(laptop())
        self.queue.finish(attempt['id'], 1, failure='adapter_failed')
        self.now += 300
        with self.assertRaises(ValueError):
            self.queue.retry(write['id'], actor='owner')

    def test_chunks_resume_but_corrupt_outputs_never_become_usable(self):
        data = b'another object'
        sha = hashlib.sha256(data).hexdigest()
        first = base64.b64encode(data[:3]).decode()
        self.artifacts.put(sha, size=len(data), offset=0, data=first)
        self.assertEqual(self.artifacts.put(sha, size=len(data), offset=0, data=first)['offset'], 3)
        with self.assertRaises(ValueError):
            self.artifacts.put(sha, size=len(data), offset=3,
                data=base64.b64encode(b'x'*(len(data)-3)).decode(), final=True)
        self.assertFalse(self.artifacts.has({'sha256': sha, 'size': len(data)}))
        with self.assertRaises(ValueError):
            self.artifacts.get('../secret')

    def test_publication_claim_is_single_use_and_timeout_never_replays(self):
        job = self.enqueue()
        attempt = self.queue.reserve(laptop())
        self.queue.finish(attempt['id'], 1, report={'summary': 'verified artifact'})
        publication = ComputePublications(self.queue)
        claim = publication.begin(job['id'], target='site:prod',request_key='publish1',actor='producer:test')
        self.assertTrue(claim['may_publish'])
        again = publication.begin(job['id'], target='site:prod',request_key='publish1',actor='producer:test')
        self.assertFalse(again['may_publish'])
        self.assertNotIn('receipt_token', again)
        self.now += 601
        self.assertEqual(publication.inspect(claim['id'],actor='producer:test')['state'], 'unknown')
        with self.assertRaises(ValueError):
            publication.finish(claim['id'], token=claim['receipt_token'], evidence='too late',actor='producer:test')
        with self.assertRaises(ValueError):
            publication.begin(job['id'], target='site:prod',request_key='different-key',actor='producer:test')

    def test_publication_commits_only_with_exact_producer_and_token(self):
        job = self.enqueue()
        attempt = self.queue.reserve(laptop())
        self.queue.finish(attempt['id'],1,report={'ok':True})
        pub = ComputePublications(self.queue)
        claim = pub.begin(job['id'],target='site:prod',request_key='publish',actor='producer:test')
        with self.assertRaises(ValueError):
            pub.finish(claim['id'],token='wrong'*8,evidence='verified',actor='producer:test')
        result = pub.finish(claim['id'],token=claim['receipt_token'],evidence='activation digest verified',actor='producer:test')
        self.assertEqual(result['state'],'completed')
        self.assertNotIn('token_hash',result)

    def engine(self, worker):
        sampler = Mock()
        sampler.sample.side_effect = lambda **kw: {**healthy(self.now), **kw}
        engine = ComputeEngine(self.path, enabled=True,sampler=sampler,worker=worker,clock=lambda:self.now)
        engine.laptop_sampler = Mock()
        engine.laptop_sampler.sample.side_effect = lambda **kw:laptop(self.now)
        return engine

    def test_lost_start_ack_is_inspected_never_started_twice(self):
        job = self.enqueue()
        worker = Mock()
        expected = {}
        def call(action, **payload):
            if action == 'stage': return {'complete': True, 'offset': len(DATA)}
            if action == 'start':
                expected.update({k:v for k,v in payload.items() if k!='manifest'})
                raise WorkerUnavailable('lost response')
            return {'binding': expected, 'state':'running', 'termination_confirmed':False}
        worker.call.side_effect = call
        engine = self.engine(worker)
        engine.tick()
        engine.tick()
        self.assertEqual(sum(c.args[0]=='start' for c in worker.call.call_args_list),1)
        self.assertEqual(self.queue.inspect(job['id'])['state'],'running')
        self.now += 121
        worker.call.side_effect = WorkerUnavailable('offline')
        engine.tick()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'unknown')
        self.assertEqual(len(self.queue.status()['reservations']),1)

    def test_remote_binding_mismatch_keeps_reservation(self):
        job = self.enqueue()
        worker = Mock()
        worker.call.side_effect=lambda action,**payload: ({'complete':True,'offset':len(DATA)} if action=='stage'
            else {'binding':{},'state':'succeeded','termination_confirmed':True})
        self.engine(worker).tick()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'unknown')
        self.assertEqual(len(self.queue.status()['reservations']),1)

    def test_remote_output_is_fetched_and_verified_before_completion(self):
        job = self.enqueue()
        worker = Mock()
        expected = {}
        output = b'build output bytes'
        output_sha = hashlib.sha256(output).hexdigest()
        def call(action, **payload):
            if action=='stage': return {'complete':True,'offset':len(DATA)}
            if action=='start':
                expected.update({k:v for k,v in payload.items() if k!='manifest'})
                return {'binding':expected,'state':'running','termination_confirmed':False}
            if action=='heartbeat': return {'binding':expected,'state':'running','termination_confirmed':False}
            if action=='inspect': return {'binding':expected,'state':'succeeded','termination_confirmed':True,
                'result':{'artifacts':[{'sha256':output_sha,'size':len(output)}]}}
            if action=='fetch': return {'artifact_id':output_sha,'offset':0,'size':len(output),
                'data':base64.b64encode(output).decode(),'eof':True}
        worker.call.side_effect = call
        engine = self.engine(worker)
        engine.tick()
        engine.tick()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'completed')
        self.assertTrue(self.artifacts.has({'sha256':output_sha,'size':len(output)}))


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = ComputeEngine(Path(self.temp.name))
        self.token='test-credential-only-'+'x'*32
        self.policy={'test':{'enabled':True,'source_cidrs':['127.0.0.1/32'],
            'token_sha256':hashlib.sha256(self.token.encode()).hexdigest(),
            'kinds':['agc.build'],'request_prefix':'test:','max_priority':10,'publish_targets':['site:prod']}}
        self.factory=Mock(return_value=self.engine)
        self.app=FastAPI()
        self.app.include_router(producer_routes(self.factory,config_loader=lambda:self.policy))
        self.client=TestClient(self.app,client=('127.0.0.1',5000))
        self.headers={'X-Compute-Producer':'test','Authorization':'Bearer '+self.token}

    def test_transport_scope_agent_and_browser_guards_precede_engine(self):
        for headers in ({}, {**self.headers,'Authorization':'Bearer '+'x'*40},
                        {**self.headers,'Origin':'https://console.test'},
                        {**self.headers,'X-Agent-Console-Session':'session'}):
            self.assertEqual(self.client.get('/api/compute/producers/jobs/test',headers=headers).status_code,403)
        remote=TestClient(self.app,client=('192.168.1.71',5000))
        self.policy['test']['source_cidrs'].append('192.168.1.71/32')
        self.assertEqual(remote.get('/api/compute/producers/jobs/test',headers=self.headers).status_code,403)
        self.factory.assert_not_called()

    def test_producer_can_upload_and_enqueue_only_its_scoped_work(self):
        upload=self.client.post('/api/compute/producers/artifacts/'+SHA,headers=self.headers,
            json={'size':len(DATA),'offset':0,'data':base64.b64encode(DATA).decode(),'final':True})
        self.assertEqual(upload.status_code,200,upload.text)
        body={'kind':'agc.build','execution_target':'agc-laptop','source_digest':SHA,
              'input_digest':'b'*64,'request_key':'test:first','manifest':manifest()}
        response=self.client.post('/api/compute/producers/jobs',headers=self.headers,json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['actor'],'producer:test')
        for changes in ({'kind':'tv.build'},{'priority':11},{'request_key':'other:one'}):
            self.assertEqual(self.client.post('/api/compute/producers/jobs',headers=self.headers,json={**body,**changes}).status_code,403)
        other=self.engine.queue.enqueue(kind='maintenance.report',execution_target='n100',source_digest='a'*64,
            input_digest='b'*64,request_key='private',actor='owner')
        self.assertEqual(self.client.get('/api/compute/producers/jobs/'+other['id'],headers=self.headers).status_code,403)


    def test_producer_can_fetch_archived_unknown_evidence_but_not_reconcile_or_publish(self):
        queue = self.engine.queue
        queue.set_hold(held=False, actor='owner')
        job = queue.enqueue(kind='bushi.run', execution_target='agc-laptop', source_digest=SHA,
            input_digest='b'*64, request_key='test:unknown', actor='producer:test',
            manifest=manifest('bushi.run', production_writes=True))
        attempt = queue.reserve(laptop(queue.clock()))
        expected = binding(job, attempt)
        self.engine.artifacts.put(SHA, size=len(DATA), offset=0,
            data=base64.b64encode(DATA).decode(), final=True)
        queue.mark_external_unknown(attempt['id'], 1, terminated=True)
        route = '/api/compute/producers/jobs/' + job['id'] + '/artifacts/' + SHA
        self.assertEqual(self.client.get(route, headers=self.headers).status_code, 400)
        queue.mark_external_unknown(attempt['id'], 1, terminated=True,
            report={'binding': expected, 'artifacts': [{'sha256': SHA, 'size': len(DATA)}]})
        response = self.client.get(route, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(base64.b64decode(response.json()['data']), DATA)
        self.assertEqual(self.client.post('/api/compute/producers/jobs/'+job['id']+'/publication',
            headers=self.headers, json={'target':'site:prod','request_key':'test:pub'}).status_code, 400)
        self.assertEqual(self.client.post('/api/compute/producers/attempts/'+attempt['id']+'/reconcile-external',
            headers=self.headers, json={'generation':1,'outcome':'applied','evidence':'not authority'}).status_code, 404)


if __name__=='__main__':
    unittest.main()


class InventoryRetentionTests(unittest.TestCase):
    setUp = RemoteTests.setUp
    enqueue = RemoteTests.enqueue
    engine = RemoteTests.engine

    def test_inventory_binds_and_enumerates_every_output_object(self):
        job = self.enqueue()
        attempt = self.queue.reserve(laptop())
        expected = binding(self.queue.inspect(job['id']),attempt)
        value = {'version':1,'binding':expected,'files':[{'path':'build/app.js','sha256':SHA,'size':len(DATA)}]}
        raw = json.dumps(value).encode()
        sha = hashlib.sha256(raw).hexdigest()
        self.artifacts.put(sha,size=len(raw),offset=0,data=base64.b64encode(raw).decode(),final=True)
        result={'binding':expected,'inventory_artifact':{'sha256':sha,'size':len(raw)},
                'artifacts':[{'sha256':sha,'size':len(raw)}]}
        engine=self.engine(Mock())
        self.assertEqual(engine.result_artifact_ids(result),{sha,SHA})
        with self.assertRaises(ValueError):
            engine.result_artifact_ids({**result,'binding':{**expected,'lease_generation':2}})

    def test_gc_requires_ack_preserves_shared_sources_and_recent_uploads(self):
        from agent_console.compute_retention import ComputeRetention
        engine=self.engine(Mock())
        retention=ComputeRetention(engine)
        first=self.enqueue(priority=1)
        second=self.enqueue('unpublished')
        attempt=self.queue.reserve(laptop())
        output=b'accepted archive'
        output_sha=hashlib.sha256(output).hexdigest()
        self.artifacts.put(output_sha,size=len(output),offset=0,data=base64.b64encode(output).decode(),final=True)
        result={'binding':binding(self.queue.inspect(first['id']),attempt),
                'artifacts':[{'sha256':output_sha,'size':len(output)}]}
        self.queue.finish(attempt['id'],1,report=result)
        pub=ComputePublications(self.queue)
        with self.assertRaises(ValueError):
            retention.consume(first['id'],actor='producer:test',evidence='not published')
        claim=pub.begin(first['id'],target='prod',request_key='publish',actor='producer:test')
        pub.finish(claim['id'],token=claim['receipt_token'],evidence='target digest verified',actor='producer:test')
        # A transfer pin is released only after this job's publication completes.
        retention.pin_upload(output_sha,'attempt:'+attempt['id'])
        retired=retention.consume(first['id'],actor='producer:test',evidence='target keeps release and rollback')
        self.assertEqual(retired['retired_objects'],1)
        self.assertTrue(self.artifacts.has({'sha256':SHA,'size':len(DATA)}))
        self.assertFalse(self.artifacts.has({'sha256':output_sha,'size':len(output)}))
        self.assertEqual(self.queue.inspect(second['id'])['state'],'queued')
        self.assertEqual(pub.inspect(claim['id'],actor='producer:test')['state'],'completed')


class LaptopSamplerTests(unittest.TestCase):
    def test_small_clock_skew_never_shortens_local_warmup(self):
        from agent_console.compute_sampler import LaptopSampler
        worker = Mock()
        current = NOW
        future = 5
        def status(*args, **kwargs):
            return {'worker_id':'agc-laptop','boot_id':'same-boot',
                'telemetry':laptop(current + future), 'capabilities':['agc.build'],
                'admission':{'ready':True}}
        worker.call.side_effect = status
        sampler = LaptopSampler(worker)
        for elapsed in range(0, 300, 30):
            current = NOW + elapsed
            result = sampler.sample(enabled=True, operator_hold=False, now=current)
            self.assertTrue(result['online'])
            self.assertEqual(result['sampled_at'], current)
            self.assertEqual(result['readiness'], 'warming_up')
        current = NOW + 300
        self.assertEqual(sampler.sample(enabled=True, operator_hold=False, now=current)['readiness'], 'ready')
        future = 5.001
        self.assertFalse(sampler.sample(enabled=True, operator_hold=False, now=current)['online'])
        future = 3
        self.assertEqual(sampler.sample(enabled=True, operator_hold=False, now=current)['readiness'], 'warming_up')
        future = -30.001
        self.assertFalse(sampler.sample(enabled=True, operator_hold=False, now=current)['online'])
        self.assertIsNone(admission('agc.build', laptop(NOW, sampled_at=NOW+5), [], NOW))
        self.assertEqual(admission('agc.build', laptop(NOW, sampled_at=NOW+5.001), [], NOW), 'telemetry_from_future')
        self.assertEqual(admission('agc.build', laptop(NOW, sampled_at=NOW+5, healthy_since=NOW-295), [], NOW), 'warming_up')

    def test_central_warmup_resets_after_offline_stale_boot_or_pressure(self):
        from agent_console.compute_sampler import LaptopSampler
        now=NOW
        worker=Mock()
        data=laptop(now)
        worker.call.return_value={'worker_id':'agc-laptop','boot_id':'boot1',
                                  'telemetry':data,'capabilities':data['capabilities'],'admission':{'ready':True}}
        sampler=LaptopSampler(worker)
        first=sampler.sample(enabled=True,operator_hold=False,now=now)
        self.assertEqual(first['readiness'],'warming_up')
        for offset in range(15,301,15):
            data['sampled_at']=now+offset
            last=sampler.sample(enabled=True,operator_hold=False,now=now+offset)
        self.assertEqual(last['readiness'],'ready')
        worker.call.side_effect=WorkerUnavailable('offline')
        self.assertFalse(sampler.sample(enabled=True,operator_hold=False,now=now+315)['online'])
        worker.call.side_effect=None
        data['sampled_at']=now+330
        again=sampler.sample(enabled=True,operator_hold=False,now=now+330)
        self.assertEqual(again['readiness'],'warming_up')
        data['sampled_at']=now+345
        data['host_cpu_percent']=95
        self.assertEqual(sampler.sample(enabled=True,operator_hold=False,now=now+345)['readiness'],'staff_cpu_priority')
        data['sampled_at']=now-40
        self.assertFalse(sampler.sample(enabled=True,operator_hold=False,now=now+360)['online'])


class RemoteFencingTests(unittest.TestCase):
    setUp = RemoteTests.setUp
    enqueue = RemoteTests.enqueue
    engine = RemoteTests.engine

    def test_monday_does_not_probe_start_or_ack_worker(self):
        from datetime import datetime
        self.now=datetime.fromisoformat('2026-10-19T12:00:00+08:00').timestamp()
        job=self.enqueue()
        worker=Mock()
        self.engine(worker).tick()
        worker.call.assert_not_called()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'blocked')

    def test_late_oom_receipt_never_retries_unchanged_budget(self):
        job=self.enqueue()
        attempt=self.queue.reserve(laptop())
        self.now+=121
        self.queue.expire()
        self.queue.reconcile_terminated(attempt['id'],evidence='worker cgroup empty, OOM kill recorded',actor='dispatcher',failure='oom')
        self.now+=400
        with self.assertRaises(ValueError):
            self.queue.retry(job['id'],actor='owner')

    def test_archive_ack_is_durable_and_independent_from_result_acceptance(self):
        job=self.enqueue()
        attempt=self.queue.reserve(laptop())
        expected=binding(self.queue.inspect(job['id']),attempt)
        report={'binding':expected,'inventory_artifact':{'sha256':SHA,'size':len(DATA)},
                'artifacts':[{'sha256':SHA,'size':len(DATA)}]}
        self.queue.finish(attempt['id'],1,report=report)
        self.assertEqual(len(self.queue.pending_remote_acks()),1)
        worker=Mock()
        worker.call.side_effect=WorkerUnavailable('ack response lost')
        engine=self.engine(worker)
        engine._acknowledge_archived()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'completed')
        self.assertEqual(len(self.queue.pending_remote_acks()),1)
        worker.call.side_effect=None
        worker.call.return_value={'binding':expected,'acknowledged':True}
        engine._acknowledge_archived()
        self.assertEqual(self.queue.pending_remote_acks(),[])

    def test_owner_reconciles_unknown_publication_with_external_evidence(self):
        job=self.enqueue()
        attempt=self.queue.reserve(laptop())
        self.queue.finish(attempt['id'],1,report={'ok':True})
        publication=ComputePublications(self.queue)
        claim=publication.begin(job['id'],target='site:prod',request_key='publish',actor='producer:test')
        self.now+=601
        result=publication.reconcile(claim['id'],outcome='completed',evidence='verified running release digest externally',actor='owner')
        self.assertEqual(result['state'],'completed')
        self.assertFalse(publication.begin(job['id'],target='site:prod',request_key='publish',actor='producer:test')['may_publish'])


class RPCBoundsTests(unittest.TestCase):
    def worker(self, program):
        import sys
        from agent_console.compute_transport import SSHWorker
        worker=SSHWorker({'enabled':True,'host':'100.111.183.86','user':'agc-worker',
                          'identity_file':'/nonexistent-test-key','known_hosts_file':'/nonexistent-test-hosts'})
        worker.command=(sys.executable,'-c',program)
        return worker

    def test_json_roundtrip_uses_stdin_and_fixed_protocol(self):
        worker=self.worker('import json,sys; r=json.load(sys.stdin); print(json.dumps({"protocol_version":1,"ok":True,"action":r["action"]}))')
        self.assertEqual(worker.call('status')['action'],'status')
        with self.assertRaises(ValueError):
            worker.call('shell',command='forbidden')

    def test_oversized_or_non_json_response_is_bounded_and_redacted(self):
        for program in ('import sys; sys.stdin.read(); print("x"*600000)',
                        'import sys; sys.stdin.read(); print("secret-error-detail")'):
            with self.assertRaisesRegex(WorkerUnavailable,'worker_transport_unavailable'):
                self.worker(program).call('status')


class OrganizerFenceTests(unittest.TestCase):
    setUp = RemoteTests.setUp
    enqueue = RemoteTests.enqueue

    def test_cross_host_named_reservation_survives_unknown_until_termination(self):
        from dataclasses import replace
        from agent_console import compute_policy, compute_queue
        self.assertFalse(compute_policy.PROFILES['bushi.emergency'].enabled)
        job=self.enqueue(kind='bushi.run')
        laptop_attempt=self.queue.reserve(laptop())
        self.assertEqual(self.queue.status()['resource_reservations'],[
            {'resource':'bushi-organizer','attempt_id':laptop_attempt['id']}])
        emergency=self.queue.enqueue(kind='bushi.emergency',execution_target='n100',
            source_digest='c'*64,input_digest='d'*64,request_key='emergency',actor='owner')
        future_profiles=dict(compute_policy.PROFILES)
        future_profiles['bushi.emergency']=replace(future_profiles['bushi.emergency'],enabled=True)
        telemetry={**healthy(self.now),'guest_id':'lxc-102','guest_available_bytes':2*GiB}
        # Simulate later qualified fallback policy without enabling any adapter
        # in production; this exercises the cross-host SQL reservation itself.
        with patch.object(compute_policy,'PROFILES',future_profiles), patch.object(compute_queue,'PROFILES',future_profiles):
            self.assertIsNone(self.queue.reserve(telemetry))
            self.assertEqual(self.queue.inspect(emergency['id'])['reason'],'shared_resource_reserved')
            self.now+=121
            self.queue.expire()
            self.assertEqual(len(self.queue.status()['resource_reservations']),1)
            self.queue.reconcile_terminated(laptop_attempt['id'],evidence='exact worker cgroup confirmed empty',actor='owner')
            telemetry.update(sampled_at=self.now,healthy_since=self.now-300)
            attempt=self.queue.reserve(telemetry)
            self.assertEqual(attempt['job_id'],emergency['id'])
            self.queue.finish(attempt['id'],attempt['generation'],failure='adapter_failed')
            self.assertEqual(self.queue.status()['resource_reservations'],[])


class ExternalOutcomeFenceTests(unittest.TestCase):
    setUp = RemoteTests.setUp
    engine = RemoteTests.engine

    def enqueue_write(self):
        return self.queue.enqueue(kind='bushi.run', execution_target='agc-laptop', source_digest=SHA,
            input_digest='b'*64, request_key='bushi:write', actor='producer:test',
            manifest=manifest('bushi.run', production_writes=True))

    def worker(self, *, terminal='unknown'):
        worker = Mock()
        expected, objects = {}, {}
        self.allow_fetch = False
        def call(action, **payload):
            if action == 'stage':
                return {'complete': True, 'offset': len(DATA)}
            if action == 'start':
                expected.update({key: value for key, value in payload.items() if key != 'manifest'})
                return {'binding': expected, 'state': 'running', 'termination_confirmed': False}
            if action == 'heartbeat':
                return {'binding': expected, 'state': 'running', 'termination_confirmed': False}
            if action == 'inspect':
                output = json.dumps({'ok': False, 'errorType': 'write_unknown', 'worker_binding': expected}).encode()
                output_sha = hashlib.sha256(output).hexdigest()
                inventory = json.dumps({'version': 1, 'binding': expected,
                    'files': [{'path': 'response.json', 'sha256': output_sha, 'size': len(output)}]}).encode()
                inventory_sha = hashlib.sha256(inventory).hexdigest()
                objects.update({output_sha: output, inventory_sha: inventory})
                description = {'sha256': inventory_sha, 'size': len(inventory)}
                return {'binding': expected, 'state': terminal, 'termination_confirmed': True,
                    'reason': 'external_write_unknown' if terminal == 'unknown' else 'adapter_failed',
                    'result': {'inventory_artifact': description, 'artifacts': [description]}}
            if action == 'fetch':
                if not self.allow_fetch:
                    raise WorkerUnavailable('lost artifact receipt')
                data = objects[payload['artifact_id']]
                return {'artifact_id': payload['artifact_id'], 'offset': 0, 'size': len(data),
                        'data': base64.b64encode(data).decode(), 'eof': True}
            raise AssertionError('unknown results must never be acknowledged')
        worker.call.side_effect = call
        return worker, objects

    def test_external_unknown_keeps_cross_host_fence_and_evidence_across_restart(self):
        job = self.enqueue_write()
        worker, objects = self.worker()
        engine = self.engine(worker)
        engine.tick()  # start once
        engine.tick()  # terminal unknown, transfer fault
        attempt = self.queue.inspect(job['id'])['attempts'][0]
        self.assertEqual(attempt['external_write_unknown'], 1)
        self.assertIsNotNone(attempt['termination_confirmed_at'])
        self.assertIsNone(attempt['result_json'])
        self.assertEqual(self.queue.status()['resource_reservations'][0]['resource'], 'bushi-organizer')
        self.assertEqual(len(self.queue.status()['reservations']), 1)
        with self.assertRaisesRegex(ValueError, 'business outcome'):
            self.queue.reconcile_terminated(attempt['id'], evidence='process definitely ended', actor='owner')
        self.now += 121
        self.allow_fetch = True
        restarted = self.engine(worker)
        restarted.tick()
        result = self.queue.inspect(job['id'])
        self.assertEqual(result['state'], 'unknown')
        report = json.loads(result['attempts'][0]['result_json'])
        self.assertTrue(report['external_write_unknown'])
        self.assertEqual(restarted.result_artifact_ids(report), set(objects))
        for sha, data in objects.items():
            self.assertTrue(restarted.artifacts.has({'sha256': sha, 'size': len(data)}))
        self.assertEqual(self.queue.pending_remote_acks(), [])
        # Further protocol trouble cannot downgrade the durable business fence.
        self.queue.mark_unknown(attempt['id'], reason='remote_protocol_invalid')
        self.assertEqual(self.queue.inspect(job['id'])['attempts'][0]['reason'], 'external_write_unknown')
        with self.assertRaises(ValueError):
            self.queue.reconcile_external(attempt['id'], 2, outcome='applied', evidence='wrong generation', actor='owner')
        self.queue.reconcile_external(attempt['id'], 1, outcome='applied',
            evidence='Owner independently read exact event and player status10 from organizer', actor='owner')
        self.assertEqual(self.queue.status()['resource_reservations'], [])
        self.assertEqual(self.queue.status()['reservations'], [])
        self.now += 400
        with self.assertRaises(ValueError):
            self.queue.retry(job['id'], actor='owner')
        self.assertEqual(self.queue.pending_remote_acks(), [])
        self.assertEqual(sum(call.args[0] == 'start' for call in worker.call.call_args_list), 1)

    def test_lease_loss_and_late_success_cannot_release_external_authority(self):
        job = self.enqueue_write()
        worker, _ = self.worker(terminal='succeeded')
        engine = self.engine(worker)
        engine.tick()
        self.now += 121
        self.allow_fetch = True
        engine.tick()
        result = self.queue.inspect(job['id'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['attempts'][0]['external_write_unknown'], 1)
        self.assertEqual(len(self.queue.status()['resource_reservations']), 1)
        self.assertEqual(self.queue.pending_remote_acks(), [])

    def test_failed_write_and_termination_only_reconciliation_remain_fenced(self):
        job = self.enqueue_write()
        worker, _ = self.worker(terminal='failed')
        engine = self.engine(worker)
        engine.tick()
        self.allow_fetch = True
        engine.tick()
        attempt = self.queue.inspect(job['id'])['attempts'][0]
        self.assertEqual(attempt['external_write_unknown'], 1)
        with self.assertRaises(ValueError):
            self.queue.reconcile_terminated(attempt['id'], evidence='cgroup empty', actor='owner', write_not_started=True)
        self.assertEqual(len(self.queue.status()['resource_reservations']), 1)

    def test_business_readback_cannot_release_a_still_running_process(self):
        job = self.enqueue_write()
        attempt = self.queue.reserve(laptop())
        self.queue.claim_remote_start(attempt['id'], 1)
        self.queue.mark_external_unknown(attempt['id'], 1)
        with self.assertRaisesRegex(ValueError, 'process termination'):
            self.queue.reconcile_external(attempt['id'], 1, outcome='not_applied', evidence='external readback', actor='owner')
        self.assertEqual(len(self.queue.status()['resource_reservations']), 1)
