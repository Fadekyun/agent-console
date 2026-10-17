import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_console import cli, session_client
from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.session_control_api import session_control_routes
from agent_console.tmux import TmuxSession


class ScopedChildWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manager=SessionManager(Settings(workspace_root=self.root,state_dir=self.root/'state',
            database_path=self.root/'state/db.sqlite',profile_dir=self.root/'profiles',
            handoff_dir=self.root/'handoffs',worktree_root=self.root/'trees',
            tmux_socket='unused-scoped-wait',legacy_tmux_socket_path=None))
        for identity,name,parent,status in [('parent','parent-name',None,'detached'),
                ('old','old-failure','parent','process-exited'),('active','active-name','parent','detached'),
                ('grandchild','grandchild-name','active','detached'),('outside','outside-name',None,'process-exited')]:
            self.insert(identity,name,parent,status)
        with self.manager.database.connect() as db:
            db.execute("UPDATE sessions SET attention_state='needs_input',attention_note='Keep this note' WHERE id='parent'")
            db.execute("UPDATE sessions SET exit_reason='Earlier task exited without completion' WHERE id='old'")
        self.live_ids={'parent','active','grandchild'}
        live=patch.object(self.manager,'_live_sessions',side_effect=self.live);live.start();self.addCleanup(live.stop)
        observe=patch('agent_console.inspection_views.TmuxObservation',side_effect=self.observation)
        observe.start();self.addCleanup(observe.stop)
        app=FastAPI();app.include_router(session_control_routes(self.manager))
        self.client=TestClient(app);self.addCleanup(self.client.close)

    def insert(self,identity,name,parent,status='process-exited'):
        with self.manager.database.connect() as db:
            db.execute("INSERT INTO sessions(id,tmux_name,parent_session_id,tool,profile,managed,status,created_at,evidence_capability_hash,repository) VALUES(?,?,?,'shell','general',1,?,'now',?,?)",
                (identity,name,parent,status,hashlib.sha256((identity+'-cap').encode()).hexdigest(),str(self.root)))

    def rows(self):
        with self.manager.database.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM sessions')]

    def live(self):
        return {row['tmux_name']:('canonical',TmuxSession(row['tmux_name'],1,1,0,1,'sh'))
                for row in self.rows() if row['id'] in self.live_ids}

    def observation(self,settings):
        return SimpleNamespace(observed_at='now',sessions={name:{'socket_scope':'canonical',
            'current_command':'sh','attached_clients':0} for name in self.live()})

    def request(self,payload):
        return self.client.post('/api/agent-sessions',json={'command':'children','payload':payload},
            headers={'Authorization':'Bearer parent-cap','X-Agent-Console-Session':'parent'})

    def finish_and_rename(self,interval):
        with self.manager.database.connect() as db:
            db.execute("UPDATE sessions SET tmux_name='renamed-child',attention_state='ready_for_review' WHERE id='active'")
            db.execute("UPDATE sessions SET tmux_name='renamed-parent' WHERE id='parent'")
        self.insert('replacement','active-name','parent')

    def assert_attention_unchanged(self):
        rows={row['id']:row for row in self.rows()}
        self.assertEqual((rows['parent']['attention_state'],rows['parent']['attention_note']),('needs_input','Keep this note'))
        self.assertEqual(rows['old']['attention_state'],'normal')
        self.assertEqual(rows['old']['exit_reason'],'Earlier task exited without completion')

    def test_local_cli_waits_only_selected_batch_and_pins_renamed_ids(self):
        markers={key:'' for key in ('AGENT_CONSOLE_REPORTING_URL','AGENT_CONSOLE_SESSION_ID','AGENT_CONSOLE_EVIDENCE_CAPABILITY')}
        with patch.dict(os.environ,markers),patch.object(cli,'SessionManager',return_value=self.manager), \
                patch('agent_console.manager.time.sleep',side_effect=self.finish_and_rename) as sleep, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            result=cli.main(['--json','session','wait-for-children','parent-name',
                '--child','active-name','--child','active','--child','active-name','--timeout','5','--poll-interval','1'])
        self.assertEqual(result,0);sleep.assert_called_once()
        report=json.loads(out.getvalue())
        self.assertEqual(report['outcome'],'success')
        self.assertEqual(report['selected_child_ids'],['active'])
        self.assertEqual([(c['child_id'],c['tmux_name'],c['wait_status']) for c in report['children']],
                         [('active','renamed-child','success')])
        saved=self.manager.wait_status('renamed-parent')
        self.assertEqual(saved['summary']['selected_child_ids'],['active'])
        self.assert_attention_unchanged()

    def test_local_unscoped_preserves_historical_failure_and_selected_failure_is_not_hidden(self):
        report=self.manager.wait_for_children('parent-name',timeout=5,poll_interval=1)
        self.assertEqual(report['outcome'],'failure')
        self.assertEqual({c['child_id'] for c in report['children']},{'old','active','grandchild'})
        report=self.manager.wait_for_children('parent-name',child_selectors=['old'],timeout=5,poll_interval=1)
        self.assertEqual(report['outcome'],'failure')
        self.assertEqual(report['children'][0]['wait_status'],'completed')
        self.assert_attention_unchanged()

    def test_local_selected_running_child_times_out_ignoring_old_failure(self):
        report=self.manager.wait_for_children('parent-name',child_selectors=['active-name'],
            timeout=1,poll_interval=1)
        self.assertEqual((report['outcome'],report['exit_code']),('timeout',1))
        self.assertEqual(report['selected_child_ids'],['active'])
        self.assertEqual([(child['child_id'],child['running'],child['wait_status'])
            for child in report['children']],[('active',True,'timeout')])
        self.assert_attention_unchanged()

    def test_local_selected_intervention_ignores_old_failure(self):
        for state in ('blocked','needs_input'):
            with self.subTest(state=state):
                with self.manager.database.connect() as db:
                    db.execute("UPDATE sessions SET attention_state=? WHERE id='active'",(state,))
                report=self.manager.wait_for_children('parent-name',child_selectors=['active-name'],
                    timeout=5,poll_interval=1)
                self.assertEqual((report['outcome'],report['exit_code']),('intervention',2))
                self.assertEqual([(child['child_id'],child['attention_state'],child['wait_status'])
                    for child in report['children']],[('active',state,'intervention')])
                self.assert_attention_unchanged()

    def test_local_invalid_selectors_fail_before_creating_wait_record(self):
        self.insert('shadow','active',None)
        for selectors in (['missing'],['outside'],['grandchild'],['active'],[],['']):
            with self.subTest(selectors=selectors),self.assertRaises((ValueError,PermissionError)):
                self.manager.wait_for_children('parent-name',child_selectors=selectors,timeout=5)
        with self.manager.database.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_waits').fetchone()[0],0)
        self.assert_attention_unchanged()

    def test_local_selected_child_disappearance_fails_instead_of_empty_success(self):
        def remove(interval):
            self.live_ids.remove('active')
            with self.manager.database.connect() as db:
                db.execute("UPDATE sessions SET parent_session_id='outside' WHERE id='active'")
        with patch('agent_console.manager.time.sleep',side_effect=remove),self.assertRaises(ValueError):
            self.manager.wait_for_children('parent-name',child_selectors=['active'],timeout=5,poll_interval=1)
        self.assertEqual(self.manager.wait_status('parent-name')['outcome'],'failure')

    def test_managed_cli_uses_authorized_backend_then_stable_parent_and_child_ids(self):
        calls=[]
        def transport(command,payload):
            self.assertEqual(command,'children');calls.append(payload.copy())
            response=self.request(payload)
            self.assertEqual(response.status_code,200,response.text)
            if len(calls)==1:
                self.assertEqual(response.json()['children'][0]['running'],True)
                self.assertEqual(response.json()['children'][0]['attention_state'],'normal')
            return response.json()
        with patch.dict(os.environ,{'AGENT_CONSOLE_REPORTING_URL':'http://fixture.invalid',
                'AGENT_CONSOLE_SESSION_ID':'parent','AGENT_CONSOLE_EVIDENCE_CAPABILITY':'parent-cap'}), \
                patch.object(cli,'SessionManager',side_effect=AssertionError('managed caller opened writer')), \
                patch.object(session_client,'request',side_effect=transport), \
                patch.object(session_client.time,'sleep',side_effect=self.finish_and_rename) as sleep, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['session','wait-for-children','parent-name','--child','active-name',
                '--child','active','--timeout','5','--poll-interval','1']),0)
        sleep.assert_called_once()
        self.assertEqual(calls,[{'name':'parent-name','child_selectors':['active-name','active']},
                                {'name':'parent','child_ids':['active']}])
        report=json.loads(out.getvalue())
        self.assertEqual(report['outcome'],'success');self.assertEqual(report['selected_child_ids'],['active'])
        self.assertEqual([c['tmux_name'] for c in report['children']],['renamed-child'])
        self.assert_attention_unchanged()

    def managed_wait(self,timeout):
        args=SimpleNamespace(command='session',session_command='wait-for-children',name='parent-name',
            timeout=timeout,poll_interval=1,child_selectors=['active-name'])
        def transport(command,payload):
            self.assertEqual(command,'children')
            response=self.request(payload)
            self.assertEqual(response.status_code,200,response.text)
            return response.json()
        with patch.object(session_client,'request',side_effect=transport):
            return session_client.run(args)

    def test_managed_selected_running_child_times_out_ignoring_old_failure(self):
        report=self.managed_wait(timeout=1)
        self.assertEqual((report['outcome'],report['exit_code']),('timeout',1))
        self.assertEqual(report['selected_child_ids'],['active'])
        self.assertEqual([(child['id'],child['running'],child['wait_status'])
            for child in report['children']],[('active',True,'waiting')])
        self.assert_attention_unchanged()

    def test_managed_selected_intervention_ignores_old_failure(self):
        for state in ('blocked','needs_input'):
            with self.subTest(state=state):
                with self.manager.database.connect() as db:
                    db.execute("UPDATE sessions SET attention_state=? WHERE id='active'",(state,))
                report=self.managed_wait(timeout=5)
                self.assertEqual((report['outcome'],report['exit_code']),('intervention',2))
                self.assertEqual([(child['id'],child['attention_state'],child['wait_status'])
                    for child in report['children']],[('active',state,'intervention')])
                self.assert_attention_unchanged()

    def test_managed_backend_denies_invalid_outside_nested_and_ambiguous_children(self):
        self.insert('shadow','active',None)
        for selectors in (['missing'],['outside'],['grandchild'],['active'],[],[''],None):
            with self.subTest(selectors=selectors):
                response=self.request({'name':'parent','child_selectors':selectors})
                self.assertIn(response.status_code,(400,403),response.text)
        response=self.request({'name':'parent','child_selectors':['active-name','active-name']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['selected_child_ids'],['active'])
        # Pinned IDs remain unambiguous even if a different session uses the ID as its name.
        self.assertEqual(self.request({'name':'parent','child_ids':['active']}).status_code,200)
        self.assertEqual(self.request({'name':'outside','child_selectors':['active-name']}).status_code,403)
        self.assertEqual(self.request({'name':'parent','child_selectors':['active-name'],'child_ids':['active']}).status_code,400)
        with self.manager.database.connect() as db:
            db.execute("UPDATE sessions SET parent_session_id='outside' WHERE id='active'")
        self.assertEqual(self.request({'name':'parent','child_ids':['active']}).status_code,403)
        self.assert_attention_unchanged()

    def test_owner_wait_endpoint_passes_scoped_selection_to_manager(self):
        # The web module creates a default app on import; keep it fixture-owned.
        with patch('agent_console.manager.SessionManager',return_value=self.manager):
            import agent_console.web as web
        web.SessionManager=SessionManager
        with self.manager.database.connect() as db:
            db.execute("UPDATE sessions SET attention_state='ready_for_review' WHERE id='active'")
        with patch.multiple(web,EXPECTED_LOGIN='fixture@example.com',TRUSTED_HOSTS=['testserver']):
            client=TestClient(web.create_app(self.manager),client=('127.0.0.1',50000))
            self.addCleanup(client.close)
            result=client.post('/api/sessions/parent-name/wait-for-children',
                json={'child_selectors':['active-name'],'timeout':5,'poll_interval':1},
                headers={'Tailscale-User-Login':'fixture@example.com'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['selected_child_ids'],['active'])
        self.assertEqual(result.json()['outcome'],'success')
        self.assert_attention_unchanged()

    def test_managed_unscoped_failure_and_old_server_scope_rejection(self):
        args=SimpleNamespace(command='session',session_command='wait-for-children',name='parent',timeout=5,poll_interval=1)
        def transport(command,payload):
            response=self.request(payload);self.assertEqual(response.status_code,200,response.text)
            return response.json()
        with patch.object(session_client,'request',side_effect=transport):
            result=session_client.run(args)
        self.assertEqual(result['outcome'],'failure')
        self.assertEqual({c['id'] for c in result['children']},{'old','active','grandchild'})
        for selectors in (None, ['active']):
            args.child_selectors=selectors
            with self.subTest(selectors=selectors), \
                    patch.object(session_client,'request',return_value={'children':[]}), \
                    self.assertRaisesRegex(RuntimeError,'updated server'):
                session_client.run(args)

    def test_managed_unscoped_wait_pins_parent_across_rename_and_name_reuse(self):
        calls=[]
        def transport(command,payload):
            self.assertEqual(command,'children');calls.append(payload.copy())
            response=self.request(payload)
            self.assertEqual(response.status_code,200,response.text)
            return response.json()
        def finish_and_reuse(interval):
            with self.manager.database.connect() as db:
                db.execute("UPDATE sessions SET tmux_name='renamed-active' WHERE id='active'")
                db.execute("UPDATE sessions SET attention_state='ready_for_review' WHERE id='grandchild'")
            self.insert('replacement','active-name','parent')
        args=SimpleNamespace(command='session',session_command='wait-for-children',name='active-name',
            timeout=5,poll_interval=1)
        with patch.object(session_client,'request',side_effect=transport), \
                patch.object(session_client.time,'sleep',side_effect=finish_and_reuse) as sleep:
            result=session_client.run(args)
        sleep.assert_called_once()
        self.assertEqual(calls,[{'name':'active-name'},{'name':'active'}])
        self.assertEqual(result['outcome'],'success')
        self.assertEqual([child['id'] for child in result['children']],['grandchild'])
        self.assertNotIn('selected_child_ids',result)

    def test_managed_wait_rejects_changed_parent_in_both_modes(self):
        child={'id':'active','tmux_name':'active-name','parent_session_id':'parent',
               'running':True,'attention_state':'normal'}
        for selectors in (None, ['active']):
            args=SimpleNamespace(command='session',session_command='wait-for-children',name='parent-name',
                timeout=5,poll_interval=1,child_selectors=selectors)
            first={'parent_id':'parent','children':[child.copy()],'selected_child_ids':['active']}
            second={**first,'parent_id':'replacement','children':[child.copy()]}
            with self.subTest(selectors=selectors), \
                    patch.object(session_client,'request',side_effect=[first,second]), \
                    patch.object(session_client.time,'sleep'), \
                    self.assertRaisesRegex(RuntimeError,'changed.*parent'):
                session_client.run(args)

    def test_managed_read_only_wait_requires_same_tree(self):
        self.insert('outside-child','outside-child-name','outside')
        with self.manager.database.connect() as db:
            db.execute("UPDATE sessions SET profile='reviewer',agent_mode='plan' WHERE id='parent'")
        for linked in (False, True):
            if linked:
                with self.manager.database.connect() as db:
                    db.execute("UPDATE sessions SET parent_session_id='parent' WHERE id='outside'")
            for selection in ({}, {'child_selectors':['outside-child-name']}, {'child_ids':['outside-child']}):
                with self.subTest(linked=linked, selection=selection):
                    response=self.request({'name':'outside-name',**selection})
                    self.assertEqual(response.status_code,200 if linked else 403,response.text)
                    if linked:
                        self.assertEqual(response.json()['parent_id'],'outside')
                        self.assertEqual([row['id'] for row in response.json()['children']],['outside-child'])


if __name__=='__main__':
    unittest.main()
