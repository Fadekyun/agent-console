import concurrent.futures
import sqlite3
import time
import unittest
from unittest.mock import patch
import test_web as fixture
from agent_console.resources import ResourceUnavailable


class WebStabilityTests(unittest.TestCase):
    setUp = fixture.WebTests.setUp
    tearDown = fixture.WebTests.tearDown

    def test_resources_auth_and_retryable_launch_rejection(self):
        self.assertEqual(self.client.get('/api/resources').status_code,403)
        self.assertEqual(self.client.get('/api/resources',headers=self.headers).status_code,200)
        with patch('agent_console.manager.require_launch_resources',side_effect=ResourceUnavailable(['host_memory_low'])):
            response=self.client.post('/api/sessions',headers=self.headers,json={'tool':'shell','profile':'general','name':'resource-rejected'})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(response.headers['retry-after'],'30')
        self.assertEqual(response.json()['reason_codes'],['host_memory_low'])
        self.assertFalse(self.manager.tmux.exists('resource-rejected'))

    def test_stopped_resume_same_identity_and_stale_clients_rejected(self):
        session=self.manager.create(tool='shell',profile='general',name='resume-http')
        self.manager.tmux.kill('resume-http')
        endpoint='/api/sessions/resume-http/resume'
        self.assertEqual(self.client.post(endpoint,json={'session_id':session['id']}).status_code,403)
        response=self.client.post(endpoint,headers=self.headers,json={'session_id':session['id']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['id'],session['id'])
        pid=self.manager.tmux.pane_pids('resume-http')
        self.assertEqual(self.client.post(endpoint,headers=self.headers,json={'session_id':session['id']}).status_code,200)
        self.assertEqual(pid,self.manager.tmux.pane_pids('resume-http'))
        self.assertEqual(self.client.post(endpoint,headers=self.headers,json={'session_id':'stale'}).status_code,409)

    def test_workbench_resource_deferral_can_retry_same_request(self):
        request={'tool':'shell','profile':'general','repository':str(self.workspace),'task':'fixture'}
        preview=self.client.post('/api/workbench/launches/preview',headers=self.headers,json={'request':request})
        self.assertEqual(preview.status_code,200,preview.text)
        payload={'request':request,'expected_hash':preview.json()['hash'],'request_key':'resource-retry'}
        with patch('agent_console.manager.require_launch_resources',side_effect=ResourceUnavailable(['host_memory_low'])):
            response=self.client.post('/api/workbench/launches',headers=self.headers,json=payload)
        self.assertEqual(response.status_code,503,response.text)
        retried=self.client.post('/api/workbench/launches',headers=self.headers,json=payload)
        self.assertEqual(retried.status_code,200,retried.text)
        self.assertEqual(retried.json()['state'],'created')

    def test_sqlite_writer_does_not_block_health_and_disconnect_keeps_agent(self):
        self.manager.create(tool='shell',profile='general',name='contention')
        with self.client as client, concurrent.futures.ThreadPoolExecutor(1) as pool:
            def attach():
                try:
                    with client.websocket_connect('/ws/sessions/contention',headers=self.headers) as ws:
                        ws.receive_bytes()
                except Exception:
                    pass
            with self.manager.database.connect() as writer:
                writer.execute('BEGIN IMMEDIATE')
                future=pool.submit(attach)
                time.sleep(.2)
                started=time.monotonic()
                self.assertEqual(client.get('/healthz').status_code,200)
                self.assertLess(time.monotonic()-started,1)
            future.result(timeout=10)
            with client.websocket_connect('/ws/sessions/contention',headers=self.headers) as ws:
                ws.receive_bytes()
                ws.send_text('{"type":"detach"}')
                try: ws.receive_bytes()
                except Exception:pass
        self.assertTrue(self.manager.tmux.exists('contention'))
