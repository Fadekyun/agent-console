import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.workflow_release import ReleaseService


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.repo=self.base/'repo';self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'change.txt').write_text('reviewed candidate\n')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','candidate'],check=True)
        self.sha=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        self.manager=SessionManager(Settings(workspace_root=self.repo,state_dir=self.base/'state',database_path=self.base/'state/main.sqlite3',config_dir=self.base/'config',profile_dir=self.base/'profiles',handoff_dir=self.base/'handoffs',worktree_root=self.repo/'trees',tmux_socket='unused-release-tests'))
        self.service=ReleaseService(self.manager);self.session=self.add_session('source');self.reviewer=self.add_session('reviewer')
        self.adapter=self.base/'adapter';self.control=self.base/'control.json';self.control.write_text('{}')
        self.external=self.base/'external.json';self.calls=self.base/'calls'
        self.adapter.write_text('''#!/usr/bin/env python3
import hashlib,json,sys,time
from pathlib import Path
request=json.loads(Path(sys.argv[-1]).read_text());control=json.loads(Path('''+repr(str(self.control))+''').read_text())
external=Path('''+repr(str(self.external))+''');calls=Path('''+repr(str(self.calls))+''')
if request['mode']=='apply':
 if control.get('slow'):time.sleep(control['slow'])
 if not control.get('fail_before'):
  assert hashlib.sha256(Path(request['snapshot_path']).read_bytes()).hexdigest()==request['artifact_sha256']
  with calls.open('a') as f:f.write(request['operation_id']+'\\n')
  external.write_text(json.dumps({'sha':request['candidate_sha'],'action':request['action'],'target':request['target']}))
 sys.exit(1 if control.get('lost_ack') or control.get('fail_before') else 0)
outcome='applied' if external.exists() and json.loads(external.read_text())['sha']==request['candidate_sha'] else 'not-applied'
if control.get('unknown'):outcome='unknown'
Path(request['response_path']).write_text(json.dumps({'outcome':outcome,'candidate_sha':'0'*40 if control.get('wrong_sha') else request['candidate_sha'],'summary':'Observed test target','external_reference':'fixture://target'}))
''');self.adapter.chmod(0o700)
        self.target={'id':'staging-fixture','label':'Staging fixture','actions':['deploy'],'apply':[str(self.adapter)],'probe':[str(self.adapter)],'timeout_seconds':5,'environment':[]}
        self.write_config()
        self.candidate=self.publish(self.session,commit=True)

    def tearDown(self):
        # Wait only for fixtures this test owns; never affect other sessions.
        with self.service.store.connect() as db:attempts=[dict(r) for r in db.execute('SELECT * FROM release_attempts')]
        for attempt in attempts:
            deadline=time.monotonic()+8
            while self.service.busy(attempt) and time.monotonic()<deadline:time.sleep(.05)
        self.temp.cleanup()

    def add_session(self,name):
        session={'id':'sess-'+name,'tmux_name':name,'repository':str(self.repo),'managed':True}
        with self.manager.database.connect() as db:
            db.execute("INSERT INTO sessions(id,tmux_name,tool,profile,repository,created_at,last_activity,status,managed) VALUES(?,?,'shell','general',?,'2026-10-01','2026-10-01','process-exited',1)",(session['id'],name,str(self.repo)))
        return session

    def write_config(self):
        self.service.config_path.write_text(json.dumps({'version':1,'targets':[self.target]}));self.service.config_path.chmod(0o600)

    def publish(self,session,commit=False,checks=None,outcome='pass'):
        return self.service.store.publish(session,kind='final',outcome=outcome,summary='Verified candidate '+str(time.time_ns()),checks=['git checks passed'] if checks is None else checks,
            artifacts=[{'kind':'commit','sha':self.sha}] if commit else [],request_key=str(time.time_ns()),actor='test',workspace=self.repo)

    def request(self,**changes):return {'candidate_result_id':self.candidate['id'],'evidence_result_ids':[self.candidate['id']],'action':'deploy','target':self.target['id'],**changes}
    def grant(self,**changes):
        request=self.request(**changes);view=self.service.preview(**request)
        return self.service.authorize(**request,expected_hash=view['hash'],request_key='grant-'+str(time.time_ns()),actor='operator')
    def start(self,grant,mode='apply',key=None):return self.service.start(grant['id'],mode=mode,request_key=key or str(time.time_ns()),actor='operator')
    def settled(self,attempt):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            current=self.service.attempt(attempt['id'])
            if current['state'] not in {'queued','running'} and not self.service.busy(current):return current
            time.sleep(.05)
        self.fail('release worker did not settle: '+repr(current))

    def test_single_session_checks_can_authorize_exact_candidate_and_observe_real_action(self):
        grant=self.grant();self.assertFalse(self.external.exists())
        first=self.start(grant,key='apply-one');second=self.start(grant,key='apply-one')
        self.assertEqual(first['id'],second['id']);done=self.settled(first)
        self.assertEqual(done['state'],'applied',done)
        self.assertEqual(json.loads(self.external.read_text()),{'sha':self.sha,'action':'deploy','target':self.target['id']})
        self.assertEqual(len(self.calls.read_text().splitlines()),1)
        with self.assertRaisesRegex(ValueError,'already applied'):self.start(grant)

    def test_lost_apply_ack_uses_external_probe_instead_of_repeating(self):
        self.control.write_text('{"lost_ack":true}')
        done=self.settled(self.start(self.grant()))
        self.assertEqual(done['state'],'applied');self.assertEqual(len(self.calls.read_text().splitlines()),1)

    def test_unknown_requires_probe_before_retry_and_blocks_other_grants(self):
        self.control.write_text('{"unknown":true}');grant=self.grant()
        self.assertEqual(self.settled(self.start(grant))['state'],'unknown')
        from agent_console.workbench_state import WorkbenchState
        state=WorkbenchState(self.manager)
        with patch.object(self.manager,'tool_catalog',return_value=[{'name':'shell','status':'ready'}]):
            node=next(n for n in state.snapshot()['nodes'] if n['id']==self.session['id'])
        self.assertTrue(node['needs_attention']);self.assertEqual(node['release']['state'],'unknown')
        self.assertEqual(node['result_state'],'completed')
        self.assertTrue(any(e['action']=='release.observed' for e in state.history(self.session['id'])['events']))
        with self.assertRaisesRegex(ValueError,'checked before'):self.start(grant)
        with self.assertRaisesRegex(ValueError,'unresolved'):self.start(self.grant())
        self.control.write_text('{}')
        self.assertEqual(self.settled(self.start(grant,'probe'))['state'],'applied')
        self.assertEqual(len(self.calls.read_text().splitlines()),1)

    def test_verified_not_applied_allows_explicit_retry(self):
        self.control.write_text('{"fail_before":true}');grant=self.grant()
        self.assertEqual(self.settled(self.start(grant))['state'],'not-applied')
        self.control.write_text('{}')
        self.assertEqual(self.settled(self.start(grant))['state'],'applied')
        self.assertEqual(len(self.calls.read_text().splitlines()),1)

    def test_pending_worker_survives_service_recreation_and_target_is_serialized(self):
        self.control.write_text('{"slow":1}');grant=self.grant();attempt=self.start(grant,key='stable')
        self.service=ReleaseService(self.manager)
        self.assertEqual(self.start(grant,key='stable')['id'],attempt['id'])
        with self.assertRaisesRegex(ValueError,'active adapter'):self.start(self.grant())
        self.assertEqual(self.settled(attempt)['state'],'applied');self.assertEqual(len(self.calls.read_text().splitlines()),1)

    def test_unacknowledged_worker_creation_cannot_be_retried_without_observation(self):
        grant=self.grant()
        with patch.object(self.service,'_spawn'):
            self.start(grant)
        self.assertEqual(self.service.inspect(grant['id'])['attempts'][-1]['state'],'unknown')
        with self.assertRaisesRegex(ValueError,'checked before'):self.start(grant)
        self.assertEqual(self.settled(self.start(grant,'probe'))['state'],'not-applied')
        self.assertEqual(self.settled(self.start(grant))['state'],'applied')

    def test_evidence_must_consume_exact_candidate_before_publication(self):
        early=self.publish(self.reviewer)
        item=self.service.store.send(self.candidate['id'],self.reviewer['id'],note='',request_key='review-input',actor='test')
        for state in ['delivered','consumed']:self.service.store.acknowledge(item['id'],self.reviewer['id'],state=state,actor='test')
        with self.assertRaisesRegex(ValueError,'before publication'):self.grant(evidence_result_ids=[early['id']])
        review=self.publish(self.reviewer);grant=self.grant(evidence_result_ids=[review['id']])
        self.assertEqual(grant['preview']['evidence'][0]['session_id'],self.reviewer['id'])
        self.publish(self.reviewer,outcome='fail')
        with self.assertRaisesRegex(ValueError,'superseded'):self.start(grant)
        self.assertFalse(self.calls.exists())

    def test_candidate_drift_and_adapter_drift_invalidate_authority(self):
        grant=self.grant();self.adapter.write_text(self.adapter.read_text()+'\n# Updated adapter\n')
        with self.assertRaisesRegex(ValueError,'changed'):self.start(grant)
        grant=self.grant();self.publish(self.session,commit=True)
        with self.assertRaisesRegex(ValueError,'superseded'):self.start(grant)
        self.assertFalse(self.calls.exists())

    def test_unknown_target_action_and_missing_checks_are_blocked(self):
        for changes in [{'target':'production'},{'action':'merge'}]:
            with self.assertRaises(ValueError):self.grant(**changes)
        self.candidate=self.publish(self.session,commit=True,checks=[])
        with self.assertRaisesRegex(ValueError,'actual checks'):self.grant()
        self.assertFalse(self.calls.exists())

    def test_wrong_candidate_observation_is_unknown(self):
        self.control.write_text('{"wrong_sha":true}');grant=self.grant()
        self.assertEqual(self.settled(self.start(grant))['state'],'unknown')
        with self.assertRaisesRegex(ValueError,'checked before'):self.start(grant)

    def test_target_permission_and_secret_reference_validation(self):
        self.service.config_path.chmod(0o666)
        with self.assertRaisesRegex(ValueError,'owner-controlled'):self.service.catalog()
        self.write_config();self.target['environment']=['AGENT_CONSOLE_EVIDENCE_CAPABILITY'];self.write_config()
        with self.assertRaisesRegex(ValueError,'environment reference'):self.service.catalog()

    def test_authorization_request_key_deduplicates_but_cannot_change_target(self):
        request=self.request();view=self.service.preview(**request)
        first=self.service.authorize(**request,expected_hash=view['hash'],request_key='review-key',actor='operator')
        self.assertEqual(first['id'],self.service.authorize(**request,expected_hash=view['hash'],request_key='review-key',actor='operator')['id'])
        with self.assertRaisesRegex(ValueError,'different release work'):
            self.service.authorize(**self.request(target='different'),expected_hash=view['hash'],request_key='review-key',actor='operator')

    def test_release_api_requires_operator_and_runs_only_after_explicit_authorization(self):
        from fastapi.testclient import TestClient
        import agent_console.web as web
        from agent_console.web import create_app
        settings=patch.multiple(web,EXPECTED_LOGIN='test@example.com',TRUSTED_HOSTS=['testserver'])
        settings.start();self.addCleanup(settings.stop)
        client=TestClient(create_app(self.manager), client=("127.0.0.1", 50000));headers={'Tailscale-User-Login':'test@example.com'}
        self.assertEqual(client.get('/api/workflow/release-targets').status_code,403)
        self.assertEqual(client.post('/api/workflow/releases/authorize',json={}).status_code,403)
        targets=client.get('/api/workflow/release-targets',headers=headers).json()
        self.assertNotIn(str(self.adapter),json.dumps(targets))
        request=self.request()
        response=client.post('/api/workflow/releases/preview',json=request,headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        payload={**request,'expected_hash':response.json()['hash'],'request_key':'api-grant'}
        grant=client.post('/api/workflow/releases/authorize',json=payload,headers=headers).json()
        self.assertFalse(self.calls.exists())
        endpoint='/api/workflow/releases/'+grant['id']+'/attempts'
        self.assertEqual(client.post(endpoint,json={'mode':'apply','request_key':'api-run'}).status_code,403)
        response=client.post(endpoint,json={'mode':'apply','request_key':'api-run'},headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.settled(response.json())['state'],'applied')

    def test_timeout_remains_unknown_and_external_probe_controls_retry(self):
        self.target['timeout_seconds']=1;self.write_config();self.control.write_text('{"slow":3}')
        grant=self.grant();done=self.settled(self.start(grant))
        self.assertEqual(done['state'],'unknown')
        with self.assertRaisesRegex(ValueError,'checked before'):self.start(grant)
        self.control.write_text('{}')
        self.assertEqual(self.settled(self.start(grant,'probe'))['state'],'not-applied')

    def test_orphan_adapter_blocks_retry_until_observed_after_worker_loss(self):
        import signal
        from agent_console.task_runner import _active_group_members
        self.control.write_text('{"slow":1.5}');grant=self.grant();attempt=self.start(grant)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            members=_active_group_members(attempt['pid'])
            if members and len(members)>1:break
            time.sleep(.02)
        self.assertGreater(len(members),1)
        os.kill(attempt['pid'],signal.SIGKILL)
        with self.assertRaisesRegex(ValueError,'active adapter'):self.start(grant,'probe')
        deadline=time.monotonic()+5
        while self.service.busy(attempt) and time.monotonic()<deadline:time.sleep(.05)
        self.assertFalse(self.service.busy(attempt))
        self.assertEqual(self.service.inspect(grant['id'])['attempts'][-1]['state'],'unknown')
        self.assertEqual(self.settled(self.start(grant,'probe'))['state'],'applied')
        self.assertEqual(len(self.calls.read_text().splitlines()),1)

    def test_later_failed_prerequisite_stales_release_check_evidence(self):
        upstream=self.add_session('upstream');first=self.publish(upstream)
        self.service.graph.attach(upstream['id'],self.session['id'],purpose='Candidate input',dependencies=[{'source_id':upstream['id'],'readiness':'after-final'}],expected_version=0,actor='test')
        graph=self.service.graph.inspect(self.session['id']);ready=graph['readiness'][self.session['id']]
        self.service.graph.deliver(self.session['id'],expected_version=graph['version'],expected_signature=ready['signature'],actor='test')
        for item in self.service.store.inbox(self.session['id'])['items']:
            for state in ['delivered','consumed']:self.service.store.acknowledge(item['id'],self.session['id'],state=state,actor='test')
        self.candidate=self.publish(self.session,commit=True);grant=self.grant()
        self.publish(upstream,outcome='fail')
        with self.assertRaisesRegex(ValueError,'stale or failed'):self.start(grant)
        self.assertFalse(self.calls.exists())

    def test_workflow_pause_blocks_release_and_stop_retains_reconciliation(self):
        from agent_console.workflow_engine import WorkflowEngine
        engine=WorkflowEngine(self.manager);grant=self.grant()
        engine.control(self.session['id'],state='paused',actor='test')
        with self.assertRaisesRegex(ValueError,'paused'):self.start(grant)
        engine.control(self.session['id'],state='running',actor='test');self.control.write_text('{"slow":2}')
        attempt=self.start(grant)
        deadline=time.monotonic()+5
        while self.service.attempt(attempt['id'])['state']=='queued' and time.monotonic()<deadline:time.sleep(.02)
        engine.control(self.session['id'],state='stopped',actor='test')
        done=self.settled(attempt);self.assertEqual(done['state'],'unknown')
        self.control.write_text('{}')
        self.assertEqual(self.settled(self.start(grant,'probe'))['state'],'not-applied')
        with self.assertRaisesRegex(ValueError,'stopped'):self.start(grant)
