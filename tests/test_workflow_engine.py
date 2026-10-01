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
from agent_console.workflow_engine import WorkflowEngine,DEFAULT_POLICY
from agent_console.providers import TOOL_BINARIES


@unittest.skipUnless(subprocess.run(['sh','-c','command -v tmux'],capture_output=True).returncode==0,'tmux required')
class WorkflowEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.workspace=self.base/'workspace';self.workspace.mkdir()
        profiles=self.base/'profiles';profiles.mkdir()
        for role in ['general','coder','planner','reviewer']:(profiles/(role+'.md')).write_text('# '+role)
        self.settings=Settings(workspace_root=self.workspace,state_dir=self.base/'state',database_path=self.base/'state/main.sqlite3',profile_dir=profiles,handoff_dir=self.base/'handoffs',worktree_root=self.workspace/'trees',tmux_socket='workflow-test-'+str(os.getpid())+'-'+str(id(self)),max_managed_sessions=4,max_children_per_parent=2)
        self.manager=SessionManager(self.settings);(self.manager.auth.codex_home('default')/'auth.json').write_text('{}')
        self.fake=self.base/'fake-codex';self.calls=self.base/'calls'
        self.fake.write_text('''#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
if '--help' in sys.argv:print('--output-schema --output-last-message --sandbox');sys.exit(0)
if '--version' in sys.argv:print('fixture-codex 1');sys.exit(0)
prompt=sys.stdin.read()
with Path('''+repr(str(self.calls))+''').open('a') as f:f.write('started\\n')
inputs=json.loads(prompt.split('BEGIN UNTRUSTED INPUT DATA\\n')[1].split('\\nEND UNTRUSTED INPUT DATA')[0])
if 'slow-native' in prompt:time.sleep(2)
else:time.sleep(.1)
if 'fail-native' in prompt:sys.exit(2)
files=[]
if 'write-artifact' in prompt:Path('result.txt').write_text('Actual isolated worktree output');files=['result.txt']
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'outcome':'pass','summary':'Fixture native task completed','checks':['Fixture check passed'],'files':files,'commit':'','consumed_inputs':[i['input_id'] for i in inputs],'suggestions':[]}))
''');self.fake.chmod(0o700)
        self.patch=patch.dict(TOOL_BINARIES,{'codex':self.fake});self.patch.start()
        self.root=self.manager.create(tool='shell',profile='general',name='workflow-root')
        self.engine=WorkflowEngine(self.manager)

    def tearDown(self):
        try:self.engine.control(self.root['id'],state='stopped',actor='test');self.engine.tick()
        finally:
            subprocess.run(['tmux','-L',self.settings.tmux_socket,'kill-server'],capture_output=True)
            self.patch.stop();self.temp.cleanup()

    def propose(self,task='Do one useful task',key='one',dependencies=None,**config):
        return self.engine.propose(self.root['id'],task=task,reason='A separate focused check is useful',expected_output='A verified result',config={'tool':'codex','profile':'general','worktree':False,**config},dependencies=dependencies or [],request_key=key,actor='test')

    def accept(self,step):
        preview=self.engine.preview(step['root_id'],step['config'])
        return self.engine.decide(step['id'],decision='accepted',expected_version=step['version'],preview_hash=preview['hash'],actor='test')

    def finish(self,step_id):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            self.engine.tick();step=self.engine.step(step_id)
            if step['attempts'] and step['attempts'][-1]['state'] in {'completed','failed','unknown'}:return step
            time.sleep(.1)
        step=self.engine.step(step_id)
        output=self.manager.tmux.capture(step['attempts'][-1]['name'])[0] if step['attempts'] else ''
        self.fail('attempt did not settle: '+json.dumps(step)+'\n'+output[-4000:])

    def test_suggestions_do_not_launch_until_reviewed_and_retries_deduplicate(self):
        step=self.propose();self.assertEqual(self.propose()['id'],step['id']);self.engine.tick();self.assertFalse(self.calls.exists())
        self.accept(step);done=self.finish(step['id']);self.assertEqual(done['attempts'][-1]['state'],'completed',done)
        self.engine.tick();self.assertEqual(self.calls.read_text().splitlines(),['started'])
        native=done['attempts'][0]['session_id'];self.assertEqual(self.engine.store.results(native)[0]['outcome'],'pass')

    def test_after_final_waits_then_passes_exact_input_to_native_task(self):
        step=self.propose(dependencies=[{'source_id':self.root['id'],'readiness':'after-final'}]);self.accept(step);self.engine.tick();self.assertFalse(self.calls.exists())
        self.publish('ready');self.engine.tick();self.assertFalse(self.calls.exists())
        self.publish('final');done=self.finish(step['id']);self.assertEqual(done['attempts'][0]['state'],'completed',done)
        inbox=self.engine.store.inbox(done['attempts'][0]['session_id'])['items'];self.assertEqual(inbox[0]['state'],'consumed')
        self.assertEqual(inbox[0]['result']['kind'],'final')
        self.assertFalse(self.engine.graph.inspect(step['id'])['readiness'][step['id']]['stale'])

    def publish(self,kind='final',summary='Exact candidate'):
        return self.engine.svc.publish(self.root['id'],{'kind':kind,'outcome':'pass','summary':summary,'checks':[],'artifacts':[],'request_key':str(time.time_ns())},'test')

    def test_pause_stops_new_dispatch_and_stop_cancels_pending(self):
        step=self.propose();self.accept(step);self.engine.control(self.root['id'],state='paused',actor='test');self.engine.tick();self.assertFalse(self.calls.exists())
        self.engine.control(self.root['id'],state='running',actor='test');self.assertEqual(self.finish(step['id'])['attempts'][0]['state'],'completed')
        self.engine.control(self.root['id'],state='stopped',actor='test')
        with self.assertRaisesRegex(ValueError,'cannot be resumed'):self.engine.control(self.root['id'],state='running',actor='test')

    def test_auto_envelope_accepts_only_in_scope_and_records_review(self):
        policy={**DEFAULT_POLICY,'mode':'auto','repositories':[str(self.workspace)],'actions':['read'],'roles':['planner'],'harnesses':['codex']}
        self.engine.configure(self.root['id'],policy=policy,expected_version=0,actor='test')
        good=self.propose(profile='planner',action='read');self.assertEqual(good['decision'],'accepted',good)
        outside=self.propose(key='outside',profile='general',action='write');self.assertEqual(outside['decision'],'proposed');self.assertIn('outside',outside['error'])
        self.assertEqual(self.engine.policy(self.root['id'])['version'],1)

    def test_unsupported_harness_fails_preview_before_creation(self):
        step=self.propose(tool='shell')
        with self.assertRaisesRegex(ValueError,'no verified native'):self.accept(step)
        self.assertEqual(self.engine.step(step['id'])['attempts'],[])

    def test_changed_inputs_coalesce_until_current_attempt_finishes(self):
        self.publish();step=self.propose(task='slow-native',dependencies=[{'source_id':self.root['id'],'readiness':'after-final'}]);self.accept(step)
        self.engine.tick()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not self.calls.exists():time.sleep(.05)
        self.assertTrue(self.calls.exists());self.publish(summary='New candidate');self.publish(summary='Newest candidate');self.engine.tick()
        self.assertEqual(len(self.engine.step(step['id'])['attempts']),1)
        done=self.finish(step['id']);self.assertEqual(done['attempts'][0]['state'],'completed')
        # Tick after completion retires the old terminal and reserves one replacement.
        self.engine.tick();second=self.finish(step['id']);self.assertEqual(len(second['attempts']),2,second)
        inbox=self.engine.store.inbox(second['attempts'][-1]['session_id'])['items'];self.assertEqual(inbox[0]['result']['summary'],'Newest candidate')

    def test_edit_requires_new_review_and_reject_never_dispatches(self):
        step=self.propose();accepted=self.accept(step)
        edited=self.engine.edit(step['id'],task='Narrower task',reason='Operator refined scope',expected_output='Only the requested fix',config=step['config'],dependencies=[],expected_version=accepted['version'],actor='test')
        self.assertEqual(edited['decision'],'proposed');self.engine.tick();self.assertFalse(self.calls.exists())
        self.engine.decide(step['id'],decision='rejected',expected_version=edited['version'],actor='test')
        self.engine.tick();self.assertFalse(self.calls.exists())

    def test_concurrent_sweeps_and_recovery_after_creation_do_not_relaunch(self):
        from concurrent.futures import ThreadPoolExecutor
        step=self.propose();self.accept(step)
        original=self.engine._prepare
        with patch.object(self.engine,'_prepare',side_effect=RuntimeError('Simulated dispatcher loss after session persistence')):
            self.engine.tick()
        self.assertFalse(self.calls.exists())
        recovered=WorkflowEngine(self.manager)
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:recovered.tick(),range(3)))
        done=self.finish(step['id']);self.assertEqual(done['attempts'][0]['state'],'completed',done)
        self.assertEqual(len(done['attempts']),1);self.assertEqual(self.calls.read_text().splitlines(),['started'])

    def test_pause_after_terminal_creation_holds_native_gate(self):
        step=self.propose();self.accept(step);prepare=self.engine._prepare
        def paused_prepare(attempt,session):
            self.engine.control(self.root['id'],state='paused',actor='test');return prepare(attempt,session)
        with patch.object(self.engine,'_prepare',side_effect=paused_prepare):self.engine.tick()
        time.sleep(.3);self.assertFalse(self.calls.exists())
        self.engine.control(self.root['id'],state='running',actor='test')
        self.assertEqual(self.finish(step['id'])['attempts'][0]['state'],'completed')

    def test_stop_interrupts_owned_running_attempt_and_keeps_history(self):
        step=self.propose(task='slow-native');self.accept(step);self.engine.tick()
        deadline=time.monotonic()+5
        while not self.calls.exists() and time.monotonic()<deadline:time.sleep(.05)
        self.assertTrue(self.calls.exists())
        self.engine.control(self.root['id'],state='stopped',actor='test');self.engine.tick()
        attempt=self.engine.step(step['id'])['attempts'][0]
        self.assertEqual(attempt['state'],'cancelled',attempt)
        self.assertFalse(self.manager.inspect(attempt['name'])['running'])
        self.assertFalse(self.manager.inspect(self.root['tmux_name'])['running'])

    def test_read_action_uses_read_only_sandbox_even_for_general_role(self):
        step=self.propose(action='read');self.accept(step);done=self.finish(step['id'])
        launch=self.settings.state_dir/'workflow-attempts'/done['attempts'][0]['id']/'launch.json'
        argv=json.loads(launch.read_text())['argv']
        self.assertEqual(argv[argv.index('--sandbox')+1],'read-only')
        self.assertIn('The supervising runner owns result publication',' '.join(argv))
        self.assertNotIn('To signal completion:',' '.join(argv))

    def test_lost_prepared_terminal_cannot_silently_retry(self):
        step=self.propose();self.accept(step);prepare=self.engine._prepare
        def paused_prepare(attempt,session):
            self.engine.control(self.root['id'],state='paused',actor='test');return prepare(attempt,session)
        with patch.object(self.engine,'_prepare',side_effect=paused_prepare):self.engine.tick()
        attempt=self.engine.step(step['id'])['attempts'][0]
        self.manager.tmux.kill(attempt['name'])
        # A killed runner may acknowledge cancellation itself; simulate loss of
        # that receipt to exercise reconciliation of a prepared launch.
        time.sleep(.3);self.engine._attempt_state(attempt['id'],'prepared')
        self.engine.tick();current=self.engine.step(step['id'])
        self.assertEqual(current['attempts'][0]['state'],'unknown')
        self.assertFalse(self.calls.exists())
        with self.assertRaisesRegex(ValueError,'reconciled'):self.engine.retry(step['id'],actor='test')

    def test_lost_prepared_runner_is_detected_even_when_shell_remains(self):
        import signal
        step=self.propose();self.accept(step);prepare=self.engine._prepare
        def paused_prepare(attempt,session):
            self.engine.control(self.root['id'],state='paused',actor='test');return prepare(attempt,session)
        with patch.object(self.engine,'_prepare',side_effect=paused_prepare):self.engine.tick()
        attempt=self.engine.step(step['id'])['attempts'][0]
        path=self.settings.state_dir/'workflow-attempts'/attempt['id']/'runner-presence.json'
        deadline=time.monotonic()+5
        while not path.is_file() and time.monotonic()<deadline:time.sleep(.05)
        handle=json.loads(path.read_text());os.kill(handle['pid'],signal.SIGKILL)
        time.sleep(.2);self.engine.tick()
        self.assertEqual(self.engine.step(step['id'])['attempts'][0]['state'],'unknown')
        self.assertFalse(self.calls.exists())

    def test_budget_reached_does_not_silently_launch_replacement(self):
        policy={**DEFAULT_POLICY,'max_total':2}
        self.engine.configure(self.root['id'],policy=policy,expected_version=0,actor='test')
        step=self.propose();self.accept(step);self.finish(step['id'])
        self.engine.retry(step['id'],actor='test');self.engine.tick()
        current=self.engine.step(step['id']);self.assertEqual(len(current['attempts']),1)
        self.assertIn('budget',current['error'])

    def test_native_selected_file_is_snapshotted_from_isolated_worktree(self):
        repo=self.workspace/'repository';repo.mkdir()
        subprocess.run(['git','init','-q',str(repo)],check=True)
        (repo/'seed.txt').write_text('Base commit')
        subprocess.run(['git','-C',str(repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','base'],check=True)
        step=self.propose(task='write-artifact',repository=str(repo),worktree=True);self.accept(step)
        done=self.finish(step['id']);self.assertEqual(done['attempts'][0]['state'],'completed',done)
        result=self.engine.store.results(done['attempts'][0]['session_id'])[0]
        self.assertEqual(self.engine.store.artifact(result['id'],0)[0],b'Actual isolated worktree output')
        self.assertFalse((repo/'result.txt').exists())

    def test_uncertain_launch_requires_reconciliation_before_retry(self):
        step=self.propose();self.accept(step)
        with patch.object(self.engine,'_prepare',side_effect=RuntimeError('Lost receipt preparation')):self.engine.tick()
        attempt=self.engine.step(step['id'])['attempts'][0]
        self.engine._attempt_state(attempt['id'],'unknown','Fixture uncertainty before native startup')
        self.engine.tick();self.assertFalse(self.calls.exists())
        with self.assertRaisesRegex(ValueError,'reconciled'):self.engine.retry(step['id'],actor='test')
        resolved=self.engine.reconcile(attempt['id'],outcome='not-started',summary='Verified that the native startup gate was never claimed.',actor='test')
        self.assertEqual(resolved['attempts'][0]['state'],'cancelled')
        self.engine.retry(step['id'],actor='test');self.assertEqual(self.finish(step['id'])['attempts'][-1]['state'],'completed')
        self.assertEqual(self.calls.read_text().splitlines(),['started'])
