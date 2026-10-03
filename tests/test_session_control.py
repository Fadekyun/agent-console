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
            interrupt=Mock(return_value={'interrupted':True}), delegate=Mock(return_value={'delegated':True}))
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

    def test_metadata_survives_socket_failure_without_cross_project_leak(self):
        result = self.request('read',{'route':['session','list']})
        self.assertEqual(result.status_code,200,result.text)
        rows = result.json()
        self.assertEqual({row['id'] for row in rows},{'parent','child','peer'})
        self.assertTrue(all(row['running'] is None and row['live_state']=='unknown' for row in rows))
        self.assertNotIn('evidence_capability_hash',result.text)
        self.assertEqual(self.request('read',{'route':['session','inspect'],'name':'other'}).status_code,403)

    def test_peer_review_remains_available_to_read_only_roles(self):
        result = self.request('read',{'route':['session','review'],'name':'parent','lines':5},identity='peer')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['source'],'unavailable')
        self.assertEqual(result.json()['session']['live_state'],'unknown')

    def test_saved_output_remains_readable_when_socket_is_unavailable(self):
        transcript = self.settings.state_dir / 'saved.txt'
        transcript.write_text('saved peer output\n')
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET archived_transcript=? WHERE id='parent'", (str(transcript),))
        result = self.request('read',{'route':['session','review'],'name':'parent','lines':5},identity='peer')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['source'],'archived-transcript')
        self.assertEqual(result.json()['content'],'saved peer output\n')

    def test_attention_own_identity_and_descendant_control(self):
        self.assertEqual(self.request('attention',{'state':'ready_for_review'},identity='peer').status_code,200)
        self.manager.set_attention.assert_called_once()
        self.assertEqual(self.request('interrupt',{'name':'child'}).status_code,200)
        self.assertEqual(self.request('interrupt',{'name':'peer'}).status_code,403)
        self.assertEqual(self.request('interrupt',{'name':'parent'},identity='child').status_code,403)
        self.assertEqual(self.request('attention',{'name':'parent','state':'ready_for_review'},identity='peer').status_code,403)

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
        with patch.dict(os.environ,{'AGENT_CONSOLE_REPORTING_URL':'http://localhost:3210'}), \
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
        with patch('agent_console.session_client.request', return_value={'children':[{'running':None,'attention_state':'normal'}]}), \
             patch('agent_console.session_client.time.monotonic',side_effect=[0,2]):
            result=run(args)
        self.assertEqual(result['outcome'],'timeout')
        self.assertEqual(result['children'][0]['wait_status'],'waiting')

if __name__ == '__main__':
    unittest.main()
