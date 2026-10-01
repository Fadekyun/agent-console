import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.workflow_engine import WorkflowEngine
from agent_console.workbench_state import WorkbenchState


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);workspace=root/'workspace';workspace.mkdir();profiles=root/'profiles';profiles.mkdir()
        (profiles/'general.md').write_text('# General')
        self.manager=SessionManager(Settings(workspace_root=workspace,state_dir=root/'state',database_path=root/'state/main.sqlite3',profile_dir=profiles,handoff_dir=root/'handoffs',worktree_root=workspace/'worktrees',tmux_socket='unused-workbench-test'))
        self.engine=WorkflowEngine(self.manager);self.state=WorkbenchState(self.manager)
        self.sessions=[]
        self.patches=[patch.object(self.manager,'list_sessions',side_effect=lambda:self.sessions),patch.object(self.manager,'inspect',side_effect=lambda name:next(s for s in self.sessions if s['tmux_name']==name)),patch.object(self.manager,'tool_catalog',return_value=[{'name':'codex','status':'ready'}])]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in self.patches:p.stop()
        self.temp.cleanup()

    def session(self,name,*,parent=None,running=False,attention='normal'):
        item={'id':'sess-'+name,'tmux_name':name,'tool':'shell','profile':'general','repository':str(self.manager.settings.workspace_root),'parent_session_id':parent,'managed':True,'running':running,'status':'detached' if running else 'process-exited','attention_state':attention,'created_at':'2026-10-01T00:00:00+00:00','last_activity':'2026-10-01T00:00:00+00:00'}
        with self.manager.database.connect() as db:db.execute('INSERT INTO sessions(id,tmux_name,tool,profile,repository,parent_session_id,created_at,last_activity,status,managed) VALUES(?,?,?,?,?,?,?,?,?,1)',tuple(item[k] for k in ['id','tmux_name','tool','profile','repository','parent_session_id','created_at','last_activity','status']))
        self.sessions.append(item);return item

    def publish(self,session,outcome='pass'):
        return self.engine.svc.publish(session['id'],{'kind':'final','outcome':outcome,'summary':'Actual verified result','checks':[],'artifacts':[],'request_key':'final-1'},'test')

    def test_empty_ready_work_has_no_attention_groups(self):
        view=self.state.snapshot();self.assertEqual(view['groups'],[]);self.assertTrue(view['readiness']['ready'])

    def test_priority_keeps_attention_mechanics_and_results_distinct(self):
        stopped=self.session('stopped');running=self.session('running',running=True);blocked=self.session('blocked',running=True,attention='needs_input')
        view=self.state.snapshot();self.assertEqual([g['root_id'] for g in view['groups']],[blocked['id'],running['id'],stopped['id']])
        stopped_node=next(n for n in view['nodes'] if n['id']==stopped['id']);self.assertEqual(stopped_node['result_state'],'unknown')
        self.publish(stopped);node=next(n for n in self.state.snapshot()['nodes'] if n['id']==stopped['id'])
        self.assertEqual((node['mechanical'],node['attention'],node['result_state']),('stopped','normal','completed'))

    def test_connected_owner_replaces_launch_parent_without_restarting(self):
        a=self.session('original');b=self.session('new-owner');child=self.session('child',parent=a['id'],running=True)
        self.engine.svc.attach(b['id'],child['id'],purpose='Related work',dependencies=[],expected_version=0,actor='test')
        view=self.state.snapshot();node=next(n for n in view['nodes'] if n['id']==child['id'])
        self.assertEqual(node['owner_id'],b['id']);self.assertEqual(node['root_id'],b['id']);self.assertTrue(child['running']);self.assertEqual(child['parent_session_id'],a['id'])

    def test_logical_step_collapses_native_attempts_and_preserves_their_history(self):
        root=self.session('root');old=self.session('old',parent=root['id']);current=self.session('current',parent=root['id'])
        step=self.engine.propose(root['id'],task='One distinct task',reason='Useful split',expected_output='Result',config={'tool':'shell','profile':'general'},dependencies=[],request_key='step',actor='test')
        for number,session in enumerate([old,current],1):
            with self.engine.store.connect(write=True) as db:
                db.execute('INSERT INTO workflow_attempts(id,step_id,generation,step_version,session_id,name,state,input_signature,inputs_json,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('attempt-'+str(number),step['id'],number,1,session['id'],session['tmux_name'],'completed','signature','[]','{}','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00'))
        with self.engine.store.connect(write=True) as db:db.execute('INSERT INTO work_node_bindings VALUES(?,?)',(step['id'],current['id']))
        self.publish(old,'fail');self.publish(current)
        view=self.state.snapshot();self.assertEqual(len(view['nodes']),2);node=next(n for n in view['nodes'] if n['id']==step['id'])
        self.assertEqual(node['native_id'],current['id']);self.assertEqual(node['attempts'][0]['result']['outcome'],'fail');self.assertEqual(view['aliases'][old['id']],step['id'])
        self.assertEqual(view['groups'][0]['children_total'],1)
        history=self.state.history(old['id']);self.assertTrue(any(e['action']=='result.published' for e in history['events']))
        self.assertEqual(self.engine.inspect(old['id'])['root_id'],root['id'])

    def test_add_below_unlaunched_step_waits_for_its_logical_input(self):
        root=self.session('root');data={'task':'Pending step','reason':'Distinct task','expected_output':'Result','config':{'tool':'shell','profile':'general'},'dependencies':[],'request_key':'one','actor':'test'}
        first=self.engine.propose(root['id'],**data)
        second=self.engine.propose(first['id'],**{**data,'request_key':'two','dependencies':[{'source_id':first['id'],'readiness':'after-final'}]})
        self.assertEqual(second['owner_id'],first['id']);view=self.state.snapshot();node=next(n for n in view['nodes'] if n['id']==second['id'])
        self.assertTrue(node['waiting']);self.assertTrue(node['readiness']['blocked']);self.assertEqual(node['root_id'],root['id'])

    def test_failed_result_needs_attention_and_missing_tool_has_readiness_warning(self):
        session=self.session('failed');self.publish(session,'fail')
        with patch.object(self.manager,'tool_catalog',return_value=[{'name':'codex','status':'error','reason':'tool launcher is missing'}]):view=self.state.snapshot()
        self.assertEqual(view['groups'][0]['priority'],0);self.assertFalse(view['readiness']['ready']);self.assertIn('tool launcher',view['readiness']['warnings'][0])

    def test_acknowledged_failure_remains_failed_without_permanent_attention(self):
        from datetime import datetime,timedelta
        session=self.session('reviewed-failure');result=self.publish(session,'fail')
        session['attention_updated_at']=(datetime.fromisoformat(result['created_at'])+timedelta(seconds=1)).isoformat()
        node=self.state.snapshot()['nodes'][0]
        self.assertFalse(node['needs_attention']);self.assertEqual(node['result_state'],'failed')
