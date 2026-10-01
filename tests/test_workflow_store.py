from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from agent_console.workflow_store import WorkflowStore


class ResultInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.repo=self.base/'repo';self.repo.mkdir()
        self.store=WorkflowStore(self.base/'state');self.store.migrate()
        self.session={'id':'sess-source','repository':str(self.repo)}

    def tearDown(self): self.temp.cleanup()

    def publish(self,**values):
        return self.store.publish(self.session,**{'kind':'final','outcome':'pass','summary':'Small task complete','checks':['Targeted test passed'],'artifacts':[],'request_key':'request-1','actor':'operator','workspace':self.base,**values})

    def test_one_session_final_needs_no_child_or_review(self):
        result=self.publish()
        self.assertEqual(result['version'],1);self.assertEqual(result['kind'],'final')
        self.assertEqual(self.store.results(self.session['id'])[0]['id'],result['id'])

    def test_publish_deduplicates_retries_and_rejects_conflicting_key(self):
        first=self.publish();self.assertEqual(self.publish()['id'],first['id'])
        with self.assertRaisesRegex(ValueError,'different content'): self.publish(summary='different')
        next_result=self.publish(request_key='request-2',summary='Correction')
        self.assertEqual(next_result['version'],2)
        self.assertEqual(len(self.store.results(self.session['id'])),2)

    def test_parallel_publish_allocates_unique_monotonic_versions(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda n:self.publish(request_key=f'parallel-{n}'),range(12)))
        self.assertEqual(sorted(r['version'] for r in results),list(range(1,13)))

    def test_native_writer_commits_while_status_reader_holds_snapshot(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store.connect() as reader:
                self.assertEqual(reader.execute('SELECT COUNT(*) FROM results').fetchone()[0],0)
                future=pool.submit(self.publish)
                result=future.result(timeout=2)
                self.assertEqual(result['version'],1)
                # The status reader keeps a consistent snapshot while another
                # process commits the final result, then sees it on next read.
                self.assertEqual(reader.execute('SELECT COUNT(*) FROM results').fetchone()[0],0)
        self.assertEqual(len(self.store.results(self.session['id'])),1)

    def test_file_snapshot_is_immutable_and_integrity_checked(self):
        file=self.repo/'answer.txt';file.write_text('Exact candidate')
        result=self.publish(artifacts=[{'path':'answer.txt'}])
        file.write_text('Later content')
        self.assertEqual(self.store.artifact(result['id'],0)[0],b'Exact candidate')
        blob=self.store.objects/result['artifacts'][0]['hash'];blob.write_text('corrupt')
        with self.assertRaisesRegex(ValueError,'integrity'):self.store.artifact(result['id'],0)

    def test_selected_artifact_comes_from_actual_session_worktree(self):
        worktree=self.base/'worktree';worktree.mkdir()
        (self.repo/'answer.txt').write_text('Original repository')
        (worktree/'answer.txt').write_text('Actual session change')
        self.session['worktree']=str(worktree)
        result=self.publish(artifacts=[{'path':'answer.txt'}])
        self.assertEqual(self.store.artifact(result['id'],0)[0],b'Actual session change')

    def test_path_escape_symlink_and_secret_fail_without_publishing(self):
        outside=self.base/'outside';outside.write_text('outside')
        (self.repo/'escape').symlink_to(outside)
        for name in ['../outside','escape',str(outside)]:
            with self.assertRaises((ValueError,OSError)): self.publish(artifacts=[{'path':name}])
        (self.repo/'secret').write_text('ghp_'+'A'*32)
        with self.assertRaisesRegex(ValueError,'values withheld'):self.publish(artifacts=[{'path':'secret'}])
        self.assertEqual(self.store.results(self.session['id']),[])

    def test_commit_snapshot_survives_source_repository_removal(self):
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        (self.repo/'answer.txt').write_text('Committed content')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','fixture'],check=True)
        sha=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        result=self.publish(artifacts=[{'kind':'commit','sha':sha}])
        shutil.rmtree(self.repo)
        data,artifact=self.store.artifact(result['id'],0)
        self.assertEqual(artifact['sha'],sha);self.assertTrue(data.startswith(b'\x1f\x8b'))

    def test_inbox_preserves_source_version_sequence_and_acknowledgments(self):
        result=self.publish(kind='ready')
        item=self.store.send(result['id'],'sess-target',request_key='handoff-1',note='Use this checkpoint',actor='operator')
        same=self.store.send(result['id'],'sess-target',request_key='handoff-1',note='Use this checkpoint',actor='operator')
        self.assertEqual(item['id'],same['id']);self.assertEqual(item['state'],'queued')
        received=self.store.inbox('sess-target')['items'][0]
        self.assertEqual(received['source_session_id'],'sess-source');self.assertEqual(received['result']['version'],1)
        with self.assertRaises(ValueError):self.store.acknowledge(item['id'],'sess-target',state='consumed',actor='recipient')
        with self.assertRaises(KeyError):self.store.acknowledge(item['id'],'wrong-recipient',state='delivered',actor='recipient')
        delivered=self.store.acknowledge(item['id'],'sess-target',state='delivered',actor='recipient')
        consumed=self.store.acknowledge(item['id'],'sess-target',state='consumed',actor='recipient')
        self.assertIsNotNone(delivered['delivered_at']);self.assertIsNotNone(consumed['consumed_at'])
        self.assertEqual(self.store.acknowledge(item['id'],'sess-target',state='delivered',actor='recipient')['state'],'consumed')
        reopened=WorkflowStore(self.base/'state');reopened.migrate()
        self.assertEqual(reopened.inbox('sess-target')['items'][0]['state'],'consumed')

    def test_new_source_version_does_not_rewrite_delivered_history(self):
        first=self.publish();self.store.send(first['id'],'sess-target',request_key='one',actor='operator')
        second=self.publish(request_key='two',summary='New candidate');self.store.send(second['id'],'sess-target',request_key='two',actor='operator')
        inbox=self.store.inbox('sess-target')['items']
        self.assertEqual([item['sequence'] for item in inbox],[1,2])
        self.assertEqual([item['result']['summary'] for item in inbox],['Small task complete','New candidate'])

    def test_future_schema_is_never_relabelled(self):
        with sqlite3.connect(self.store.path) as db:db.execute('UPDATE schema_meta SET version=999')
        with self.assertRaises(ValueError):self.store.migrate()
        with sqlite3.connect(self.store.path) as db:self.assertEqual(db.execute('SELECT version FROM schema_meta').fetchone()[0],999)

    def test_cli_reporting_does_not_initialize_local_writer(self):
        from unittest.mock import patch
        from agent_console.cli import parser
        from agent_console.workflow_cli import run
        import os
        args=parser().parse_args(['workflow','inbox','--current'])
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"items":[]}'
        with patch.dict(os.environ,{'AGENT_CONSOLE_REPORTING_URL':'http://127.0.0.1:3211','AGENT_CONSOLE_SESSION_ID':'sess-fixture','AGENT_CONSOLE_EVIDENCE_CAPABILITY':'private-fixture'}), patch('agent_console.workflow_cli.urllib.request.urlopen',return_value=Response()) as request, patch('agent_console.manager.SessionManager',side_effect=AssertionError('must not create local writer')):
            self.assertEqual(run(args),{'items':[]})
            self.assertEqual(request.call_args.args[0].full_url,'http://127.0.0.1:3211/api/agent-workflow')
