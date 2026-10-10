import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_integration_requests import IntegrationRequestTests, REQUEST_ID, OWNER, CHANNEL
from agent_console.integration_requests import IntegrationError, IntegrationService
from agent_console.artifact_review import parse_request
from agent_console.task_runner import _prepare_provider, _schema, _validate_final, run_request


class ArtifactReviewTests(unittest.TestCase):
    setUp = IntegrationRequestTests.setUp
    tearDown = IntegrationRequestTests.tearDown
    write_config = IntegrationRequestTests.write_config
    row = IntegrationRequestTests.row
    fake_argv = IntegrationRequestTests.fake_argv

    def prepare(self):
        config_path = self.manager.auth.config_dir / 'plan-integration.json'
        config = json.loads(config_path.read_text())
        config['provider']['tool'] = 'codex-pro'
        config['projects']['agc'] = config['projects'].pop('n100')
        path = config_path.with_name('review-integration.json')
        path.write_text(json.dumps(config)); path.chmod(0o600)
        auth = self.manager.auth.codex_home('default', tool='codex-pro')
        auth.mkdir(parents=True, exist_ok=True)
        (auth / 'auth.json').write_text('{}')
        bundle = self.context_dir / 'release-55'
        (bundle / 'context').mkdir(parents=True)
        (bundle / 'context' / 'checks.txt').write_text('Exact release evidence')
        (bundle / 'package.tar.gz').write_bytes(b'package test fixture')
        (bundle / 'rollout.py').write_bytes(b'print("do not run")')
        binding = dict(source_sha='a'*40, package_sha256=hashlib.sha256((bundle/'package.tar.gz').read_bytes()).hexdigest(),
                       rollout_sha256=hashlib.sha256((bundle/'rollout.py').read_bytes()).hexdigest(), target='shop-kiosk-01', bundle_id='release-55')
        request = dict(request_id=REQUEST_ID, requester_id=OWNER, channel_id=CHANNEL, project='agc', text='Review update', review=binding)
        self.service = IntegrationService(self.manager, review=True)
        return bundle, request

    def accept(self, request):
        with patch.object(self.manager.tmux, 'create') as create:
            response, code = self.service.submit(json.dumps(request).encode())
        self.assertEqual(code, 0); self.assertTrue(response['accepted']); create.assert_called_once()
        return self.row()

    def test_actual_files_frozen_model_pinned_and_request_idempotent(self):
        bundle, request = self.prepare()
        row = self.accept(request)
        frozen = Path(row['artifact_dir'])/'context'
        self.assertEqual((frozen/'package.tar.gz').read_bytes(), (bundle/'package.tar.gz').read_bytes())
        (bundle/'rollout.py').write_text('changed')
        self.assertNotEqual((frozen/'rollout.py').read_bytes(), (bundle/'rollout.py').read_bytes())
        argv, _, _ = _prepare_provider(self.settings, row)
        self.assertIn('model="gpt-6-astra"', argv); self.assertIn('model_reasoning_effort="low"', argv)
        self.assertIn('read-only', argv); self.assertIn('approval_policy="never"', argv)
        with patch.object(self.manager.tmux,'create') as create, patch.object(self.manager.tmux,'exists',return_value=True):
            response, _ = self.service.submit(json.dumps(request).encode())
        self.assertTrue(response['duplicate']); create.assert_not_called()
        request['review']['source_sha'] = 'b'*40
        response, code = self.service.submit(json.dumps(request).encode())
        self.assertEqual(response['reason_code'], 'idempotency_conflict'); self.assertNotEqual(code,0)

    def test_mismatched_package_never_launches(self):
        bundle, request = self.prepare()
        (bundle/'package.tar.gz').write_bytes(b'tamper')
        with patch.object(self.manager.tmux,'create') as create, self.assertRaises(IntegrationError):
            self.service.submit(json.dumps(request).encode())
        create.assert_not_called()

    def test_wrong_target_traversal_and_symlink_rejected(self):
        bundle, request = self.prepare()
        for key, value in [('target','other'),('bundle_id','../release-55')]:
            bad = {**request, 'review': {**request['review'], key:value}}
            with self.assertRaises(IntegrationError): parse_request(json.dumps(bad).encode())
        (bundle/'package.tar.gz').unlink(); (bundle/'package.tar.gz').symlink_to('/etc/hosts')
        with patch.object(self.manager.tmux,'create') as create, self.assertRaises(OSError):
            self.service.submit(json.dumps(request).encode())
        create.assert_not_called()

    def test_strict_verdict_binding_and_evidence(self):
        _, request = self.prepare(); binding=request['review']
        final=dict(outcome='approved', summary='checked', steps=['rollout.py'], verification=['digest and rollback'], blockers=[], review=binding)
        self.assertEqual(_validate_final(final,binding),final)
        self.assertEqual(_schema(binding)['properties']['outcome']['enum'],['approved','rejected','needs_input'])
        for bad in [{**final,'review':{**binding,'source_sha':'b'*40}}, {**final,'blockers':['unsafe']}, {**final,'verification':[]}]:
            with self.assertRaises(ValueError): _validate_final(bad,binding)

    def test_native_runner_returns_bound_review_without_interactive_submit(self):
        _, request = self.prepare(); row=self.accept(request)
        final=dict(outcome='approved', summary='checked', steps=['rollout.py'], verification=['digest and rollback'], blockers=[], review=request['review'])
        rc=run_request(row['id'],settings=self.settings,argv_override=self.fake_argv(final=final),env_override={},ack_timeout=2,run_timeout=5)
        self.assertEqual(rc,0)
        response, code=self.service.status(json.dumps(dict(request_id=REQUEST_ID,requester_id=OWNER,channel_id=CHANNEL)).encode())
        self.assertEqual(code,0); self.assertEqual(response['state'],'completed'); self.assertEqual(response['result'],final)
        self.assertEqual(response['model'],'gpt-6-astra'); self.assertEqual(response['session_id'],row['session_id'])

    def test_frozen_tamper_fails_before_provider_execution(self):
        _, request = self.prepare(); row = self.accept(request)
        frozen = Path(row['artifact_dir'])/'context'/'rollout.py'
        frozen.chmod(0o600); frozen.write_text('modified after freeze')
        with self.assertRaises(IntegrationError): _prepare_provider(self.settings,row)

    def test_rejected_review_completes_without_approval(self):
        _, request = self.prepare(); row=self.accept(request)
        final=dict(outcome='rejected',summary='unsafe',steps=['rollout.py'],verification=['rollback missing'],blockers=['rollback'],review=request['review'])
        self.assertEqual(run_request(row['id'],settings=self.settings,argv_override=self.fake_argv(final=final),env_override={},ack_timeout=2,run_timeout=5),0)
        response,_=self.service.status(json.dumps(dict(request_id=REQUEST_ID,requester_id=OWNER,channel_id=CHANNEL)).encode())
        self.assertEqual(response['state'],'completed'); self.assertEqual(response['result']['outcome'],'rejected')
        frozen=Path(row['artifact_dir'])/'context'/'package.tar.gz'
        frozen.chmod(0o600); frozen.write_bytes(b'tampered')
        with self.assertRaises(IntegrationError):
            self.service.status(json.dumps(dict(request_id=REQUEST_ID,requester_id=OWNER,channel_id=CHANNEL)).encode())

    def test_fifo_artifacts_rejected_without_blocking(self):
        import subprocess
        import sys
        bundle, request = self.prepare()
        for name in ('package.tar.gz', 'rollout.py'):
            path=bundle/name
            original=path.read_bytes()
            path.unlink(); os.mkfifo(path)
            for function in ('freeze_bundle','verify_frozen'):
                destination=Path(self.temp.name)/('fifo-'+function+'-'+name)
                destination.mkdir()
                script=("from pathlib import Path; from agent_console.artifact_review import " + function +
                        "; from agent_console.integration_requests import IntegrationError; import json; " +
                        "binding=json.loads("+repr(json.dumps(request['review']))+"); " +
                        "\ntry: "+function+"(Path("+repr(str(bundle))+")"+
                        (",Path("+repr(str(destination))+")" if function=='freeze_bundle' else '')+
                        ",binding)\nexcept IntegrationError: raise SystemExit(0)\nraise SystemExit(1)")
                result=subprocess.run([sys.executable,'-c',script],timeout=2,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr.decode())
            path.unlink(); path.write_bytes(original)

    def test_native_progress_message_then_final_review(self):
        _, request = self.prepare(); row=self.accept(request)
        final=dict(outcome='approved',summary='checked',steps=['rollout.py'],verification=['digests'],blockers=[],review=request['review'])
        events=[{'type':'thread.started','thread_id':'t'}, {'type':'turn.started'},
                {'type':'item.completed','item':{'id':'item_0','type':'agent_message','text':'I will inspect the package and rollback procedure.'}},
                {'type':'item.completed','item':{'id':'item_1','type':'agent_message','text':json.dumps(final)}},
                {'type':'turn.completed'}]
        rc=run_request(row['id'],settings=self.settings,argv_override=self.fake_argv(events=events),env_override={},ack_timeout=2,run_timeout=5)
        self.assertEqual(rc,0)
        self.assertEqual(self.service.result(REQUEST_ID),final)

    def test_earlier_json_does_not_approve_malformed_last_message(self):
        _, request=self.prepare(); row=self.accept(request)
        final=dict(outcome='approved',summary='checked',steps=['rollout.py'],verification=['digests'],blockers=[],review=request['review'])
        events=[{'type':'thread.started','thread_id':'t'},{'type':'turn.started'},
                {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}},
                {'type':'item.completed','item':{'type':'agent_message','text':'not a valid final result'}},
                {'type':'turn.completed'}]
        rc=run_request(row['id'],settings=self.settings,argv_override=self.fake_argv(events=events),env_override={},ack_timeout=2,run_timeout=5)
        self.assertEqual(rc,1); self.assertEqual(self.row()['reason_code'],'invalid_final_output')

    def test_native_final_file_after_progress_agrees_with_final_message(self):
        _, request=self.prepare(); row=self.accept(request)
        final=dict(outcome='approved',summary='checked',steps=['rollout.py'],verification=['digests'],blockers=[],review=request['review'])
        events=[{'type':'thread.started','thread_id':'t'},{'type':'turn.started'},
                {'type':'item.completed','item':{'type':'agent_message','text':'Inspecting the package now.'}},
                {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}},
                {'type':'turn.completed'}]
        argv=self.fake_argv(events=events)
        path=Path(row['artifact_dir'])/'provider-final.txt'
        argv[-1]=argv[-1].replace('raise SystemExit(0)', 'open('+repr(str(path))+',"w").write('+repr(json.dumps(final))+'); raise SystemExit(0)')
        self.assertEqual(run_request(row['id'],settings=self.settings,argv_override=argv,env_override={},ack_timeout=2,run_timeout=5),0)
        self.assertEqual(self.service.result(REQUEST_ID),final)

    def test_malformed_native_final_file_cannot_fall_back_to_earlier_approval(self):
        _, request=self.prepare(); row=self.accept(request)
        final=dict(outcome='approved',summary='checked',steps=['rollout.py'],verification=['digests'],blockers=[],review=request['review'])
        argv=self.fake_argv(final=final)
        path=Path(row['artifact_dir'])/'provider-final.txt'
        argv[-1]=argv[-1].replace('raise SystemExit(0)', 'open('+repr(str(path))+',"w").write("invalid final"); raise SystemExit(0)')
        self.assertEqual(run_request(row['id'],settings=self.settings,argv_override=argv,env_override={},ack_timeout=2,run_timeout=5),1)
        self.assertEqual(self.row()['reason_code'],'invalid_final_output')

    def test_commentary_only_without_final_result_still_fails(self):
        _, request=self.prepare(); row=self.accept(request)
        events=[{'type':'thread.started','thread_id':'t'},{'type':'turn.started'},
                {'type':'item.completed','item':{'type':'agent_message','text':'Inspecting the package.'}},
                {'type':'turn.completed'}]
        self.assertEqual(run_request(row['id'],settings=self.settings,argv_override=self.fake_argv(events=events),env_override={},ack_timeout=2,run_timeout=5),1)
        self.assertEqual(self.row()['reason_code'],'invalid_final_output')

    def test_conflicting_valid_native_final_file_and_last_message_fail(self):
        _, request=self.prepare(); row=self.accept(request)
        final=dict(outcome='approved',summary='checked',steps=['rollout.py'],verification=['digests'],blockers=[],review=request['review'])
        argv=self.fake_argv(final={**final,'outcome':'rejected','blockers':['unsafe']})
        path=Path(row['artifact_dir'])/'provider-final.txt'
        argv[-1]=argv[-1].replace('raise SystemExit(0)', 'open('+repr(str(path))+',"w").write('+repr(json.dumps(final))+'); raise SystemExit(0)')
        self.assertEqual(run_request(row['id'],settings=self.settings,argv_override=argv,env_override={},ack_timeout=2,run_timeout=5),1)
        self.assertEqual(self.row()['reason_code'],'invalid_final_output')

    def test_preexisting_native_final_file_rejected_before_spawn(self):
        _, request=self.prepare(); row=self.accept(request)
        (Path(row['artifact_dir'])/'provider-final.txt').write_text('{}')
        with self.assertRaises(RuntimeError): _prepare_provider(self.settings,row)

    def test_invalid_streamed_final_cannot_use_valid_file(self):
        _, request=self.prepare(); row=self.accept(request)
        final=dict(outcome='approved',summary='checked',steps=['rollout.py'],verification=['digests'],blockers=[],review=request['review'])
        events=[{'type':'thread.started','thread_id':'t'},{'type':'turn.started'},
                {'type':'item.completed','item':{'type':'agent_message','text':None}},
                {'type':'turn.completed'}]
        argv=self.fake_argv(events=events)
        path=Path(row['artifact_dir'])/'provider-final.txt'
        argv[-1]=argv[-1].replace('raise SystemExit(0)', 'open('+repr(str(path))+',"w").write('+repr(json.dumps(final))+'); raise SystemExit(0)')
        self.assertEqual(run_request(row['id'],settings=self.settings,argv_override=argv,env_override={},ack_timeout=2,run_timeout=5),1)
        self.assertEqual(self.row()['reason_code'],'invalid_final_output')
