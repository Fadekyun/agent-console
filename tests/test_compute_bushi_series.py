import base64
from copy import deepcopy
import gzip
import hashlib
import io
import json
import tarfile
import unittest
from unittest.mock import patch

from agent_console.compute_bushi_series import request_payload, request_digest
from agent_console.compute_transport import binding
from agent_console.workflow_store import canonical
import test_compute_remote as remote
from test_compute_remote import manifest, laptop, NOW, SHA


class SeriesClaimTests(unittest.TestCase):
    setUp = remote.RemoteTests.setUp

    def store(self, raw):
        description={'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
        for offset in range(0,len(raw),262144):
            self.artifacts.put(description['sha256'],size=len(raw),offset=offset,
                data=base64.b64encode(raw[offset:offset+262144]).decode(),final=offset+262144>=len(raw))
        return description

    def archive(self,payload, *, compressed=True, name='request.json', second=False, link=False):
        out=io.BytesIO()
        raw=payload if isinstance(payload,bytes) else canonical(payload).encode()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600
            if link: info.type=tarfile.SYMTYPE;info.linkname='/etc/shadow';info.size=0
            archive.addfile(info,io.BytesIO(raw))
            if second: archive.addfile(tarfile.TarInfo('extra.txt'),io.BytesIO())
        result=out.getvalue()
        return self.store(gzip.compress(result,mtime=0) if compressed else result)

    def payload(self, action='modify_criteria'):
        return {'version':1,'action':action,'series_id':'14522',
                'request':{'expect':'Reviewed existing event','criteria':'参加費 $18'}}

    def make_manifest(self,payload, *, execute=False, description=None):
        value=manifest('bushi.run',production_writes=execute)
        value.update(parameters={'action':payload['action'],'seriesId':payload['series_id'],
                                 'mode':'execute' if execute else 'dry-run'},
                     inputs_artifact=description or self.archive(payload))
        value['input_sha256']=value['inputs_artifact']['sha256']
        if execute:
            value['dry_run_job_id']=payload['dry_run']['job_id']
            value['business_key']=('agc:bushi-series-modify:'+payload['series_id']+':'+request_digest(payload)
                if payload['action']=='modify_criteria' else 'agc:bushi-series-apply:'+payload['series_id'])
        return value

    def enqueue(self,m,key,actor='producer:series'):
        return self.queue.enqueue(kind='bushi.run',execution_target='agc-laptop',source_digest=SHA,
            input_digest=m['input_sha256'],manifest=m,request_key=key,actor=actor,
            priority=90 if m['production_writes'] else 100)

    def accepted_dry(self,payload=None, *, actor='producer:series', key='dry', mutation=None):
        payload=payload or self.payload()
        m=self.make_manifest(payload)
        job=self.enqueue(m,key,actor)
        attempt=self.queue.reserve(laptop(self.now));self.assertEqual(attempt['job_id'],job['id'])
        expected=binding(job,attempt)
        semantic={'criteria':payload['request'].get('criteria','same'),'formats':['Standard'],'capacities':[16]}
        result={'action':payload['action'],'series_id':payload['series_id'],'mode':'dry-run','ready':True,
                'verified':False,'request_sha256':request_digest(payload),
                'semantic_sha256':hashlib.sha256(canonical(semantic).encode()).hexdigest(),'semantic':semantic}
        response={'ok':True,'worker_binding':expected,'result':result}
        if mutation:mutation(response)
        body=self.store(canonical(response).encode())
        inventory=self.store(canonical({'version':1,'binding':expected,
            'files':[{'path':'response.json',**body}]}).encode())
        self.queue.finish(attempt['id'],1,report={'binding':expected,'artifacts':[inventory],'inventory_artifact':inventory})
        proof={'job_id':job['id'],'source_sha':m['source_sha'],'manifest_sha256':job['manifest_digest'],
               'input_sha256':job['input_digest'],'request_sha256':result['request_sha256'],
               'semantic_sha256':result['semantic_sha256'],'semantic':semantic,'ready':True,
               'action':payload['action'],'series_id':payload['series_id']}
        return {**payload,'dry_run':proof},job,inventory

    def test_exact_accepted_dry_proof_creates_one_durable_modification_claim(self):
        payload,_,_=self.accepted_dry()
        value=self.make_manifest(payload,execute=True)
        job=self.enqueue(value,'execute')
        self.assertEqual(self.enqueue(value,'execute')['id'],job['id'])
        with self.assertRaisesRegex(ValueError,'business action'):
            self.enqueue(value,'different-key')
        with self.queue.store.connect() as db:
            claim=db.execute('SELECT * FROM compute_business_claims').fetchone()
            self.assertEqual(claim['business_key'],value['business_key'])
            self.assertEqual(claim['job_id'],job['id'])

    def test_changed_request_or_free_declared_hash_cannot_borrow_proof(self):
        payload,_,_=self.accepted_dry()
        changed=deepcopy(payload);changed['request']['criteria']='Unreviewed new terms'
        with self.assertRaisesRegex(ValueError,'exact request'):
            self.enqueue(self.make_manifest(changed,execute=True),'changed')
        value=self.make_manifest(payload,execute=True)
        value['business_key']='agc:bushi-series-modify:14522:'+'f'*64
        with self.assertRaisesRegex(ValueError,'business identity differs'):
            self.enqueue(value,'declared-digest')
        self.assertNotIn('queued',self.queue.status()['counts'])

    def test_cross_producer_and_source_proof_reuse_is_rejected(self):
        payload,_,_=self.accepted_dry()
        value=self.make_manifest(payload,execute=True)
        with self.assertRaises(PermissionError):self.enqueue(value,'other','producer:other')
        value['source_sha']='c'*40
        with self.assertRaisesRegex(ValueError,'source or action'):
            self.enqueue(value,'changed-source')

    def test_proof_semantics_must_equal_accepted_receipt_and_not_just_hash_itself(self):
        payload,_,_=self.accepted_dry()
        changed=deepcopy(payload)
        changed['dry_run']['semantic']={'criteria':'forged'}
        changed['dry_run']['semantic_sha256']=hashlib.sha256(canonical(changed['dry_run']['semantic']).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,'differs from accepted'):
            self.enqueue(self.make_manifest(changed,execute=True),'forged')
        changed=deepcopy(payload);changed['dry_run']['ready']=1
        with self.assertRaisesRegex(ValueError,'differs from accepted'):
            self.enqueue(self.make_manifest(changed,execute=True),'coerced')

    def test_failed_domain_response_and_bound_digest_corruption_never_authorize_write(self):
        payload,_,_=self.accepted_dry(mutation=lambda response:response.update(ok=False))
        with self.assertRaisesRegex(ValueError,'not successful'):
            self.enqueue(self.make_manifest(payload,execute=True),'failed-proof')
        self.assertEqual(self.queue.status()['counts'],{'completed':1})

    def test_inventory_and_worker_binding_are_independently_checked(self):
        payload,job,inventory=self.accepted_dry()
        with self.queue.store.connect(write=True) as db:
            result=json.loads(db.execute('SELECT result_json FROM compute_attempts WHERE job_id=?',(job['id'],)).fetchone()[0])
            result['binding']['lease_generation']=99
            db.execute('UPDATE compute_attempts SET result_json=? WHERE job_id=?',(canonical(result),job['id']))
        with self.assertRaisesRegex(ValueError,'authenticated inventory'):
            self.enqueue(self.make_manifest(payload,execute=True),'wrong-generation')

    def test_pending_or_unknown_dry_run_never_authorizes_execute(self):
        payload,job,_=self.accepted_dry()
        with self.queue.store.connect(write=True) as db:
            db.execute("UPDATE compute_jobs SET state='unknown' WHERE id=?",(job['id'],))
        with self.assertRaisesRegex(ValueError,'durably completed'):
            self.enqueue(self.make_manifest(payload,execute=True),'unknown-proof')

    def test_apply_manual_and_routine_share_same_series_claim_across_producers(self):
        routine,_,_=self.accepted_dry(self.payload('apply_series'),actor='producer:routine',key='routine-dry')
        self.enqueue(self.make_manifest(routine,execute=True),'routine-execute','producer:routine')
        manual,_,_=self.accepted_dry(self.payload('apply_manual'),actor='producer:manual',key='manual-dry')
        with self.assertRaisesRegex(ValueError,'business action'):
            self.enqueue(self.make_manifest(manual,execute=True),'manual-execute','producer:manual')

    def test_strict_tar_bounds_paths_duplicates_and_compression_bombs(self):
        payload=self.payload()
        for kwargs in ({'name':'../request.json'},{'second':True},{'link':True}):
            value=self.make_manifest(payload,description=self.archive(payload,**kwargs))
            with self.assertRaises(ValueError):self.enqueue(value,'invalid')
        raw=b'{"version":1,"version":1,"action":"modify_criteria","series_id":"14522","request":{}}'
        with self.assertRaisesRegex(ValueError,'duplicate JSON'):
            self.enqueue(self.make_manifest(payload,description=self.archive(raw)),'duplicate-json')
        compressed=self.store(gzip.compress(b'\x00'*(1024*1024),mtime=0))
        with self.assertRaisesRegex(ValueError,'expansion'):
            request_payload(self.artifacts,self.make_manifest(payload,description=compressed))
        plain=self.make_manifest(payload,description=self.archive(payload,compressed=False))
        self.assertEqual(request_payload(self.artifacts,plain),payload)

    def test_series_aliases_and_dry_run_authority_are_forbidden(self):
        value=self.make_manifest(self.payload())
        value['parameters']['seriesId']='014522'
        with self.assertRaises(ValueError):self.enqueue(value,'alias')
        value=self.make_manifest(self.payload());value['business_key']='agc:bushi-series-apply:14522'
        with self.assertRaises(ValueError):self.enqueue(value,'dry-authority')
        value=manifest('bushi.run');value['dry_run_job_id']='compute-other'
        with self.assertRaises(ValueError):self.enqueue(value,'unknown-action-proof')

    def test_consumed_or_corrupt_artifacts_fail_closed_without_claim(self):
        payload,job,inventory=self.accepted_dry()
        (self.artifacts.root/inventory['sha256']).write_bytes(b'x'*inventory['size'])
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            self.enqueue(self.make_manifest(payload,execute=True),'corrupt')
        with self.queue.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM compute_business_claims').fetchone()[0],0)


class SeriesProducerScopeTests(unittest.TestCase):
    setUp=remote.ProducerTests.setUp

    def test_existing_paid_scope_does_not_gain_series_write_authority(self):
        self.policy['test'].update(kinds=['bushi.run'],max_priority=100,allow_production_writes=True,
                                  business_key_namespaces=['agc:paid-participation'])
        m=manifest('bushi.run',production_writes=True)
        m.update(parameters={'action':'apply_series','seriesId':'14522','mode':'execute'},
                 inputs_artifact={'sha256':'c'*64,'size':1},input_sha256='c'*64,
                 business_key='agc:bushi-series-apply:14522',dry_run_job_id='compute-dry')
        body={'kind':'bushi.run','execution_target':'agc-laptop','source_digest':SHA,
              'input_digest':'c'*64,'request_key':'test:apply','manifest':m}
        response=self.client.post('/api/compute/producers/jobs',headers=self.headers,json=body)
        self.assertEqual(response.status_code,403,response.text)
        self.factory.assert_not_called()
