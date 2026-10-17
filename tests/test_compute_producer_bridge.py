import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_console import compute_producer_bridge as bridge


class ProducerBridgeTests(unittest.TestCase):
    def request(self, path='/jobs/job_123', method='GET', **extra):
        return {'version': 1, 'method': method, 'path': bridge.PREFIX + path, **extra}

    def test_exact_routes_and_methods_only(self):
        routes = [('GET', '/jobs/job-123'), ('GET', '/publications/receipt_123'),
                  ('GET', '/jobs/job/artifacts/' + 'a'*64 + '?offset=0&length=262144'),
                  ('POST', '/jobs'), ('POST', '/artifacts/' + 'b'*64),
                  ('POST', '/jobs/job/publication'), ('POST', '/jobs/job/consume'),
                  ('POST', '/publications/receipt/finish')]
        for method, route in routes:
            bridge.validate_request(self.request(route, method, **({'body': {}} if method == 'POST' else {})))
        for route in ['/../hold', '/jobs/job/retry', '/jobs/job/cancel', '/jobs//id',
                      '/jobs/%2e%2e', '/jobs/x?header=secret', '/jobs/x\r\nHost: evil',
                      '//evil.test/path', '/jobs/a/artifacts/'+'a'*64+'?offset=0&length=262145',
                      '/jobs/a/artifacts/'+'a'*64+'?offset=0&offset=1',
                      '/jobs/a/artifacts/'+'a'*64+'?offset=0&length=1&x=1']:
            with self.subTest(route=route), self.assertRaises(ValueError):
                bridge.validate_request(self.request(route))
        for extra in [{'headers': {'authorization': 'forged'}}, {'version': True}, {'method': 'DELETE'}, {'body': {}}]:
            value = self.request()
            value.update(extra)
            with self.assertRaises(ValueError):
                bridge.validate_request(value)
        with self.assertRaises(ValueError):
            bridge.validate_request(self.request('/jobs', 'POST', body={'data': 'x'*bridge.MAX_REQUEST}))

    def test_bridge_uses_fixed_loopback_and_its_own_identity(self):
        config = {'producer': 'ct106-points', 'token': 't'*40}
        response = {'status': 409, 'body': {'detail': 'unknown'}}
        with patch.object(bridge, 'http_call', return_value=response) as call:
            self.assertEqual(bridge.bridge(self.request(), config), response)
            endpoint, request, headers = call.call_args.args
            self.assertEqual(endpoint, 'http://192.168.1.115:3210')
            self.assertEqual(call.call_args.kwargs, {'local_bridge': True})
            self.assertEqual(headers['Authorization'], 'Bearer ' + 't'*40)
            self.assertEqual(headers['X-Compute-Producer'], 'ct106-points')
            self.assertEqual(set(headers), {'Authorization', 'X-Compute-Producer', 'Content-Type', 'Accept'})
            with self.assertRaises(ValueError):
                bridge.bridge(self.request(), {**config, 'endpoint': 'https://evil.test'})
        with self.assertRaises(ValueError):
            bridge.credentials({'producer': 'p', 'token': 'a'*32 + '\r\nX: injected'})

    def test_local_bridge_requires_fresh_kernel_loopback_route_and_bound_socket(self):
        route = {'type': 'local', 'dst': '192.168.1.115', 'from': '127.0.0.1',
                 'dev': 'lo', 'table': 'local'}
        result = Mock(returncode=0, stdout=json.dumps([route]).encode())
        connection = Mock()
        connection.sock.getsockname.return_value = ('127.0.0.1', 40000)
        connection.sock.getpeername.return_value = ('192.168.1.115', 3210)
        with patch.object(bridge.subprocess, 'run', return_value=result) as probe, \
                patch.object(bridge.http.client, 'HTTPConnection', return_value=connection) as factory:
            self.assertIs(bridge.local_bridge_connection(), connection)
            factory.assert_called_once_with('192.168.1.115', 3210, timeout=bridge.SECONDS,
                                            source_address=('127.0.0.1', 0))
            self.assertEqual(probe.call_args.args[0],
                ['/usr/bin/ip', '-j', 'route', 'get', '192.168.1.115', 'from', '127.0.0.1'])
            self.assertEqual(probe.call_args.kwargs['timeout'], 2)
            for changes in ({'dev': 'eth0'}, {'type': 'unicast'}, {'dst': '192.168.1.116'},
                            {'from': '192.168.1.115'}, {'table': 'main'}):
                result.stdout = json.dumps([{**route, **changes}]).encode()
                with self.assertRaises(bridge.TransportError):
                    bridge.local_bridge_connection()
            result.stdout = json.dumps([route]).encode()
            connection.sock.getsockname.return_value = ('192.168.1.115', 40000)
            with self.assertRaises(bridge.TransportError):
                bridge.local_bridge_connection()
            connection.close.assert_called_once()
            # Every invocation re-reads kernel routing, even rejected sockets.
            self.assertEqual(probe.call_count, 7)

    def test_http_preserves_status_and_never_follows_redirect(self):
        connection = Mock()
        response = Mock(status=307)
        response.read1.side_effect = [b'{"detail":"redirect denied"}', b'']
        connection.getresponse.return_value = response
        with patch.object(bridge.http.client, 'HTTPConnection', return_value=connection) as constructor:
            result = bridge.http_call('http://127.0.0.1:3210', self.request(), {})
            self.assertEqual(result['status'], 307)
            constructor.assert_called_once_with('127.0.0.1', 3210, timeout=bridge.SECONDS)
            connection.request.assert_called_once()
            connection.close.assert_called_once()
        for url in ['http://192.168.1.115:3210', 'https://user:pass@example.test', 'https://example.test/api', 'file:///x']:
            with self.assertRaises(ValueError):
                bridge.http_call(url, self.request(), {})

    def test_http_response_size_limit(self):
        connection = Mock()
        response = Mock(status=200)
        response.read1.side_effect = [b'x'*65536]*17
        connection.getresponse.return_value = response
        with patch.object(bridge.http.client, 'HTTPConnection', return_value=connection):
            with self.assertRaises(bridge.TransportError):
                bridge.http_call('http://127.0.0.1:3210', self.request(), {})

    def test_duplicate_json_and_unsafe_config_rejected(self):
        for data in [b'{"version":1,"version":2}', b'{"a":NaN}', b'[]']:
            with self.assertRaises(ValueError):
                bridge.decode(data)
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            config.write_text('{"producer":"p","token":"secret"}')
            # /tmp is writable, so caller-created configuration is never trusted.
            with self.assertRaises(ValueError):
                bridge.load_config(str(config))

    def test_ssh_client_no_command_forwarding_secret_or_environment(self):
        config = {'transport': 'ssh-stdio', 'ssh_host': 'points-producer@192.168.1.115',
                  'ssh_identity_file': '/etc/points/key', 'ssh_known_hosts_file': '/etc/points/known_hosts'}
        with patch.object(bridge, 'protected_file', return_value=b'protected'):
            client = bridge.ProducerClient(config)
            self.assertEqual(client.command[-1], config['ssh_host'])
            for option in ['StrictHostKeyChecking=yes', 'ClearAllForwardings=yes', 'ForwardAgent=no',
                           'ForwardX11=no', 'SendEnv=-*', 'ControlMaster=no', 'BatchMode=yes']:
                self.assertIn(option, client.command)
            with patch.object(bridge, 'bounded_process', return_value={'status': 200, 'body': {'ok': True}}) as call:
                client.request('GET', bridge.PREFIX + '/jobs/job')
                self.assertEqual(bridge.decode(call.call_args.args[1]), self.request('/jobs/job'))
            with self.assertRaises(ValueError):
                bridge.ProducerClient({**config, 'token': 'unexpected'})
            with self.assertRaises(ValueError):
                bridge.ProducerClient({**config, 'ssh_host': '-oProxyCommand=evil'})

    def test_real_process_bounded_json_and_bad_exit(self):
        program = 'import sys,json; x=json.load(sys.stdin); print(json.dumps({"status":201,"body":{"version":x["version"]}}))'
        result = bridge.bounded_process([sys.executable, '-I', '-c', program], b'{"version":1}\n')
        self.assertEqual(result, {'status': 201, 'body': {'version': 1}})
        for program in ['raise SystemExit(1)', 'print("x"*1100000)', 'print("not-json")']:
            with self.assertRaises(bridge.TransportError):
                bridge.bounded_process([sys.executable, '-I', '-c', program], b'{}\n')

    def test_upload_resumes_authoritative_offset_and_returns_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / 'source.tar'
            data = b'bounded source'
            filename.write_bytes(data)
            client = object.__new__(bridge.ProducerClient)
            client.request = Mock(side_effect=[{'status': 200, 'body': {'offset': 4, 'complete': False}},
                                               {'status': 200, 'body': {'offset': len(data), 'complete': True}}])
            result = client.upload(filename)
            self.assertEqual(result['body'], {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
            self.assertEqual(base64.b64decode(client.request.call_args_list[1].args[2]['data']), data[4:])
            client.request = Mock(side_effect=bridge.TransportError('lost response'))
            with self.assertRaises(bridge.TransportError):
                client.upload(filename)
            client.request.assert_called_once()  # never auto-retries ambiguous calls

    def test_fetch_empty_object_requires_eof_and_exact_empty_digest(self):
        empty = hashlib.sha256(b'').hexdigest()
        client = object.__new__(bridge.ProducerClient)
        response = {'status': 200, 'body': {'artifact_id': empty, 'offset': 0,
                    'size': 0, 'data': '', 'eof': True}}
        client.request = Mock(return_value=response)
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / 'empty.log'
            self.assertEqual(client.fetch('job', empty, filename)['body'], {'sha256': empty, 'size': 0})
            self.assertEqual(filename.read_bytes(), b'')
            filename.unlink()
            response['body']['eof'] = False
            with self.assertRaises(bridge.TransportError):
                client.fetch('job', empty, filename)
            response['body'].update(eof=True, artifact_id='a'*64)
            with self.assertRaisesRegex(bridge.TransportError, 'digest mismatch'):
                client.fetch('job', 'a'*64, filename)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_fetch_verified_atomic_no_overwrite_or_corrupt_partial(self):
        data = b'committed output'
        sha = hashlib.sha256(data).hexdigest()
        client = object.__new__(bridge.ProducerClient)
        response = {'status': 200, 'body': {'artifact_id': sha, 'offset': 0, 'size': len(data),
                    'data': base64.b64encode(data).decode(), 'eof': True}}
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / 'result.json'
            client.request = Mock(return_value=response)
            self.assertEqual(client.fetch('job', sha, filename)['status'], 200)
            self.assertEqual(filename.read_bytes(), data)
            with self.assertRaises(FileExistsError):
                client.fetch('job', sha, filename)
            filename.unlink()
            response['body']['data'] = base64.b64encode(b'wrong contents!').decode()
            with self.assertRaises(bridge.TransportError):
                client.fetch('job', sha, filename)
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
