"""Disposable contract/process/persistence evidence. Never starts Console manager."""
import concurrent.futures
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_console.device_presence import (Clock, DEVICE, GET, POST, Receiver, PrivateStore,
                                            Rejected, Unavailable, encode, utc, payload)
from agent_console.presence_service import Authority, serve, request

DUMMY = 'FixtureOnlyPresenceWriter00000000000001'


class Sample:
    def __init__(self, wall=1789311600.):
        self.wall, self.mono = wall, 100.
        self.clock = Clock(lambda: self.wall, lambda: self.mono)
    def step(self, amount):
        self.wall += amount
        self.mono += amount


def client_job(directory, generation, raw):
    return request(directory, generation, 'write', raw, DUMMY)


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)
        credential = self.directory/'writer.sha256'
        credential.write_text(hashlib.sha256(DUMMY.encode()).hexdigest())
        credential.chmod(0o600)
        self.store = PrivateStore(self.directory)
        self.sample = Sample()
        self.receiver = Receiver(self.store, self.sample.clock)
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def raw(self, state='online', observed=None):
        return encode(dict(device_id=DEVICE, state=state, observed_at=utc(self.sample.wall if observed is None else observed)))
    def test_unchanged_vectors_exact_initial_and_duplicate_ack(self):
        vectors = json.loads((Path(__file__).parent/'fixtures/presence-wire-vectors.json').read_bytes())
        for vector in vectors:
            with self.subTest(vector=vector['payload']['state']):
                raw = encode(vector['payload'])
                self.assertEqual(raw.decode(), vector['canonical_utf8'])
                self.assertEqual(payload(raw)[2], vector['payload_sha256'])
                sample = Sample(payload(raw)[1])
                with tempfile.TemporaryDirectory() as tmp:
                    p = Path(tmp);p.chmod(0o700)
                    credential = p/'writer.sha256';credential.write_text(hashlib.sha256(DUMMY.encode()).hexdigest());credential.chmod(0o600)
                    store = PrivateStore(p)
                    try:
                        receiver = Receiver(store, sample.clock)
                        sample.step(.5)
                        self.assertEqual(receiver.post(raw, DUMMY), vector['ack_initial'])
                        end = receiver.current['mono_end']
                        sample.step(1)
                        self.assertEqual(receiver.post(raw, DUMMY), vector['ack_duplicate'])
                        self.assertEqual(receiver.current['mono_end'], end)
                    finally:store.close()
    def test_restart_ordering_future_duplicate_and_expiry(self):
        raw = self.raw();ack = self.receiver.post(raw, DUMMY)
        original = (self.directory/'ordering.json').read_bytes()
        restarted = Receiver(self.store, self.sample.clock)
        self.assertEqual(restarted.get()['status'], 'Status unavailable')
        with self.assertRaises(Rejected) as failure:restarted.post(raw, DUMMY)
        self.assertEqual(failure.exception.status, 409)
        self.sample.step(1)
        self.assertTrue(restarted.post(self.raw('offline'), DUMMY)['accepted'])
        self.assertEqual(restarted.get()['status'], 'Offline')
        with self.assertRaises(Rejected):restarted.post(self.raw(observed=self.sample.wall+.000001), DUMMY)
        self.sample.step(180)
        self.assertEqual(restarted.get()['status'], 'Status unavailable')
        with self.assertRaises(Rejected):restarted.post(raw, DUMMY)
        self.assertNotEqual(original, (self.directory/'ordering.json').read_bytes())
    def test_monotonic_expiry_independent_of_wall_and_rollback_latches(self):
        raw = self.raw();self.receiver.post(raw, DUMMY)
        # Smaller wall advance is within documented sample tolerance.
        self.sample.mono += 180
        self.sample.wall += 179.9
        self.assertEqual(self.receiver.get()['status'], 'Status unavailable')
        with self.assertRaises(Rejected):self.receiver.post(raw, DUMMY)
        self.sample.wall -= 1
        self.assertEqual(self.receiver.get()['status'], 'Status unavailable')
        self.sample.step(2)
        with self.assertRaises(Unavailable):self.receiver.post(self.raw(), DUMMY)
    def test_no_ack_on_persistence_failure_or_ordering_tamper(self):
        with patch.object(self.store, 'write', side_effect=OSError('fixture')):
            with self.assertRaises(Unavailable):self.receiver.post(self.raw(), DUMMY)
        self.assertEqual(self.receiver.get()['status'], 'Status unavailable')
        with self.assertRaises(Unavailable):self.receiver.post(self.raw(), DUMMY)
    def test_strict_decode_and_no_follow_regular_before_read(self):
        for raw in [b'{"state":"online","state":"offline"}', b'{"observed_at":true}', self.raw().replace(b'"online"', b'null')]:
            with self.assertRaises(Rejected):self.receiver.post(raw, DUMMY)
        target = self.directory/'ordering.json'
        os.mkfifo(target, 0o600)
        with self.assertRaises(Unavailable):self.store.read('ordering.json')
        target.unlink();target.symlink_to(self.directory/'writer.sha256')
        with self.assertRaises(OSError):self.store.read('ordering.json')
    def test_read_has_no_persistence_writes(self):
        self.receiver.post(self.raw(), DUMMY)
        p = self.directory/'ordering.json';before = p.stat()
        with patch.object(self.store, 'write', side_effect=AssertionError('read wrote')):
            self.assertEqual(self.receiver.get()['status'], 'Online')
        self.assertEqual((before.st_ino,before.st_size,before.st_mtime_ns), (p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns))
    def start(self, generation):
        context = multiprocessing.get_context('spawn')
        read, write = context.Pipe(duplex=False)
        child = context.Process(target=serve, args=(str(self.directory), generation, write))
        child.start();write.close()
        self.assertTrue(read.poll(3));self.assertIs(read.recv(), True);read.close()
        self.addCleanup(self.stop, child)
        return child
    def stop(self, child):
        if child.is_alive():child.terminate()
        child.join(2)
        if child.is_alive():child.kill();child.join(1)
        self.assertFalse(child.is_alive())
    def test_real_authority_process_concurrent_clients_singleton_and_restart(self):
        generation = secrets.token_hex(32);child = self.start(generation)
        raw = encode(dict(device_id=DEVICE,state='online',observed_at=utc(time.time()))).decode()
        with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(client_job, [str(self.directory)]*4, [generation]*4, [raw]*4))
        self.assertTrue(all(v['status']==200 for v in results))
        self.assertEqual(sum(v['body']['duplicate'] is False for v in results),1)
        acks = [{k:v for k,v in value['body'].items() if k!='duplicate'} for value in results]
        self.assertTrue(all(v==acks[0] for v in acks))
        with self.assertRaises(BlockingIOError):Authority(self.directory, secrets.token_hex(32))
        self.assertEqual(request(self.directory, generation, 'read')['body']['status'],'Online')
        self.stop(child)
        next_generation = secrets.token_hex(32);self.start(next_generation)
        self.assertEqual(request(self.directory,next_generation,'read')['body']['status'],'Status unavailable')
        self.assertEqual(request(self.directory,next_generation,'write',raw,DUMMY)['status'],409)
        self.assertEqual(request(self.directory,generation,'read')['status'],503)
        newer=encode(dict(device_id=DEVICE,state='offline',observed_at=utc(time.time()))).decode()
        self.assertEqual(request(self.directory,next_generation,'write',newer,DUMMY)['status'],200)
    def test_routes_scoped_writer_cannot_authorize_identity_or_other_namespaces(self):
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.testclient import TestClient
        from agent_console.presence_routes import install
        app = FastAPI()
        def identity(request: Request):
            if request.headers.get('test-identity') != 'dummy-user':raise HTTPException(403)
        install(app, identity, directory=str(self.directory), generation='not-running')
        @app.get('/api/sessions')
        def forbidden():raise AssertionError('normal route reached with presence token')
        with TestClient(app) as client:
            self.assertEqual(client.get(GET,headers={'x-agc-presence-writer':DUMMY}).status_code,403)
            self.assertEqual(client.get('/api/sessions',headers={'x-agc-presence-writer':DUMMY}).status_code,403)
            self.assertEqual(client.get(GET).status_code,403)
            self.assertEqual(client.get(GET,headers={'test-identity':'dummy-user'}).status_code,503)
            self.assertEqual(client.post(POST,content=self.raw(),headers={'content-type':'application/json'}).status_code,403)
            self.assertEqual(client.post(POST,content=self.raw(),headers=[('x-agc-presence-writer',DUMMY),('x-agc-presence-writer',DUMMY),('content-type','application/json')]).status_code,403)

    def test_real_private_write_failure_and_durable_ack_loss(self):
        self.directory.chmod(0o500)
        try:
            with self.assertRaises(Unavailable):self.receiver.post(self.raw(), DUMMY)
            self.assertEqual(self.receiver.get()['status'],'Status unavailable')
        finally:self.directory.chmod(0o700)
        sample = Sample();receiver = Receiver(self.store,sample.clock)
        original = self.store.write
        def committed_then_failed(*args):
            original(*args)
            raise OSError('fixture directory-fsync/ack uncertainty')
        with patch.object(self.store,'write',side_effect=committed_then_failed):
            with self.assertRaises(Unavailable):receiver.post(self.raw(),DUMMY)
        restarted=Receiver(self.store,sample.clock)
        with self.assertRaises(Rejected) as failure:restarted.post(self.raw(),DUMMY)
        self.assertEqual(failure.exception.status,409)
        self.assertEqual(restarted.get()['status'],'Status unavailable')

    def test_authoritative_master_launcher_and_app_factories_share_lifetime(self):
        from agent_console import presence_server
        from agent_console.presence_routes import install
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.testclient import TestClient
        first_ack = None;raw = None
        def identity(request: Request):
            if request.headers.get('test-identity')!='dummy-user':raise HTTPException(403)
        def fake_web_run(*args,**kwargs):
            nonlocal first_ack,raw
            generation=os.environ['AGCONSOLE_PRESENCE_GENERATION']
            self.assertEqual(kwargs['workers'],2)
            raw=encode(dict(device_id=DEVICE,state='online',observed_at=utc(time.time())))
            for index in range(2):
                # App recreation is NOT authority restart. Each uses same live process.
                app=FastAPI();install(app,identity,directory=str(self.directory),generation=generation)
                with TestClient(app) as client:
                    response=client.post(POST,content=raw,headers={'x-agc-presence-writer':DUMMY,'content-type':'application/json'})
                    self.assertEqual(response.status_code,200);ack=response.json()
                    if index==0:first_ack=ack;self.assertFalse(ack['duplicate'])
                    else:self.assertEqual(ack,{**first_ack,'duplicate':True})
                    self.assertEqual(client.get(GET,headers={'test-identity':'dummy-user'}).json()['status'],'Online')
        def after_restart(*args,**kwargs):
            generation=os.environ['AGCONSOLE_PRESENCE_GENERATION']
            self.assertEqual(request(self.directory,generation,'read')['body']['status'],'Status unavailable')
            self.assertEqual(request(self.directory,generation,'write',raw.decode(),DUMMY)['status'],409)
        import sys
        with patch.object(presence_server,'bootstrap_console_state'),patch.dict(os.environ,{'AGCONSOLE_DEVICE_PRESENCE':'1','AGCONSOLE_DEVICE_PRESENCE_DIR':str(self.directory)}),patch.object(sys,'argv',['presence_server','--host','127.0.0.1','--port','1','--workers','2']):
            with patch('uvicorn.run',side_effect=fake_web_run):presence_server.main()
            self.assertNotIn('AGCONSOLE_PRESENCE_GENERATION',os.environ)
            self.assertFalse((self.directory/'receiver.sock').exists())
            with patch('uvicorn.run',side_effect=after_restart):presence_server.main()
        self.assertFalse((self.directory/'receiver.sock').exists())

    def test_startup_rejects_bad_private_ordering_and_wrong_generation(self):
        target=self.directory/'ordering.json'
        target.write_text('{"observed_at":true,"payload_sha256":"'+'0'*64+'"}')
        target.chmod(0o600)
        with self.assertRaises(Rejected):Receiver(self.store,self.sample.clock)
        with self.assertRaises(Rejected):Authority(self.directory,secrets.token_hex(32))
        self.assertFalse((self.directory/'receiver.sock').exists())

    def assert_safe_projection(self, value, status):
        self.assertEqual(set(value), {'project_id','status','observed_at','expires_at','meaning'})
        self.assertEqual(value['status'], status)
        encoded=json.dumps(value)
        for private in (DEVICE,DUMMY,'100.111.183.86','shop-kiosk-01','device_id','payload_sha256'):
            self.assertNotIn(private,encoded)

    def test_unknown_supersedes_online_immutable_ack_replay_restart_and_recovery(self):
        vector=json.loads((Path(__file__).parent/'fixtures/presence-unknown-vector.json').read_bytes())
        online=self.raw();self.receiver.post(online,DUMMY)
        self.assert_safe_projection(self.receiver.get(),'Online')
        self.sample.step(1)
        unknown=self.raw('unknown');self.assertEqual(unknown.decode(),vector['canonical_utf8'])
        self.sample.step(.5)
        first=self.receiver.post(unknown,DUMMY)
        self.assertEqual(first,vector['ack_initial'])
        self.assert_safe_projection(self.receiver.get(),'Status unavailable')
        deadline=self.receiver.current['mono_end'];ordering=(self.directory/'ordering.json').read_bytes()
        self.sample.step(1)
        self.assertEqual(self.receiver.post(unknown,DUMMY),vector['ack_duplicate'])
        self.assertEqual(self.receiver.current['mono_end'],deadline)
        self.assertEqual((self.directory/'ordering.json').read_bytes(),ordering)
        with self.assertRaises(Rejected) as stale:self.receiver.post(online,DUMMY)
        self.assertEqual(stale.exception.status,409)
        restarted=Receiver(self.store,self.sample.clock)
        self.assert_safe_projection(restarted.get(),'Status unavailable')
        with self.assertRaises(Rejected) as replay:restarted.post(unknown,DUMMY)
        self.assertEqual(replay.exception.status,409)
        self.sample.step(1)
        self.assertTrue(restarted.post(self.raw('offline'),DUMMY)['accepted'])
        self.assert_safe_projection(restarted.get(),'Offline')
        self.sample.step(180)
        self.assert_safe_projection(restarted.get(),'Status unavailable')
        with self.assertRaises(Rejected):restarted.post(unknown,DUMMY)

    def test_actual_get_redacts_private_identity_for_all_states_restart_and_failure(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent_console.presence_routes import install
        generation=secrets.token_hex(32);child=self.start(generation)
        app=FastAPI();install(app,lambda:None,directory=str(self.directory),generation=generation)
        def read(client,expected):
            response=client.get(GET);self.assertEqual(response.status_code,200)
            self.assert_safe_projection(response.json(),expected)
        with TestClient(app) as client:
            read(client,'Status unavailable')
            for state,expected in [('online','Online'),('offline','Offline'),('unknown','Status unavailable')]:
                raw=encode({'device_id':DEVICE,'state':state,'observed_at':utc(time.time())})
                response=client.post(POST,content=raw,headers={'X-AGC-Presence-Writer':DUMMY,'Content-Type':'application/json'})
                self.assertEqual(response.status_code,200);self.assertEqual(response.json()['state'],state)
                read(client,expected)
            self.stop(child)
        generation2=secrets.token_hex(32);self.start(generation2)
        restarted=FastAPI();install(restarted,lambda:None,directory=str(self.directory),generation=generation2)
        with TestClient(restarted) as client:
            read(client,'Status unavailable')
            # A private persisted-ordering failure gives the same safe read shape.
            (self.directory/'ordering.json').write_text('{}')
            read(client,'Status unavailable')
