"""Emergency qualification: immutable paid claims, capacity gates and fixed RPC."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import base64
import json
import sys
import unittest
from unittest.mock import Mock, patch

from agent_console.compute_engine import ComputeEngine
from agent_console.compute_policy import admission, emergency_runtime_hold, GiB
from agent_console.compute_queue import ComputeQueue
from agent_console.compute_sampler import EmergencySampler, EMERGENCY_HOST_PROBE_COMMAND
from agent_console.compute_transport import EmergencySSHWorker, SSHWorker, WorkerUnavailable
from test_compute_queue import healthy
import test_compute_remote as remote
from test_compute_remote import manifest, laptop, NOW, SHA, DATA

ORDER = '12345678-1234-4234-8234-123456789abc'


def emergency_manifest(now=NOW, **changes):
    value = manifest('bushi.emergency', production_writes=True)
    value.update(parameters={'action':'participate','orderId':ORDER},
        business_key='agc:paid-participation:'+ORDER,
        emergency={'urgent':True,'deadline':int(now+900)})
    value.update(changes)
    return value


def emergency_telemetry(now=NOW, **changes):
    value = {**healthy(now), 'guest_id':'lxc-102','policy_verified':True,'worker_ready':True,
        'capabilities':['bushi.emergency'],'guest_memory_limit_bytes':2*GiB,
        'installed_source_sha':'a'*40,'installed_source_artifact':{'sha256':SHA,'size':len(DATA)}}
    value.update(changes)
    return value


class EmergencyQueueTests(unittest.TestCase):
    setUp = remote.RemoteTests.setUp

    def enqueue(self, key='urgent', *, kind='bushi.emergency', m=None, **changes):
        value = emergency_manifest(self.now) if m is None else deepcopy(m)
        if kind == 'bushi.run':
            value['kind'] = kind
            value.pop('emergency', None)
        args = dict(kind=kind, execution_target='n100' if kind=='bushi.emergency' else 'agc-laptop',
            source_digest=SHA,input_digest='b'*64,request_key=key,actor='producer:test',
            manifest=value,priority=90,deadline=int(self.now+900))
        args.update(changes)
        return self.queue.enqueue(**args)

    def test_explicit_urgent_deadline_and_identity_are_required(self):
        variants = [dict(emergency=None),dict(production_writes=False),dict(parameters={}),
                    dict(business_key='unapproved:action'),dict(parameters=[]),dict(inputs_artifact={'sha256':'b'*64,'size':1})]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.enqueue(m=emergency_manifest(self.now, **changes))
        for deadline in (self.now-1,self.now,self.now+1201):
            with self.assertRaises(ValueError):
                self.enqueue(deadline=deadline,m=emergency_manifest(self.now,emergency={'urgent':True,'deadline':int(deadline)}))
        with self.assertRaises(ValueError): self.enqueue(priority=89)
        with self.assertRaises(ValueError): self.enqueue(deadline=self.now+800)
        with self.assertRaises(ValueError): self.enqueue(manifest=None)

    def test_same_request_is_idempotent_after_deadline_but_cannot_change_target(self):
        saved = emergency_manifest(self.now)
        job = self.enqueue(m=saved)
        old_deadline = int(job['deadline'])
        self.now += 1000
        self.assertEqual(self.enqueue(m=saved,deadline=old_deadline)['id'],job['id'])
        with self.assertRaisesRegex(ValueError,'different immutable'):
            self.enqueue(kind='bushi.run',m=saved,deadline=old_deadline)
        self.assertIsNone(self.queue.reserve(emergency_telemetry(self.now)))
        self.assertEqual(self.queue.inspect(job['id'])['reason'],'deadline_passed_requires_review')

    def test_global_claim_survives_restart_cancellation_and_different_keys(self):
        original = self.enqueue('laptop-paid', kind='bushi.run')
        self.queue.cancel(original['id'],actor='owner')
        self.queue = ComputeQueue(self.path,clock=lambda:self.now)
        for actor in ('producer:test','producer:other','owner'):
            with self.assertRaisesRegex(ValueError,'business action'):
                self.enqueue('emergency-new-key',actor=actor)

    def test_business_claim_derived_even_when_manifest_omits_optional_field(self):
        value=emergency_manifest(self.now)
        value.pop('business_key')
        self.enqueue('derived',kind='bushi.run',m=value)
        with self.assertRaisesRegex(ValueError,'business action'): self.enqueue()

    def test_atomic_claim_across_concurrent_producers_and_targets(self):
        def create(kind):
            try:
                return self.enqueue(kind,kind=kind)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(create,['bushi.run','bushi.emergency']))
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(len(self.queue.status()['jobs']),1)

    def test_schema3_backfill_preserves_paid_claim(self):
        original=self.enqueue('existing',kind='bushi.run')
        with self.queue.store.connect(write=True) as db:
            db.execute('DELETE FROM compute_business_claims')
            db.execute('UPDATE compute_schema SET version=3')
        self.queue=ComputeQueue(self.path,clock=lambda:self.now)
        with self.assertRaisesRegex(ValueError,'business action'): self.enqueue()
        self.assertEqual(self.queue.inspect(original['id'])['state'],'queued')

    def test_schema3_conflicting_historical_claims_abort_migration_atomically(self):
        first=self.enqueue('legacy-one',kind='bushi.run')
        other=emergency_manifest(self.now)
        other['parameters']['orderId']='22345678-1234-4234-8234-123456789abc'
        other['business_key']='agc:paid-participation:'+other['parameters']['orderId']
        second=self.enqueue('legacy-two',kind='bushi.run',m=other)
        with self.queue.store.connect(write=True) as db:
            db.execute('DELETE FROM compute_business_claims')
            db.execute('UPDATE compute_schema SET version=3')
            db.execute('UPDATE compute_jobs SET manifest_json=? WHERE id=?',(first['manifest_json'],second['id']))
        with self.assertRaisesRegex(ValueError,'historical paid business claims conflict'):
            ComputeQueue(self.path,clock=lambda:self.now)
        with self.queue.store.connect() as db:
            self.assertEqual(db.execute('SELECT version FROM compute_schema').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM compute_business_claims').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM compute_jobs').fetchone()[0],2)

    def test_source_archive_and_commit_are_both_qualified(self):
        job=self.enqueue()
        for changes in ({'installed_source_sha':'c'*40},
                        {'installed_source_artifact':{'sha256':'c'*64,'size':len(DATA)}}):
            self.assertIsNone(self.queue.reserve(emergency_telemetry(self.now,**changes)))
            self.assertEqual(self.queue.inspect(job['id'])['reason'],'emergency_source_not_qualified')
        self.assertIsNotNone(self.queue.reserve(emergency_telemetry(self.now)))

    def test_maintenance_sampler_never_dispatches_emergency_locally(self):
        self.enqueue()
        self.assertIsNone(self.queue.reserve(healthy(self.now)))
        self.assertEqual(self.queue.status()['reservations'],[])

    def test_external_write_unknown_fences_other_host_even_after_process_ends(self):
        self.enqueue('laptop',kind='bushi.run')
        attempt=self.queue.reserve(laptop(self.now))
        self.queue.mark_external_unknown(attempt['id'],1,terminated=True)
        other=emergency_manifest(self.now)
        other['parameters']['orderId']='22345678-1234-4234-8234-123456789abc'
        other['business_key']='agc:paid-participation:'+other['parameters']['orderId']
        job=self.enqueue(m=other)
        self.assertIsNone(self.queue.reserve(emergency_telemetry(self.now)))
        self.assertEqual(self.queue.inspect(job['id'])['reason'],'shared_resource_reserved')


class EmergencyPolicyTests(unittest.TestCase):
    def test_capacity_and_policy_limits_are_fail_closed(self):
        self.assertIsNone(admission('bushi.emergency',emergency_telemetry(),[],NOW))
        cases=[({'policy_verified':False},'emergency_policy_unverified'),
            ({'capabilities':None},'adapter_unavailable'),
            ({'worker_ready':False},'worker_not_ready'),
            ({'guest_memory_limit_bytes':3*GiB},'emergency_guest_ceiling_unverified'),
            ({'host_available_bytes':4*GiB-1},'host_memory_headroom'),
            ({'guest_available_bytes':1.5*GiB-1},'guest_memory_reserve'),
            ({'guest_disk_free_bytes':6.5*GiB-1},'guest_disk_reserve'),
            ({'memory_full_avg60':1},'memory_pressure'),
            ({'io_some_avg60':10},'io_pressure'),
            ({'thinpool_data_percent':80},'thinpool_data_pressure'),
            ({'thinpool_metadata_percent':70},'thinpool_metadata_pressure'),
            ({'healthy_since':NOW-299},'warming_up')]
        for changes,reason in cases:
            with self.subTest(changes=changes):
                self.assertEqual(admission('bushi.emergency',emergency_telemetry(**changes),[],NOW),reason)

    def test_running_floor_ignores_busy_readiness_but_obeys_stale_pressure(self):
        value=emergency_telemetry(worker_ready=False,host_available_bytes=2*GiB,
                                  guest_available_bytes=GiB//2)
        self.assertFalse(emergency_runtime_hold(value,NOW))
        for changes in ({'host_available_bytes':2*GiB-1},{'guest_available_bytes':GiB//2-1},
                        {'sampled_at':NOW-31},{'online':False},{'memory_full_avg60':1},
                        {'io_some_avg60':10},{'thinpool_data_percent':80}):
            self.assertTrue(emergency_runtime_hold({**value,**changes},NOW))


class EmergencySamplerTests(unittest.TestCase):
    def setUp(self):
        self.now=NOW
        self.worker=Mock()
        self.raw=emergency_telemetry()
        self.host={key:self.raw[key] for key in ('host_cpu_count','host_memory_total_bytes',
            'host_available_bytes','memory_full_avg60','io_some_avg60','thinpool_data_percent','thinpool_metadata_percent')}
        self.host['guest_limit_bytes']=2*GiB
        self.host.update(guest_current_bytes=0, guest_cgroup_path='/lxc/102',
                         guest_namespace_root='/lxc/102/ns', boundary_observed_at=NOW)
        self.boot='boot-1'
        self.worker.call.side_effect=self.call
        self.transport=Mock(side_effect=lambda *_:json.dumps(self.host))
        self.sampler=EmergencySampler(self.worker,transport=self.transport,resource_snapshot='')

    def call(self,action,**payload):
        if action=='host-sample': return {'accepted':True}
        return {'worker_id':'bushi-emergency','boot_id':self.boot,'telemetry':self.raw,
                'admission':{'ready':True},'capabilities':['bushi.emergency']}

    def sample(self,delta=0):
        self.now=NOW+delta
        self.raw['sampled_at']=self.now
        self.host['boundary_observed_at']=self.now
        return self.sampler.sample(enabled=True,operator_hold=False,now=self.now)

    def test_fixed_probe_then_host_sample_then_status_warms_for_five_minutes(self):
        for at in range(0,301,15): result=self.sample(at)
        self.assertEqual(result['readiness'],'ready')
        self.assertEqual(result['healthy_since'],NOW)
        self.assertEqual(self.transport.call_args.args[0],EMERGENCY_HOST_PROBE_COMMAND)
        self.assertIn('vm-102-disk-0',EMERGENCY_HOST_PROBE_COMMAND[-1])
        self.assertNotIn('vm-115-disk-0',EMERGENCY_HOST_PROBE_COMMAND[-1])
        self.assertEqual([c.args[0] for c in self.worker.call.call_args_list[:2]],['host-sample','status'])
        sample=self.worker.call.call_args_list[0].kwargs['sample']
        self.assertEqual(sample['physical_host'],'n100')
        self.assertEqual(sample['ct102_memory_limit_bytes'],2*GiB)
        self.assertEqual(sample['ct102_memory_current_bytes'],0)
        self.assertEqual(sample['ct102_cgroup_path'],'/lxc/102')
        self.assertEqual(sample['ct102_namespace_root'],'/lxc/102/ns')
        self.assertEqual(self.sample(331)['readiness'],'warming_up')
        self.boot='boot-2'
        self.assertEqual(self.sample(340)['healthy_since'],NOW+340)

    def test_host_and_guest_cap_mismatch_and_future_samples_fail_closed(self):
        for changes in ({'guest_memory_limit_bytes':GiB}, {'guest_available_bytes':3*GiB},
                        {'installed_source_sha':'not-a-commit'}):
            old=self.raw.copy(); self.raw.update(changes)
            self.assertFalse(self.sample()['online']); self.raw=old
        self.raw['sampled_at']=NOW+5
        self.assertTrue(self.sampler.sample(enabled=True,operator_hold=False,now=NOW)['online'])
        self.raw['sampled_at']=NOW+6
        self.assertFalse(self.sampler.sample(enabled=True,operator_hold=False,now=NOW)['online'])

    def test_snapshot_veto_never_sends_trusted_host_sample(self):
        with patch('agent_console.compute_sampler.host_snapshot_gate',return_value='host_maintenance'):
            self.assertEqual(self.sample()['readiness'],'host_maintenance')
        self.worker.call.assert_not_called()
        self.transport.assert_not_called()

    def test_actual_outer_usage_and_mapping_cannot_be_bypassed_by_guest_reply(self):
        for update in ({'guest_cgroup_path':'/lxc/115'}, {'guest_namespace_root':'/lxc/102'},
                       {'guest_current_bytes':True}, {'guest_current_bytes':2*GiB}):
            with self.subTest(update=update):
                original=self.host.copy();self.host.update(update)
                self.assertFalse(self.sample()['online']);self.host=original

    def test_disabled_policy_never_probes_and_host_errors_are_redacted(self):
        self.sampler.sample(enabled=False,operator_hold=False,now=NOW)
        self.worker.call.assert_not_called()
        self.host['host_available_bytes']='SECRET invalid'
        value=self.sample()
        self.assertFalse(value['online'])
        self.assertNotIn('SECRET',json.dumps(value))
        self.worker.call.assert_not_called()


class EmergencyEngineTests(unittest.TestCase):
    setUp = remote.RemoteTests.setUp
    enqueue = EmergencyQueueTests.enqueue
    def engine(self):
        self.worker=Mock()
        self.expected={}
        def call(action,**payload):
            if action=='stage': return {'complete':True,'offset':len(DATA)}
            if action=='start': self.expected.update({k:v for k,v in payload.items() if k!='manifest'})
            return {'binding':self.expected,'state':'running','termination_confirmed':False}
        self.worker.call.side_effect=call
        sampler=Mock()
        sampler.sample.side_effect=lambda **kw:{**healthy(self.now),**kw}
        self.laptop_worker=Mock()
        engine=ComputeEngine(self.path,enabled=True,sampler=sampler,worker=self.laptop_worker,
            emergency_worker=self.worker,clock=lambda:self.now)
        engine.emergency_sampler=Mock()
        engine.emergency_sampler.sample.side_effect=lambda **kw:emergency_telemetry(self.now)
        return engine

    def test_tuesday_emergency_starts_without_contacting_laptop_or_local_execution(self):
        self.now=1792461600.0 # Tuesday 2026-10-20 SGT
        job=self.enqueue()
        engine=self.engine()
        with patch('agent_console.compute_engine.maintenance_report') as local:
            engine.tick()
        local.assert_not_called()
        self.laptop_worker.call.assert_not_called()
        self.assertEqual(sum(c.args[0]=='start' for c in self.worker.call.call_args_list),1)
        self.assertEqual(self.queue.inspect(job['id'])['state'],'running')
        engine.tick()
        self.assertEqual(sum(c.args[0]=='start' for c in self.worker.call.call_args_list),1)

    def test_runtime_headroom_or_deadline_causes_stop_without_replay(self):
        self.now=1792461600.0
        self.enqueue()
        engine=self.engine(); engine.tick()
        engine.emergency_sampler.sample.side_effect=lambda **kw:emergency_telemetry(self.now,host_available_bytes=GiB)
        engine.tick()
        self.assertIn('stop',[c.args[0] for c in self.worker.call.call_args_list])
        self.assertEqual(sum(c.args[0]=='start' for c in self.worker.call.call_args_list),1)
        engine.emergency_sampler.sample.side_effect=lambda **kw:emergency_telemetry(self.now)
        self.now+=875
        # Keep central lease current to isolate deadline behavior from expiry.
        with self.queue.store.connect(write=True) as db:
            db.execute('UPDATE compute_attempts SET lease_until=?',(self.now+60,))
        engine.tick()
        self.assertEqual(sum(c.args[0]=='stop' for c in self.worker.call.call_args_list),2)

    def test_deadline_during_staging_never_sends_start(self):
        self.now=1792461600.0
        job=self.enqueue(); engine=self.engine()
        attempt=self.queue.reserve(emergency_telemetry(self.now))
        self.now+=875
        with self.queue.store.connect(write=True) as db:
            db.execute('UPDATE compute_attempts SET lease_until=?',(self.now+60,))
        engine.tick()
        self.worker.call.assert_not_called()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'failed')
        self.assertEqual(self.queue.status()['resource_reservations'],[])
        with self.assertRaisesRegex(ValueError,'business action'):
            self.enqueue('fresh-key-after-safe-failure')

    def test_lost_start_receipt_never_resends_after_reopen(self):
        self.now=1792461600.0
        job=self.enqueue(); engine=self.engine()
        original=self.worker.call.side_effect
        def lost(action,**payload):
            result=original(action,**payload)
            if action=='start': raise WorkerUnavailable('lost response')
            return result
        self.worker.call.side_effect=lost
        engine.tick(); engine.tick()
        self.assertEqual(sum(c.args[0]=='start' for c in self.worker.call.call_args_list),1)
        self.assertEqual(self.queue.inspect(job['id'])['state'],'running')
        self.now+=121
        self.worker.call.side_effect=WorkerUnavailable('offline')
        engine.tick()
        self.assertEqual(self.queue.inspect(job['id'])['state'],'unknown')
        self.assertEqual(len(self.queue.status()['resource_reservations']),1)


class EmergencyProducerTests(unittest.TestCase):
    setUp=remote.ProducerTests.setUp

    def test_emergency_needs_explicit_producer_authority_and_business_namespace(self):
        self.policy['test'].update(kinds=['bushi.emergency'],max_priority=100,allow_production_writes=True)
        self.engine.queue.clock=lambda:NOW
        self.engine.artifacts.put(SHA,size=len(DATA),offset=0,data=base64.b64encode(DATA).decode(),final=True)
        body={'kind':'bushi.emergency','execution_target':'n100','source_digest':SHA,'input_digest':'b'*64,
              'request_key':'test:urgent','manifest':emergency_manifest(),'priority':90,'deadline':int(NOW+900)}
        self.assertEqual(self.client.post('/api/compute/producers/jobs',headers=self.headers,json=body).status_code,403)
        self.policy['test']['allow_emergency']=True
        self.assertEqual(self.client.post('/api/compute/producers/jobs',headers=self.headers,json=body).status_code,403)
        self.policy['test']['business_key_namespaces']=['agc:paid-participation']
        response=self.client.post('/api/compute/producers/jobs',headers=self.headers,json=body)
        self.assertEqual(response.status_code,200,response.text)
        body['request_key']='test:another'
        self.assertEqual(self.client.post('/api/compute/producers/jobs',headers=self.headers,json=body).status_code,400)


class EmergencyTransportTests(unittest.TestCase):
    def test_fixed_endpoint_and_separate_sample_capability(self):
        config={'enabled':True,'host':'192.168.1.67','user':'bushi-compute',
                'identity_file':'/etc/emergency/key','known_hosts_file':'/etc/emergency/hosts'}
        worker=EmergencySSHWorker(config)
        self.assertEqual(worker.command[-2:],('bushi-compute@192.168.1.67','agc-emergency rpc'))
        with self.assertRaises(ValueError): EmergencySSHWorker({**config,'user':'root'})
        with self.assertRaises(ValueError): EmergencySSHWorker({**config,'host':'other'})
        self.assertNotIn('host-sample',SSHWorker.actions)
        worker.command=(sys.executable,'-c','import json,sys;r=json.load(sys.stdin);print(json.dumps({"protocol_version":1,"ok":True,"action":r["action"]}))')
        self.assertEqual(worker.call('host-sample',sample={})['action'],'host-sample')
