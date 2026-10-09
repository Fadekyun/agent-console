import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_console.config import Settings
from agent_console.database import Database
from agent_console.inspection import InspectionUnavailable
from agent_console.session_control import SessionControl, validate_child
from agent_console.session_control_api import session_control_routes
from agent_console import cli


class SessionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(workspace_root=self.root, state_dir=self.root/'state',
            database_path=self.root/'state/db.sqlite', profile_dir=self.root/'profiles',
            handoff_dir=self.root/'handoffs', worktree_root=self.root/'worktrees',
            tmux_socket=None, tmux_socket_path=self.root/'absent.sock', legacy_tmux_socket_path=None)
        self.db = Database(self.settings.database_path)
        self.db.migrate()
        self.manager = SimpleNamespace(database=self.db, settings=self.settings,
            set_attention=Mock(return_value={'attention_state':'ready_for_review'}),
            interrupt=Mock(return_value={'interrupted':True}), delegate=Mock(return_value={'delegated':True}),
            restart=Mock(return_value={'restarted':True}), kill=Mock(return_value={'killed':True}))
        with self.db.connect() as db:
            db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('p','Project','2026-10-03','2026-10-03')")
            db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('q','Other','2026-10-03','2026-10-03')")
            for key,parent,project,profile in [('parent',None,'p','coder'), ('child','parent','p','coder'),
                 ('peer',None,'p','reviewer'), ('other',None,'q','coder')]:
                db.execute('''INSERT INTO sessions(id,tmux_name,parent_session_id,project_id,profile,
                   tool,managed,created_at,status,evidence_capability_hash,repository)
                   VALUES(?,?,?,?,?,'codex',1,'2026-10-03','detached',?,?)''',
                   (key,key,parent,project,profile,hashlib.sha256((key+'-cap').encode()).hexdigest(),str(self.root)))
        self.app = FastAPI(); self.app.include_router(session_control_routes(self.manager))
        self.client = TestClient(self.app)
        self.observation = patch('agent_console.inspection_views.TmuxObservation', side_effect=InspectionUnavailable('observation-unavailable'))
        self.observation.start()

    def tearDown(self):
        self.observation.stop(); self.temp.cleanup()

    def request(self, command, payload=None, identity='parent', capability=None):
        return self.client.post('/api/agent-sessions',json={'command':command,'payload':payload or {}},
            headers={'Authorization':'Bearer '+(capability or identity+'-cap'),'X-Agent-Console-Session':identity})

    def test_authentication_rejects_wrong_capability_and_identity(self):
        self.assertEqual(self.request('read',{'route':['session','list']},capability='peer-cap').status_code,403)
        self.assertEqual(self.client.post('/api/agent-sessions',json={'command':'read'}).status_code,403)

    def test_metadata_survives_socket_failure_across_projects(self):
        result = self.request('read',{'route':['session','list']})
        self.assertEqual(result.status_code,200,result.text)
        rows = result.json()
        self.assertEqual({row['id'] for row in rows},{'parent','child','peer','other'})
        self.assertTrue(all(row['running'] is None and row['live_state']=='unknown' for row in rows))
        self.assertNotIn('evidence_capability_hash',result.text)
        self.assertEqual(self.request('read',{'route':['session','inspect'],'name':'other'}).status_code,200)

    def test_global_reads_by_name_and_id_preserve_relative_tree_scope(self):
        transcript = self.settings.state_dir / 'other.txt'
        transcript.write_text('earlier\npeer output\n')
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET tmux_name='other-name',archived_transcript=? WHERE id='other'", (str(transcript),))
        for project in ('p', None):
            with self.db.connect() as db:
                db.execute("UPDATE sessions SET project_id=? WHERE id='parent'", (project,))
            for identity in ('parent', 'peer'):
                for selector in ({'name':'other-name'}, {'name':'other'}, {'session_id':'other'}):
                    with self.subTest(project=project, identity=identity, selector=selector):
                        result = self.request('read', {'route':['session','review'], 'lines':1, **selector}, identity=identity)
                        self.assertEqual(result.status_code,200,result.text)
                        self.assertEqual(result.json()['session']['id'],'other')
                        self.assertEqual(result.json()['content'],'peer output\n')
            result = self.request('read', {'route':['session','tree']})
            self.assertEqual({row['id'] for row in result.json()['roots']},{'parent','peer','other'})
            result = self.request('read', {'route':['session','tree'], 'current':True})
            self.assertEqual([row['id'] for row in result.json()['roots']],['parent'])
            self.assertEqual([row['id'] for row in result.json()['roots'][0]['children']],['child'])
            result = self.request('read', {'route':['session','review'], 'relative':'child'})
            self.assertEqual(result.json()['session']['id'],'child')
        result = self.request('read', {'route':['session','inspect'], 'name':'missing'})
        self.assertEqual(result.status_code,404,result.text)

    def test_global_reads_exclude_unrecorded_tmux_sessions(self):
        observation = SimpleNamespace(sessions={'unrecorded':{'socket_scope':'canonical',
            'current_command':'codex','attached_clients':0}}, observed_at='now')
        with patch('agent_console.inspection_views.TmuxObservation', return_value=observation):
            result = self.request('read', {'route':['session','list']})
            self.assertNotIn('unrecorded', result.text)
            self.assertEqual(self.request('read', {'route':['session','inspect'], 'name':'unrecorded'}).status_code,404)

    def test_peer_review_remains_available_to_read_only_roles(self):
        result = self.request('read',{'route':['session','review'],'name':'parent','lines':5},identity='peer')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['source'],'unavailable')
        self.assertEqual(result.json()['session']['live_state'],'unknown')

    def test_private_integration_content_is_redacted_in_every_peer_view(self):
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET execution_kind='integration-plan',initial_task='PRIVATE-TASK',attention_note='PRIVATE-NOTE',exit_reason='PRIVATE-EXIT',archived_transcript='PRIVATE-PATH' WHERE id IN ('child','other')")
            db.execute("INSERT INTO delegations(id,parent_session_id,child_session_id,profile,task,status,created_at) VALUES('d','parent','child','coder','PRIVATE-DELEGATION','running','2026-10-03')")
            db.execute("INSERT INTO delegations(id,parent_session_id,child_session_id,profile,task,status,created_at) VALUES('outside','other','peer','reviewer','PRIVATE-DELEGATION','running','2026-10-03')")
        for command,payload in [('read',{'route':['session','list']}),
                ('read',{'route':['session','tree']}),('read',{'route':['session','inspect'],'name':'child'}),
                ('read',{'route':['session','context'],'name':'child'}),
                ('read',{'route':['session','review'],'name':'child'}),('children',{}),
                ('read',{'route':['session','inspect'],'name':'other'}),
                ('read',{'route':['session','context'],'name':'other'}),
                ('read',{'route':['session','review'],'session_id':'other'})]:
            result = self.request(command,payload)
            self.assertEqual(result.status_code,200,result.text)
            self.assertNotIn('PRIVATE-',result.text)
            self.assertNotIn('evidence_capability_hash',result.text)
        self.assertEqual(self.request('attention',{'name':'child','state':'ready_for_review'}).status_code,403)
        self.manager.set_attention.assert_not_called()
        self.assertEqual(self.request('delegate',{'profile':'verifier','task':'x'},identity='child').status_code,403)

    def test_malformed_payload_types_do_not_produce_server_errors(self):
        for payload in [{'route':1},{'route':['session','review'],'name':'parent','lines':'bad'}]:
            self.assertEqual(self.request('read',payload).status_code,400)

    def test_saved_output_remains_readable_when_socket_is_unavailable(self):
        transcript = self.settings.state_dir / 'saved.txt'
        transcript.write_text('saved peer output\n')
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET archived_transcript=? WHERE id='parent'", (str(transcript),))
        result = self.request('read',{'route':['session','review'],'name':'parent','lines':5},identity='peer')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['source'],'archived-transcript')
        self.assertEqual(result.json()['content'],'saved peer output\n')

    def test_capture_race_falls_back_to_saved_output_and_unknown_live_state(self):
        transcript = self.settings.state_dir / 'saved.txt'
        transcript.write_text('saved before capture failed\n')
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET archived_transcript=? WHERE id='parent'", (str(transcript),))
        observation = SimpleNamespace(sessions={'parent':{'socket_scope':'canonical',
            'current_command':'codex','attached_clients':0}}, observed_at='now',
            capture=Mock(side_effect=InspectionUnavailable('observation-unavailable')))
        with patch('agent_console.inspection_views.TmuxObservation',return_value=observation):
            result = self.request('read',{'route':['session','review'],'name':'parent'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['source'],'archived-transcript')
        self.assertEqual(result.json()['session']['live_state'],'unknown')
        self.assertIn('saved before capture failed',result.json()['content'])

    def test_attention_own_identity_and_descendant_control(self):
        self.assertEqual(self.request('attention',{'state':'ready_for_review'},identity='peer').status_code,200)
        self.manager.set_attention.assert_called_once()
        self.assertEqual(self.manager.set_attention.call_args.kwargs["session_id"], "peer")
        self.assertEqual(self.request('interrupt',{'name':'child'}).status_code,200)
        self.manager.interrupt.assert_called_once_with('child', session_id='child')
        self.assertEqual(self.request('interrupt',{'name':'peer'}).status_code,403)
        self.assertEqual(self.request('interrupt',{'name':'parent'},identity='child').status_code,200)
        self.assertEqual(self.request('attention',{'name':'parent','state':'ready_for_review'},identity='peer').status_code,403)

    def test_writable_children_control_ancestors_by_durable_identity(self):
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET tmux_name='parent-renamed',parent_session_id='other' WHERE id='parent'")
            # A name equal to another session's ID must not redirect an ID target.
            db.execute("UPDATE sessions SET tmux_name='parent' WHERE id='peer'")
        for target, name in [('parent','parent-renamed'), ('other','other')]:
            for command, method in [('attention','set_attention'), ('interrupt','interrupt'),
                                    ('restart-agent','restart'), ('kill','kill')]:
                with self.subTest(target=target, command=command):
                    mock = getattr(self.manager, method)
                    mock.reset_mock()
                    response = self.request(command, {'name':target, 'state':'ready_for_review'}, identity='child')
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(mock.call_args.args,(name,))
                    self.assertEqual(mock.call_args.kwargs['session_id'],target)
                    if command == 'attention':
                        self.assertEqual(mock.call_args.kwargs['actor'],'session:child')
                    if command == 'kill':
                        self.assertFalse(mock.call_args.kwargs['allow_unmanaged'])

    def test_mutation_boundaries_remain_enforced_with_global_visibility(self):
        for profile, mode in [('reviewer',None), ('coder','plan'), ('coder','auto')]:
            with self.db.connect() as db:
                db.execute("UPDATE sessions SET profile=?,agent_mode=? WHERE id='child'", (profile,mode))
                db.execute("UPDATE sessions SET parent_session_id='parent' WHERE id='peer'")
            targets = ['peer','other'] + (['parent'] if profile == 'reviewer' or mode == 'plan' else [])
            for target in targets:
                for command in ('attention','interrupt','restart-agent','kill'):
                    with self.subTest(profile=profile, mode=mode, target=target, command=command):
                        response = self.request(command, {'name':target,'state':'ready_for_review'}, identity='child')
                        self.assertEqual(response.status_code,403,response.text)
            for command in ('interrupt','restart-agent','kill'):
                self.assertEqual(self.request(command, {'name':'child'}, identity='child').status_code,403)
        for method in ('set_attention','interrupt','restart','kill'):
            getattr(self.manager,method).assert_not_called()

    def test_ancestor_controls_reject_noninteractive_unmanaged_and_cycles(self):
        for update, status in [("managed=0",403), ("managed=1,execution_kind='integration-plan'",403),
                               ("execution_kind='interactive',parent_session_id='child'",400)]:
            with self.db.connect() as db:
                db.execute('UPDATE sessions SET ' + update + " WHERE id='parent'")
            for command in ('attention','interrupt','restart-agent','kill'):
                response = self.request(command, {'name':'parent','state':'ready_for_review'}, identity='child')
                self.assertEqual(response.status_code,status,response.text)
        for method in ('set_attention','interrupt','restart','kill'):
            getattr(self.manager,method).assert_not_called()

    def test_child_waits_are_readable_across_projects_and_read_only_roles(self):
        for identity, profile, mode in [('peer','reviewer',None), ('other','coder','plan')]:
            with self.db.connect() as db:
                db.execute('UPDATE sessions SET profile=?,agent_mode=? WHERE id=?', (profile,mode,identity))
            for selection in ({}, {'child_selectors':['child']}, {'child_ids':['child']}):
                response = self.request('children', {'name':'parent',**selection}, identity=identity)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['parent_id'],'parent')
                self.assertEqual([child['id'] for child in response.json()['children']],['child'])
            response = self.request('children', {'name':'parent','child_ids':['other']}, identity=identity)
            self.assertEqual(response.status_code,403,response.text)

    def test_delegate_cannot_impersonate_parent(self):
        self.assertEqual(self.request('delegate',{'name':'peer','profile':'coder','task':'x'}).status_code,403)
        self.assertEqual(self.request('delegate',{'profile':'verifier','task':'x'}).status_code,200)
        self.assertEqual(self.manager.delegate.call_args.kwargs['parent'],'parent')

    def test_read_only_and_plan_delegation_never_escalate(self):
        parent={'profile':'reviewer','project_id':'p','repository':str(self.root)}
        validate_child(parent,profile='verifier',repository=str(self.root),project_id='p',agent_mode='plan',worktree=False)
        for child,mode in [('coder',None),('verifier','auto')]:
            with self.assertRaises(PermissionError):
                validate_child(parent,profile=child,repository=str(self.root),project_id='p',agent_mode=mode,worktree=True)
        parent.update(profile='coder',agent_mode='plan')
        with self.assertRaises(PermissionError):
            validate_child(parent,profile='coder',repository=str(self.root),project_id='p',agent_mode=None,worktree=True)

    def test_write_delegation_requires_worktree_project_and_repository(self):
        parent={'profile':'coder','project_id':'p','repository':str(self.root)}
        valid=dict(profile='coder',repository=str(self.root),project_id='p',agent_mode='auto',worktree=True)
        validate_child(parent,**valid)
        for override in [{'worktree':False},{'project_id':'q'},{'repository':'/tmp/unrelated'}]:
            with self.assertRaises(PermissionError):
                validate_child(parent,**{**valid,**override})

    def test_cli_attention_and_inspection_never_instantiate_writer(self):
        with patch.dict(os.environ,{'AGENT_CONSOLE_REPORTING_URL':'http://localhost:3210',
                                    'AGENT_CONSOLE_SESSION_ID':'fixture-session',
                                    'AGENT_CONSOLE_EVIDENCE_CAPABILITY':'fixture-capability'}), \
             patch('agent_console.cli.SessionManager',side_effect=AssertionError('writer opened')), \
             patch('agent_console.session_client.request',return_value={'ok':True}) as request, \
             patch('agent_console.cli.emit'):
            self.assertEqual(cli.main(['session','attention','--current','--state','ready_for_review']),0)
            self.assertEqual(cli.main(['session','inspect','peer']),0)
            self.assertEqual(request.call_count,2)

    def test_current_identity_survives_renames(self):
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET tmux_name='renamed' WHERE id='parent'")
        result = self.request('read',{'route':['session','relatives'],'current':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['current_id'],'parent')

    def test_child_wait_does_not_treat_unknown_observation_as_completion(self):
        from agent_console.session_client import run
        args=SimpleNamespace(command='session',session_command='wait-for-children',name='parent',timeout=1,poll_interval=1)
        with patch('agent_console.session_client.request', return_value={'parent_id':'parent','children':[{'running':None,'attention_state':'normal'}]}), \
             patch('agent_console.session_client.time.monotonic',side_effect=[0,2]):
            result=run(args)
        self.assertEqual(result['outcome'],'timeout')
        self.assertEqual(result['children'][0]['wait_status'],'waiting')

if __name__ == '__main__':
    unittest.main()
