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
        old['initial_task']='Original native brief';current['initial_task']='Current native brief'
        compact=self.state.snapshot(compact=True);briefs={s['id']:s.get('initial_task') for s in compact['sessions']}
        self.assertEqual(briefs[old['id']],'Original native brief');self.assertEqual(briefs[current['id']],'Current native brief')

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

    def test_history_reads_noninteractive_records_without_granting_workflow_access(self):
        for name,managed,kind in [('imported',False,'interactive'),('integration',True,'integration-plan')]:
            session=self.session(name)
            session.update(managed=managed,execution_kind=kind)
            with self.manager.database.connect() as db:
                db.execute('UPDATE sessions SET managed=?,execution_kind=? WHERE id=?',(managed,kind,session['id']))
            self.manager.database.audit('session.imported',session['id'],'success',actor='operator',details={'private':'not returned'})
            history=self.state.history(session['id'])
            self.assertEqual(history['audit'][0]['action'],'session.imported')
            self.assertNotIn('private',json.dumps(history))
            self.assertEqual(history,self.state.history(name))
            with self.assertRaisesRegex(ValueError,'managed interactive'):
                self.engine.svc.session(session['id'])
        with self.assertRaises(KeyError):self.state.history('missing')
    def test_hide_stopped_parent_running_child_retains_group_and_child_visible(self):
        parent=self.session('parent');child=self.session('child',parent=parent['id'],running=True)
        self.publish(parent)
        self.state.set_visibility(parent['id'],True)
        view=self.state.snapshot()
        nodes={n['id']:n for n in view['nodes']}
        self.assertEqual(len(nodes),2)
        self.assertEqual(len(view['groups']),1)
        self.assertEqual(nodes[parent['id']]['hidden'],True)
        self.assertEqual(nodes[child['id']]['hidden'],False)
        with self.manager.database.connect() as db:
            rows=db.execute('SELECT status,tmux_name FROM sessions ORDER BY id').fetchall()
            self.assertEqual({r['status'] for r in rows},{'detached','process-exited'})

    def test_hide_running_session_raises_and_no_preference_written(self):
        running=self.session('running',running=True)
        with self.assertRaisesRegex(ValueError,'Running sessions cannot be hidden'):
            self.state.set_visibility(running['id'],True)
        self.assertEqual(self.state.visibility.read(),{})

    def test_legacy_archived_defaults_hidden_explicit_restore_persists(self):
        session=self.session('legacy')
        with self.manager.database.connect() as db:
            db.execute('UPDATE sessions SET status=? WHERE id=?',('archived',session['id']))
        session['status']='archived'
        view=self.state.snapshot();node=next(n for n in view['nodes'] if n['id']==session['id'])
        self.assertEqual(node['hidden'],True)
        self.state.set_visibility(session['id'],False)
        new_state=WorkbenchState(self.manager)
        node=new_state.snapshot()['nodes'];node=next(n for n in node if n['id']==session['id'])
        self.assertEqual(node['hidden'],False)
        with self.manager.database.connect() as db:
            row=db.execute('SELECT status FROM sessions WHERE id=?',(session['id'],)).fetchone()
            self.assertEqual(row['status'],'archived')
    def test_compact_snapshot_omits_session_task_keeps_node_task(self):
        task='Full searchable UniqueTail'*100
        session=self.session('compact')
        session['initial_task']=task
        with self.manager.database.connect() as db:
            db.execute('UPDATE sessions SET initial_task=? WHERE id=?',(task,session['id']))
        full=self.state.snapshot()
        compact=self.state.snapshot(compact=True)
        self.assertEqual(full['sessions'][0]['initial_task'],task)
        self.assertNotIn('initial_task',compact['sessions'][0])
        self.assertEqual(next(n['task'] for n in full['nodes']),task)
        self.assertEqual(next(n['task'] for n in compact['nodes']),task)
        self.assertEqual(len(compact['groups']),len(full['groups']))
        self.assertEqual({g['root_id'] for g in compact['groups']},{g['root_id'] for g in full['groups']})
        self.assertLess(len(json.dumps(compact,sort_keys=True,ensure_ascii=False)),len(json.dumps(full,sort_keys=True,ensure_ascii=False)))

    def test_strict_visibility_payload_and_missing_identity(self):
        from agent_console.workbench_state import HideRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            HideRequest(hidden='true')
        self.assertIs(True,HideRequest(hidden=True).hidden)
        with self.assertRaises(KeyError):self.state.set_visibility('missing',True)
        self.assertEqual(self.state.visibility.read(),{})

    def test_visibility_route_keeps_identity_guard_and_strict_payload(self):
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.testclient import TestClient
        from agent_console.workbench_state import workbench_routes
        session=self.session('api-stopped')
        def identity(x_operator: str | None = Header(None)):
            if x_operator!='test':raise HTTPException(401,'Authentication required')
        app=FastAPI();app.include_router(workbench_routes(self.manager,identity))
        with TestClient(app) as client:
            path=f"/api/workbench/sessions/{session['id']}/visibility"
            self.assertEqual(client.patch(path,json={'hidden':True}).status_code,401)
            self.assertEqual(self.state.visibility.read(),{})
            self.assertEqual(client.patch(path,json={'hidden':'true'},headers={'x-operator':'test'}).status_code,422)
            response=client.patch(path,json={'hidden':True},headers={'x-operator':'test'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['hidden'])
            self.assertTrue(self.state.snapshot()['nodes'][0]['hidden'])
        with self.manager.database.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM sessions WHERE id=?',(session['id'],)).fetchone()['status'],'process-exited')
