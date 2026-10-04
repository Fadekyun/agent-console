"""Small isolated API tests: no server singleton, subprocesses or live queue."""
import ipaddress
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from agent_console.compute_api import compute_routes
from agent_console.request_identity import (
    AuthContext, IdentityDenied, allowed_origins, authorize_identity, proxy_networks,
)


JOB = {
    'kind': 'maintenance.report',
    'execution_target': 'n100',
    'source_digest': 'a' * 64,
    'input_digest': 'b' * 64,
    'request_key': 'owner-report-1',
}


class ComputeApiTests(unittest.TestCase):
    def setUp(self):
        self.queue = Mock()
        for name in ('enqueue', 'inspect', 'retry', 'cancel', 'reconcile_terminated'):
            getattr(self.queue, name).return_value = {'id': 'job-one', 'state': 'queued'}
        self.queue.set_hold.return_value = {'held': True}
        self.engine = SimpleNamespace(queue=self.queue, status=Mock(return_value={
            'jobs': [], 'counts': {}, 'reservations': [], 'enabled': False,
            'held': True, 'workers': [], 'telemetry': {},
        }))
        self.engine_factory = Mock(return_value=self.engine)

        def require_identity(request: Request):
            if request.headers.get('x-test-owner') != 'owner':
                raise HTTPException(403, 'Owner required')
            return AuthContext(actor='owner@example.test', access_surface='tailscale')

        app = FastAPI()
        app.include_router(compute_routes(self.engine_factory, require_identity,
                                         permitted_origins=allowed_origins('https://console.example')))
        self.client = TestClient(app)
        self.auth = {'X-Test-Owner': 'owner'}

    def routes(self):
        return [
            ('GET', '/compute', None),
            ('GET', '/api/compute', None),
            ('GET', '/api/compute/jobs/job-one', None),
            ('POST', '/api/compute/jobs', JOB),
            ('POST', '/api/compute/hold', {'held': True}),
            ('POST', '/api/compute/jobs/job-one/retry', {}),
            ('POST', '/api/compute/jobs/job-one/cancel', {}),
            ('POST', '/api/compute/attempts/attempt-one/reconcile', {'evidence': 'Verified process exit'}),
        ]

    def test_every_route_requires_operator_before_engine_access(self):
        for method, path, body in self.routes():
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path, json=body).status_code, 403)
        self.engine_factory.assert_not_called()

    def test_agent_capabilities_never_substitute_for_operator_controls(self):
        for extra in [
            {'X-Agent-Console-Session': 'session-one'},
            {'X-Agent-Console-Capability': 'agent-token'},
            {'Authorization': 'Bearer agent-token'},
            {'X-Agent-Console-Session': ''},
        ]:
            for method, path, body in self.routes():
                with self.subTest(path=path, header=next(iter(extra))):
                    response = self.client.request(method, path, json=body, headers={**self.auth, **extra})
                    self.assertEqual(response.status_code, 403)
        self.engine_factory.assert_not_called()

    def identity_app(self):
        """Exercise real socket-peer provenance without importing web globals."""
        def require_identity(request: Request):
            try:
                return authorize_identity(
                    request, expected_login='owner@example.test',
                    lan_network=ipaddress.ip_network('192.168.1.0/24'),
                    trusted_proxies=proxy_networks('127.0.0.1/32'),
                )
            except IdentityDenied as exc:
                raise HTTPException(exc.status, exc.detail) from None

        app = FastAPI()

        @app.get('/shared-auth')
        def shared_auth(auth=Depends(require_identity)):
            return {'access_surface': auth.access_surface}

        app.include_router(compute_routes(self.engine_factory, require_identity,
                                         permitted_origins=allowed_origins('https://console.example')))
        return app

    def test_headerless_lan_shared_identity_cannot_access_compute(self):
        client = TestClient(self.identity_app(), client=('192.168.1.115', 50000))
        shared = client.get('/shared-auth')
        self.assertEqual(shared.status_code, 200)
        self.assertEqual(shared.json(), {'access_surface': 'local-lan'})
        for method, path, body in self.routes():
            with self.subTest(path=path):
                self.assertEqual(client.request(method, path, json=body).status_code, 403)
        # A LAN caller cannot manufacture the trusted proxy's forwarded login.
        self.assertEqual(client.get('/api/compute', headers={
            'Tailscale-User-Login': 'owner@example.test',
        }).status_code, 403)
        self.engine_factory.assert_not_called()

    def test_trusted_proxy_authenticated_owner_retains_compute_access(self):
        client = TestClient(self.identity_app(), client=('127.0.0.1', 50000))
        for headers in ({}, {'Tailscale-User-Login': 'someone-else@example.test'}):
            self.assertEqual(client.get('/api/compute', headers=headers).status_code, 403)
        self.engine_factory.assert_not_called()
        headers = {'Tailscale-User-Login': 'owner@example.test', 'Origin': 'https://console.example'}
        for method, path, body in self.routes():
            with self.subTest(path=path):
                self.assertEqual(client.request(method, path, json=body, headers=headers).status_code, 200)
        self.queue.enqueue.assert_called_once_with(**JOB, actor='owner@example.test')

    def test_mutations_require_exact_browser_origin_but_native_requests_work(self):
        for method, path, body in self.routes():
            if method != 'POST':
                continue
            for origin in ('https://foreign.example', 'null', 'https://console.example:444'):
                with self.subTest(path=path, origin=origin):
                    response = self.client.request(method, path, json=body,
                                                   headers={**self.auth, 'Origin': origin})
                    self.assertEqual(response.status_code, 403)
        self.engine_factory.assert_not_called()
        for origin in (None, 'https://CONSOLE.example:443'):
            headers = dict(self.auth)
            if origin:
                headers['Origin'] = origin
            self.assertEqual(self.client.post('/api/compute/hold', json={'held': True}, headers=headers).status_code, 200)

    def test_enqueue_has_fixed_profile_inputs_and_audited_operator(self):
        response = self.client.post('/api/compute/jobs', json=JOB, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.queue.enqueue.assert_called_once_with(**JOB, actor='owner@example.test')
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_invalid_job_inputs_never_reach_queue_and_are_not_echoed(self):
        invalid = [
            {**JOB, 'command': 'secret-untrusted-command'},
            {**JOB, 'source_digest': 'A' * 64},
            {**JOB, 'input_digest': 'b' * 63},
            {**JOB, 'kind': 'shell'},
            {**JOB, 'execution_target': 'production'},
            {**JOB, 'request_key': 'x' * 101},
            {**JOB, 'request_key': ''},
            {**JOB, 'request_key': 123},
            {**JOB, 'telemetry': {'available_ram': 99999999999}},
            {**JOB, 'memory_max': 99999999999},
        ]
        for body in invalid:
            with self.subTest(body=body):
                response = self.client.post('/api/compute/jobs', json=body, headers=self.auth)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'detail': 'Invalid compute request'})
        self.engine_factory.assert_not_called()

    def test_all_mutation_schemas_forbid_extra_fields_and_bool_coercion(self):
        for method, path, body in self.routes():
            if method == 'POST':
                response = self.client.post(path, json={**body, 'command': 'do-not-execute'}, headers=self.auth)
                self.assertEqual(response.status_code, 400, path)
        for value in ('false', 0, None):
            response = self.client.post('/api/compute/hold', json={'held': value}, headers=self.auth)
            self.assertEqual(response.status_code, 400)
        for evidence in ('', 'x' * 4001):
            response = self.client.post('/api/compute/attempts/one/reconcile', json={'evidence': evidence}, headers=self.auth)
            self.assertEqual(response.status_code, 400)
        self.engine_factory.assert_not_called()

    def test_invalid_json_does_not_reflect_request_body(self):
        response = self.client.post('/api/compute/jobs', content='{secret-untrusted-token',
                                    headers={**self.auth, 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'detail': 'Invalid compute request'})
        self.engine_factory.assert_not_called()

    def test_operations_pass_actor_and_evidence_and_never_dispatch(self):
        self.client.get('/api/compute/jobs/job-one', headers=self.auth)
        self.queue.inspect.assert_called_once_with('job-one')
        self.client.post('/api/compute/hold', json={'held': False}, headers=self.auth)
        self.queue.set_hold.assert_called_once_with(held=False, actor='owner@example.test')
        self.client.post('/api/compute/jobs/job-one/retry', json={}, headers=self.auth)
        self.queue.retry.assert_called_once_with('job-one', actor='owner@example.test')
        self.client.post('/api/compute/jobs/job-one/cancel', json={}, headers=self.auth)
        self.queue.cancel.assert_called_once_with('job-one', actor='owner@example.test')
        self.client.post('/api/compute/attempts/attempt-one/reconcile',
                         json={'evidence': 'Verified PID exit in fresh inventory'}, headers=self.auth)
        self.queue.reconcile_terminated.assert_called_once_with(
            'attempt-one', evidence='Verified PID exit in fresh inventory', actor='owner@example.test')
        self.assertEqual({call[0] for call in self.queue.mock_calls},
                         {'inspect', 'set_hold', 'retry', 'cancel', 'reconcile_terminated'})

    def test_domain_errors_are_sanitized(self):
        for error, expected in [(ValueError('secret-from-request'), 400),
                                (KeyError('secret-from-request'), 400),
                                (PermissionError('secret-from-request'), 403)]:
            self.queue.enqueue.side_effect = error
            response = self.client.post('/api/compute/jobs', json=JOB, headers=self.auth)
            self.assertEqual(response.status_code, expected)
            self.assertNotIn('secret-from-request', response.text)

    def test_status_includes_offline_jobs_and_unknown_reservations(self):
        status = {
            'enabled': False, 'held': True, 'counts': {'blocked': 1, 'unknown': 1},
            'jobs': [{'id': 'job-one', 'state': 'blocked', 'reason': 'worker_disabled'}],
            'reservations': [{'physical_host': 'n100', 'state': 'unknown'}],
            'workers': [{'worker_id': 'agc-laptop', 'enabled': False, 'availability': 'Wed–Sun'}],
            'telemetry': {},
        }
        self.engine.status.return_value = status
        response = self.client.get('/api/compute', headers=self.auth)
        self.assertEqual(response.json(), status)
        self.assertEqual(self.queue.mock_calls, [])

    def test_no_remote_execution_telemetry_or_enable_endpoint(self):
        for path in ('/api/compute/tick', '/api/compute/start', '/api/compute/telemetry',
                     '/api/compute/workers', '/api/compute/enable', '/api/compute/publish'):
            self.assertEqual(self.client.post(path, json={}, headers=self.auth).status_code, 404)
        self.engine_factory.assert_not_called()

    def test_status_page_is_protected_read_only_and_uses_text_nodes(self):
        response = self.client.get('/compute', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        html = response.text
        self.assertIn('textContent', html)
        self.assertIn('replaceChildren', html)
        for unsafe_sink in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'):
            self.assertNotIn(unsafe_sink, html)
        self.assertNotIn('<form', html)
        self.assertNotIn("method: 'POST'", html)
        self.assertIn('stale', html)
        self.assertIn('Unknown attempts retain their reservation', html)
        self.engine_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
