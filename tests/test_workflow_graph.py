import json
from pathlib import Path
import tempfile
import unittest
from agent_console.workflow_store import WorkflowStore
from agent_console.workflow_graph import WorkflowGraph


class WorkflowGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.store=WorkflowStore(self.base/'state');self.store.migrate();self.graph=WorkflowGraph(self.store)

    def tearDown(self):self.temp.cleanup()

    def publish(self,session,summary='Done',kind='final',outcome='pass',consume=True):
        if consume:
            for item in self.store.inbox(session)['items']:
                self.store.acknowledge(item['id'],session,state='delivered',actor='test')
                self.store.acknowledge(item['id'],session,state='consumed',actor='test')
        return self.store.publish({'id':session},kind=kind,outcome=outcome,summary=summary,checks=[],artifacts=[],
                                  request_key=str(len(self.store.results(session))+1),actor='test',workspace=self.base)

    def attach(self,owner,target,deps=None):
        return self.graph.attach(owner,target,purpose='A distinct useful task',dependencies=deps or [],
                                 expected_version=self.graph.inspect(owner)['version'],actor='test')

    def edge(self,source,readiness='after-final'):return {'source_id':source,'readiness':readiness}

    def deliver(self,target):
        graph=self.graph.inspect(target)
        return self.graph.deliver(target,expected_version=graph['version'],expected_signature=graph['readiness'][target]['signature'],actor='test')

    def test_ownership_and_input_graph_are_distinct_and_versioned(self):
        graph=self.attach('root','a')
        self.assertEqual(graph['version'],1);self.assertEqual(graph['edges'],[])
        graph=self.attach('root','b',[self.edge('a')])
        node=next(n for n in graph['nodes'] if n['session_id']=='b')
        self.assertEqual(node['owner_id'],'root');self.assertEqual(graph['edges'][0]['source_id'],'a')
        with self.store.connect() as db:
            versions=db.execute('SELECT content_json FROM work_revisions ORDER BY version').fetchall()
        self.assertEqual(len(json.loads(versions[0][0])['nodes']),2)
        reopened=WorkflowGraph(self.store)
        self.assertEqual(reopened.inspect('b')['version'],2)

    def test_join_queues_all_inputs_atomically_and_retries_deduplicate(self):
        self.attach('root','a');self.attach('root','join',[self.edge('root'),self.edge('a','after-ready')])
        self.publish('root')
        with self.assertRaisesRegex(ValueError,'not ready'):self.deliver('join')
        self.assertEqual(self.store.inbox('join')['items'],[])
        self.publish('a',kind='ready')
        first=self.deliver('join');self.assertEqual(self.deliver('join')['id'],first['id'])
        items=self.store.inbox('join')['items'];self.assertEqual(len(items),2)
        self.assertEqual({i['source_session_id'] for i in items},{'root','a'})
        self.assertTrue(all(i['state']=='queued' for i in items))

    def test_cycle_and_stale_edit_rejection_roll_back_without_new_revision(self):
        self.attach('root','a',[self.edge('root')])
        with self.assertRaisesRegex(ValueError,'cycle'):
            self.graph.dependencies('root',dependencies=[self.edge('a')],expected_version=1,actor='test')
        with self.assertRaisesRegex(ValueError,'reload'):
            self.graph.dependencies('a',dependencies=[],expected_version=0,actor='test')
        self.assertEqual(self.graph.inspect('root')['version'],1)

    def test_failed_inputs_block_only_dependents_and_after_final_waits(self):
        self.attach('root','a',[self.edge('root')]);self.attach('root','independent')
        self.publish('root',kind='ready');graph=self.graph.inspect('root')
        self.assertTrue(graph['readiness']['a']['blocked']);self.assertFalse(graph['readiness']['independent']['blocked'])
        self.publish('root',outcome='fail');self.assertTrue(self.graph.inspect('a')['readiness']['a']['blocked'])
        self.publish('root');self.assertFalse(self.graph.inspect('a')['readiness']['a']['blocked'])

    def test_content_changes_stale_descendants_but_ready_final_promotion_does_not(self):
        self.attach('root','a',[self.edge('root','after-ready')]);self.attach('a','b',[self.edge('a')])
        self.publish('root',kind='ready');self.deliver('a');self.publish('a');self.deliver('b')
        self.publish('root',kind='final')
        self.assertFalse(self.graph.inspect('a')['readiness']['a']['stale'])
        self.publish('root',summary='Changed candidate')
        graph=self.graph.inspect('b')
        self.assertTrue(graph['readiness']['a']['stale']);self.assertTrue(graph['readiness']['b']['stale'])
        with self.assertRaisesRegex(ValueError,'not ready'):self.deliver('b')
        # Restoring exact input content can reuse its original receipt, with no duplicate inbox.
        self.publish('root');self.deliver('a')
        self.assertFalse(self.graph.inspect('a')['readiness']['a']['stale'])
        self.assertEqual(len(self.store.inbox('a')['items']),1)

    def test_delivery_does_not_make_old_downstream_output_fresh(self):
        self.attach('root','a',[self.edge('root')]);self.attach('a','b',[self.edge('a')])
        self.publish('root');self.deliver('a');self.publish('a');self.deliver('b')
        self.publish('root',summary='Updated input');self.deliver('a')
        graph=self.graph.inspect('a')
        self.assertTrue(graph['readiness']['a']['result_stale'])
        with self.assertRaisesRegex(ValueError,'not ready'):self.deliver('b')
        self.publish('a',summary='Not acknowledged yet',consume=False)
        self.assertTrue(self.graph.inspect('a')['readiness']['a']['result_stale'])
        self.publish('a',summary='Recomputed for updated input')
        self.assertFalse(self.graph.inspect('a')['readiness']['a']['stale'])
        self.deliver('b')
        self.assertEqual(len(self.store.inbox('b')['items']),2)

    def test_alongside_pins_current_snapshot_including_no_result(self):
        before=self.publish('root',summary='Pinned')
        self.attach('root','a',[self.edge('root','alongside')]);self.attach('a','b',[self.edge('a','alongside')])
        self.publish('root',summary='Later');self.publish('a')
        self.deliver('a');self.deliver('b')
        self.assertEqual(self.store.inbox('a')['items'][0]['result_id'],before['id'])
        self.assertEqual(self.store.inbox('b')['items'],[])

    def test_changed_inputs_between_preview_and_deliver_require_reload(self):
        self.attach('root','a',[self.edge('root')]);self.publish('root');view=self.graph.inspect('a')
        self.publish('root',summary='Changed')
        with self.assertRaisesRegex(ValueError,'inputs changed'):
            self.graph.deliver('a',expected_version=view['version'],expected_signature=view['readiness']['a']['signature'],actor='test')
        self.assertEqual(self.store.inbox('a')['items'],[])

    def test_bad_attach_does_not_leave_partial_graph(self):
        with self.assertRaisesRegex(ValueError,'source'):
            self.attach('root','a',[self.edge('not-connected')])
        self.assertEqual(self.graph.inspect('root')['version'],0)
        self.attach('root','a')
        with self.assertRaisesRegex(ValueError,'already belongs'):self.attach('other','a')
        self.assertEqual(self.graph.inspect('other')['version'],0)
