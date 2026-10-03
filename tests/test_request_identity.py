import ipaddress
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from agent_console.request_identity import (
    IdentityDenied, allowed_origins, authorize_browser_origin, authorize_identity,
    normalized_origin, proxy_networks,
)


class IdentityPolicyTests(unittest.TestCase):
    def connection(self, peer='192.168.1.64', headers=()):
        return Request({'type':'http', 'client':(peer,50000), 'headers':[(k.encode(),v.encode()) for k,v in headers]})

    def authorize(self, connection, login='owner@example.com'):
        return authorize_identity(connection, expected_login=login,
                                  lan_network=ipaddress.ip_network('192.168.1.0/24'),
                                  trusted_proxies=proxy_networks('127.0.0.1/32,::1/128,192.168.1.64/32'))

    def test_proxy_identity_and_direct_lan_authority_are_separate(self):
        header = [('tailscale-user-login','owner@example.com')]
        self.assertEqual(self.authorize(self.connection(headers=header)).access_surface,'tailscale')
        self.assertEqual(self.authorize(self.connection('192.168.1.94')).access_surface,'local-lan')
        for peer, headers in [('192.168.1.64',()), ('192.168.1.94',header), ('203.0.113.1',header), ('192.168.1.64',header*2), ('192.168.1.64',[('tailscale-user-login','')])]:
            with self.subTest(peer=peer,headers=headers), self.assertRaises(IdentityDenied):
                self.authorize(self.connection(peer,headers))
        with self.assertRaises(IdentityDenied) as missing:
            self.authorize(self.connection('192.168.1.94'), login='')
        self.assertEqual(missing.exception.status,503)

    def test_forwarded_ip_does_not_establish_proxy_provenance(self):
        headers = [('tailscale-user-login','owner@example.com'),('x-forwarded-for','127.0.0.1'),('forwarded','for=127.0.0.1')]
        with self.assertRaises(IdentityDenied): self.authorize(self.connection('203.0.113.1',headers))

    def test_origins_match_exact_normalized_scheme_host_and_port(self):
        allowed = allowed_origins('https://console.example,http://192.168.1.115:3210')
        self.assertEqual(normalized_origin('https://CONSOLE.example:443'), normalized_origin('https://console.example'))
        for value in ['https://console.example:443','http://192.168.1.115:3210']:
            authorize_browser_origin(self.connection(headers=[('origin',value)]), allowed)
        for value in ['null','https://evil.example','http://console.example','https://console.example:444','http://192.168.1.115:9999','https://console.example@evil.example','https://console.example/path','https://console.example:0']:
            with self.subTest(origin=value), self.assertRaises(IdentityDenied):
                authorize_browser_origin(self.connection(headers=[('origin',value)]), allowed)
        with self.assertRaises(IdentityDenied):
            authorize_browser_origin(self.connection(headers=[('origin','https://console.example')]*2), allowed)
        authorize_browser_origin(self.connection(), allowed)  # Native clients may omit Origin.


class IdentityRouteTests(unittest.TestCase):
    def setUp(self):
        from agent_console import web
        self.web = web
        self.patches = [patch.object(web,'EXPECTED_LOGIN','owner@example.com'),
                        patch.object(web,'LAN_NETWORK',ipaddress.ip_network('192.168.1.0/24')),
                        patch.object(web,'TRUSTED_HOSTS',['testserver','console.example']),
                        patch.object(web,'TRUSTED_PROXY_NETWORKS',proxy_networks('127.0.0.1/32,192.168.1.64/32')),
                        patch.object(web,'ALLOWED_ORIGINS',allowed_origins('https://console.example'))]
        for item in self.patches: item.start()
        self.manager = Mock()
        self.manager.settings = SimpleNamespace(workspace_root=Path('/tmp'), state_dir=Path('/tmp'), database_path=Path('/tmp/unopened-auth-test-db'))
        self.manager.tmux_for_name.return_value.exists.return_value = False
        self.manager.inspect.return_value = {'id':'fixture-id','execution_kind':'interactive'}
        self.app = web.create_app(self.manager)
        self.headers = {'Tailscale-User-Login':'owner@example.com'}

    def tearDown(self):
        for item in reversed(self.patches): item.stop()

    def client(self, host):
        return TestClient(self.app, client=(host,50000))

    def assert_ws_denied(self, client, headers, code=4403):
        with self.assertRaises(WebSocketDisconnect) as denied:
            with client.websocket_connect('/ws/sessions/fixture', headers=headers): pass
        self.assertEqual(denied.exception.code, code)

    def test_http_presence_and_ws_reject_forged_headers_before_manager_access(self):
        from agent_console.device_presence import GET
        for peer in ['203.0.113.4','192.168.1.94']:
            client = self.client(peer)
            self.assertEqual(client.get('/api/interface',headers=self.headers).status_code,403)
            self.assertEqual(client.get(GET,headers=self.headers).status_code,403)
            self.assert_ws_denied(client,self.headers)
        self.manager.tmux_for_name.assert_not_called()
        self.manager.inspect.assert_not_called()

    def test_proxy_without_identity_cannot_fall_back_to_lan(self):
        from agent_console.device_presence import GET
        client = self.client('192.168.1.64')
        self.assertEqual(client.get('/api/interface').status_code,403)
        self.assertEqual(client.get(GET).status_code,403)
        self.assert_ws_denied(client,{})
        self.assertEqual(self.client('192.168.1.94').get('/api/interface').status_code,200)

    def test_foreign_origin_blocked_before_pty_even_with_valid_identity(self):
        client = self.client('192.168.1.64')
        with patch.object(self.web.pty,'openpty') as allocate:
            self.assert_ws_denied(client,{**self.headers,'Origin':'https://evil.example'})
            self.assert_ws_denied(client,{**self.headers,'Origin':'https://console.example:444'})
            self.manager.tmux_for_name.assert_not_called()
            allocate.assert_not_called()
        for origin in [None,'https://console.example:443']:
            headers = self.headers if origin is None else {**self.headers,'Origin':origin}
            self.assert_ws_denied(client,headers,4404)  # Auth passes; fixture session is absent.

    def test_session_observation_failure_closes_websocket_without_allocating_pty(self):
        client = self.client('192.168.1.64')
        self.manager.inspect.side_effect = RuntimeError('tmux observation timed out')
        with patch.object(self.web.pty, 'openpty') as allocate:
            self.assert_ws_denied(client, self.headers, 1011)
            allocate.assert_not_called()

    def test_missing_login_configuration_fails_closed_on_all_surfaces(self):
        from agent_console.device_presence import GET
        with patch.object(self.web,'EXPECTED_LOGIN',''):
            for peer in ['192.168.1.94','192.168.1.64']:
                client = self.client(peer)
                self.assertEqual(client.get('/api/interface').status_code,503)
                self.assertEqual(client.get(GET).status_code,503)
                self.assert_ws_denied(client,{},4503)
        self.manager.tmux_for_name.assert_not_called()
