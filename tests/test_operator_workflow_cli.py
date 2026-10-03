import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_console import cli
from agent_console.config import Settings
from agent_console.manager import SessionManager
from agent_console.operator_workflow_cli import MARKERS, MAX_PAYLOAD_BYTES
from agent_console.providers import TOOL_BINARIES
from agent_console.workflow_engine import DEFAULT_POLICY, WorkflowEngine
from agent_console.workflow_release import ReleaseService


class OperatorWorkflowCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base/'repo'
        self.repo.mkdir()
        self.profiles = self.base/'profiles'
        self.profiles.mkdir()
        for role in ('general', 'coder', 'planner'):
            (self.profiles/(role+'.md')).write_text('# '+role)
        self.manager = SessionManager(Settings(workspace_root=self.repo, state_dir=self.base/'state',
            database_path=self.base/'state/main.sqlite3', config_dir=self.base/'config',
            profile_dir=self.profiles, handoff_dir=self.base/'handoff', worktree_root=self.repo/'trees',
            tmux_socket='unused-owner-workflow-'+str(os.getpid())+'-'+str(id(self))))
        self.environment = patch.dict(os.environ, {key: '' for key in MARKERS})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for identity in ('root', 'peer'):
            with self.manager.database.connect() as db:
                db.execute("INSERT INTO sessions(id,tmux_name,tool,profile,repository,status,managed,created_at) VALUES(?,?,'shell','general',?,'process-exited',1,'now')",
                           (identity, identity, str(self.repo)))
        self.engine = WorkflowEngine(self.manager)
        self.fake = self.base/'fake-codex'
        self.fake.write_text('#!/bin/sh\ncase "$1" in\n--version) echo fixture-codex-1;;\n*) echo --output-schema --output-last-message --sandbox;;\nesac\n')
        self.fake.chmod(0o700)
        (self.manager.auth.codex_home('default')/'auth.json').write_text('{}')
        binaries = patch.dict(TOOL_BINARIES, {'codex': self.fake})
        binaries.start()
        self.addCleanup(binaries.stop)

    def invoke(self, words, data=None, raw=None):
        if data is not None or raw is not None:
            words = [*words, '--stdin']
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'SessionManager', return_value=self.manager), patch('sys.stdin', io.StringIO(raw if raw is not None else json.dumps(data))), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['workflow', 'manage', *words])
        return code, out.getvalue(), err.getvalue()

    def ok(self, words, data=None):
        code, out, err = self.invoke(words, data)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def proposal(self, **changes):
        return {'task':'Inspect the candidate', 'reason':'Bounded check', 'expected_output':'Recorded checks',
                'config':{'tool':'codex','profile':'general','worktree':False}, 'dependencies':[],
                'request_key':'proposal-one', **changes}

    def test_all_owner_routes_reject_each_managed_marker_before_manager_and_input(self):
        commands = [['inspect','root'], ['propose','root','--stdin'], ['policy','root','--stdin'],
                    ['control','root','--stdin'], ['attempt','reconcile','a','--stdin']]
        commands += [['step', action, 's', *([] if action in ('preview','retry') else ['--stdin'])]
                     for action in ('preview','review','edit','retry')]
        commands += [['connections', action, 'root', *([] if action == 'inspect' else ['--stdin'])]
                     for action in ('inspect','attach','dependencies','deliver')]
        commands += [['releases','targets'], ['releases','evidence','c'], ['releases','list','root'],
                     ['releases','inspect','r'], ['releases','preview','--stdin'],
                     ['releases','authorize','--stdin'], ['releases','start','r','--stdin']]
        for marker in MARKERS:
            with patch.dict(os.environ, {marker:'credential-sentinel'}):
                for words in commands:
                    with self.subTest(marker=marker, command=words), patch.object(cli,'SessionManager') as manager, patch('sys.stdin') as stdin, contextlib.redirect_stderr(io.StringIO()) as err:
                        self.assertEqual(cli.main(['workflow','manage',*words]), 2)
                        manager.assert_not_called()
                        stdin.read.assert_not_called()
                        stdin.buffer.read.assert_not_called()
                        self.assertIn('unsupported in managed sessions', err.getvalue())
                        self.assertNotIn('credential-sentinel', err.getvalue())

    def test_policy_and_control_use_persisted_versions_and_terminal_stop(self):
        self.assertEqual(self.ok(['inspect','root'])['policy']['version'], 0)
        request = {'policy':dict(DEFAULT_POLICY), 'expected_version':0}
        configured = self.ok(['policy','root'], request)
        self.assertEqual(configured['policy']['version'], 1)
        self.assertEqual(configured['policy']['actor'], 'CLI-user')
        self.assertEqual(self.invoke(['policy','root'], request)[0], 2)
        for state in ('paused','running','stopped'):
            self.assertEqual(self.ok(['control','root'], {'state':state})['policy']['state'], state)
        self.assertEqual(self.invoke(['control','root'], {'state':'running'})[0], 2)
        self.assertEqual(self.engine.policy('root')['state'], 'stopped')

    def test_step_preview_review_edit_require_exact_hash_and_version(self):
        proposal = self.proposal()
        step = self.ok(['propose','root'], proposal)
        self.assertEqual(self.ok(['propose','root'], proposal)['id'], step['id'])
        preview = self.ok(['step','preview',step['id']])
        request = {'decision':'accepted','expected_version':1,'preview_hash':'0'*64}
        self.assertEqual(self.invoke(['step','review',step['id']], request)[0], 2)
        self.assertEqual(self.engine.step(step['id'])['decision'], 'proposed')
        request['preview_hash'] = preview['hash']
        request['expected_version'] = 9
        self.assertEqual(self.invoke(['step','review',step['id']], request)[0], 2)
        request['expected_version'] = 1
        reviewed = self.ok(['step','review',step['id']], request)
        self.assertEqual(reviewed['decision'], 'accepted')
        self.assertEqual(reviewed['approved']['authority'], 'operator')
        edit = {key:value for key,value in proposal.items() if key != 'request_key'}
        edit.update(task='Revised check', expected_version=1)
        self.assertEqual(self.invoke(['step','edit',step['id']], edit)[0], 2)
        edit['expected_version'] = reviewed['version']
        edited = self.ok(['step','edit',step['id']], edit)
        self.assertEqual((edited['task'],edited['decision'],edited['version']), ('Revised check','proposed',3))
        self.assertIsNone(edited['approved'])
        self.assertEqual(self.ok(['step','review',step['id']], {'decision':'rejected','expected_version':3})['decision'], 'rejected')
        self.assertEqual(self.engine.step(step['id'])['attempts'], [])

    def test_connections_pin_versions_and_delivery_signature(self):
        graph = self.ok(['connections','inspect','root'])
        attached = self.ok(['connections','attach','root'], {'session_id':'peer','purpose':'Review input',
            'expected_version':graph['version'], 'dependencies':[{'source_id':'root','readiness':'after-final'}]})
        self.assertEqual(attached['version'], 1)
        self.assertEqual(self.invoke(['connections','dependencies','peer'], {'expected_version':0,'dependencies':[]})[0], 2)
        self.ok(['connections','dependencies','peer'], {'expected_version':1,'dependencies':[{'source_id':'root','readiness':'after-final'}]})
        self.engine.svc.publish('root', {'kind':'final','outcome':'pass','summary':'Ready','checks':['Inspected'],
            'artifacts':[],'request_key':'input'}, 'test')
        graph = self.ok(['connections','inspect','peer'])
        request = {'expected_version':graph['version'],'expected_signature':'0'*64}
        self.assertEqual(self.invoke(['connections','deliver','peer'], request)[0], 2)
        self.assertEqual(self.engine.store.inbox('peer')['items'], [])
        request['expected_signature'] = graph['readiness']['peer']['signature']
        delivery = self.ok(['connections','deliver','peer'], request)
        self.assertEqual(self.ok(['connections','deliver','peer'], request)['id'], delivery['id'])
        self.assertEqual(len(self.engine.store.inbox('peer')['items']), 1)

    def test_attempt_reconcile_and_retry_preserve_uncertainty_guard(self):
        step = self.ok(['propose','root'], self.proposal())
        with self.engine.store.connect(write=True) as db:
            db.execute("INSERT INTO workflow_attempts(id,step_id,generation,step_version,session_id,name,state,input_signature,inputs_json,config_json,created_at,updated_at) VALUES('uncertain',?,1,1,'missing-native','missing-native','unknown','','[]','{}','now','now')", (step['id'],))
        self.assertEqual(self.invoke(['step','retry',step['id']])[0], 2)
        result = self.ok(['attempt','reconcile','uncertain'], {'outcome':'not-started','summary':'Confirmed startup was never claimed'})
        self.assertEqual(result['attempts'][0]['state'], 'cancelled')
        self.assertEqual(self.ok(['step','retry',step['id']])['retry_requested'], 1)
        self.assertEqual(self.invoke(['attempt','reconcile','uncertain'], {'outcome':'not-started','summary':'Again'})[0], 2)

    def test_releases_preview_authorize_start_and_probe_keep_service_guards(self):
        subprocess.run(['git','init','-q',str(self.repo)], check=True)
        (self.repo/'candidate.txt').write_text('candidate')
        subprocess.run(['git','-C',str(self.repo),'add','.'], check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','fixture'], check=True)
        sha = subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'], text=True).strip()
        release = ReleaseService(self.manager)
        target = {'id':'fixture','label':'Isolated fixture','actions':['deploy'],'apply':[str(self.fake)],
                  'probe':[str(self.fake)],'timeout_seconds':1,'environment':[]}
        release.config_path.write_text(json.dumps({'version':1,'targets':[target]}))
        result = self.engine.svc.publish('root', {'kind':'final','outcome':'pass','summary':'Verified candidate',
            'checks':['Fixture check'],'artifacts':[{'kind':'commit','sha':sha}],'request_key':'candidate'}, 'test')
        self.assertEqual(self.ok(['releases','targets'])[0]['id'], 'fixture')
        self.assertNotIn('apply',self.ok(['releases','targets'])[0])
        self.assertTrue(self.ok(['releases','evidence',result['id']])[0]['eligible'])
        request = {'candidate_result_id':result['id'],'evidence_result_ids':[result['id']],'action':'deploy','target':'fixture'}
        preview = self.ok(['releases','preview'], request)
        authorization = {**request,'expected_hash':'0'*64,'request_key':'grant'}
        self.assertEqual(self.invoke(['releases','authorize'], authorization)[0], 2)
        self.assertEqual(self.ok(['releases','list','root']), [])
        authorization['expected_hash'] = preview['hash']
        grant = self.ok(['releases','authorize'], authorization)
        self.assertEqual(self.ok(['releases','authorize'], authorization)['id'], grant['id'])
        self.assertEqual(self.ok(['releases','inspect',grant['id']])['actor'], 'CLI-user')
        self.assertEqual(len(self.ok(['releases','list','root'])), 1)
        # Patch only the process boundary: all durable authorization, admission,
        # deduplication and uncertainty checks execute against the real services.
        with patch.object(ReleaseService,'_spawn') as spawn:
            self.assertEqual(self.invoke(['releases','start',grant['id']], {'mode':'probe','request_key':'too-early'})[0], 2)
            first = self.ok(['releases','start',grant['id']], {'mode':'apply','request_key':'apply-once'})
            duplicate = self.ok(['releases','start',grant['id']], {'mode':'apply','request_key':'apply-once'})
            self.assertEqual(first['id'], duplicate['id'])
            self.assertEqual(spawn.call_count, 1)
            self.assertEqual(self.invoke(['releases','start',grant['id']], {'mode':'apply','request_key':'unsafe-retry'})[0], 2)
            probe = self.ok(['releases','start',grant['id']], {'mode':'probe','request_key':'observe'})
            self.assertEqual(probe['mode'], 'probe')
            self.assertEqual(spawn.call_count, 2)

    def test_payload_validation_files_and_errors_never_echo_values(self):
        body = self.base/'control.json'
        body.write_text(json.dumps({'state':'paused'}))
        self.assertEqual(self.ok(['control','root','--json-file',str(body)])['policy']['state'], 'paused')
        bad = [('{"state":"credential-sentinel"}', ['control','root']),
               ('credential-sentinel', ['policy','root']),
               ('"'+'x'*MAX_PAYLOAD_BYTES+'"', ['policy','root']),
               ('{"request_key":"credential-sentinel"}', ['releases','authorize'])]
        for raw, words in bad:
            code, out, err = self.invoke(words, raw=raw)
            self.assertEqual(code, 2)
            self.assertNotIn('credential-sentinel', out+err)
            self.assertIn('Invalid workflow request', err)
        request = self.proposal(config={'tool':'credential-sentinel'})
        code, out, err = self.invoke(['propose','root'], request)
        self.assertEqual(code, 2)
        self.assertNotIn('credential-sentinel', out+err)
        for words in (['credential-sentinel'], ['step','credential-sentinel'], ['policy','root','--payload','credential-sentinel']):
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                cli.main(['workflow','manage',*words])
            self.assertNotIn('credential-sentinel', err.getvalue())

    def test_existing_agent_parser_is_unchanged(self):
        args = cli.parser().parse_args(['workflow','propose','--current','--task','Task','--reason','Why',
            '--expected-output','Result','--tool','codex','--profile','general','--request-key','explicit'])
        self.assertEqual((args.workflow_command,args.request_key,args.current), ('propose','explicit',True))
        self.assertEqual(cli.parser().parse_args(['workflow','connections','--current']).workflow_command, 'connections')


if __name__ == '__main__':
    unittest.main()
