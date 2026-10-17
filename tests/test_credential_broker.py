"""Isolated broker verification; no real keys, routes or services are contacted."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from agent_console.credential_broker import (
    BrokerStore, DEFAULT_UPSTREAMS, PROTECTED_NAMES, ROUTE_KEYS,
    _redacted_chunks, create_app,
)

ADMIN = "synthetic-broker-administration-token-only"


class BrokerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BrokerStore(Path(self.temp.name) / "broker")

    def tearDown(self):
        self.temp.cleanup()

    def test_precedence_replacement_disable_suppress_delete_live_context(self):
        store = self.store
        name, route = "CMD_API_KEY", "model/commandcode"
        context = store.context("project-one", "pi-main")
        token = context["token"]
        self.assertIsNone(store.resolve_token(token, route))
        store.put_base(name, "host-sentinel")
        self.assertEqual(store.resolve_token(token, route), "host-sentinel")
        store.put_base(name, "account-sentinel", account_ref="pi-main")
        self.assertEqual(store.resolve_token(token, route), "account-sentinel")
        store.put(name, value="global-sentinel")
        self.assertEqual(store.resolve_token(token, route), "global-sentinel")
        store.put(name, value="project-sentinel", project_id="project-one")
        self.assertEqual(store.resolve_token(token, route), "project-sentinel")
        store.put(name, value="replacement-sentinel", project_id="project-one")
        self.assertEqual(store.resolve_token(token, route), "replacement-sentinel")
        store.put(name, state="disabled", project_id="project-one")
        self.assertEqual(store.resolve_token(token, route), "global-sentinel")
        store.put(name, state="suppressed", project_id="project-one")
        self.assertIsNone(store.resolve_token(token, route))
        store.delete(name, project_id="project-one")
        self.assertEqual(store.resolve_token(token, route), "global-sentinel")
        store.delete(name)
        self.assertEqual(store.resolve_token(token, route), "account-sentinel")
        store.delete_base(name, account_ref="pi-main")
        self.assertEqual(store.resolve_token(token, route), "host-sentinel")
        store.delete_base(name)
        self.assertIsNone(store.resolve_token(token, route))
        self.assertEqual(store.context("project-one", "pi-main")["token"], token)

    def test_disabled_global_restores_base_without_affecting_other_context(self):
        self.store.put_base("CMD_API_KEY", "default-sentinel")
        self.store.put_base("CMD_API_KEY", "account-sentinel", account_ref="other")
        self.store.put("CMD_API_KEY", value="global-sentinel")
        self.store.put("CMD_API_KEY", state="disabled")
        token = self.store.context()["token"]
        other = self.store.context(account_ref="other")["token"]
        self.assertEqual(self.store.resolve_token(token, "model/commandcode"), "default-sentinel")
        self.assertEqual(self.store.resolve_token(other, "model/commandcode"), "account-sentinel")

    def test_metadata_and_scope_isolation(self):
        self.store.put("OPENROUTER_API_KEY", value="one-sentinel", project_id="one")
        first = self.store.context("one")
        second = self.store.context("two")
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(first["routes"], ["mcp/openrouter", "model/openrouter"])
        self.assertEqual(second["routes"], [])
        description = self.store.describe("one")
        self.assertEqual(description["entries"][0]["revision"], 1)
        self.assertEqual(description["revision"], 1)
        self.assertTrue(description["entries"][0]["has_value"])
        self.assertNotIn("sentinel", json.dumps(description))
        self.assertNotIn(first["token"], json.dumps(description))

    def test_context_is_stable_across_restart_bounded_and_project_deletion_revokes(self):
        first = self.store.context("one", "pi:main")
        restarted = BrokerStore(self.store.root)
        self.assertEqual(first, restarted.context("one", "pi:main"))
        with patch("agent_console.credential_broker.MAX_CONTEXTS", 1):
            self.assertEqual(first, self.store.context("one", "pi:main"))
            with self.assertRaises(ValueError):
                self.store.context("two")
        self.store.put("CMD_API_KEY", value="project-sentinel", project_id="one")
        self.store.clear_project("one")
        with self.assertRaises(PermissionError):
            self.store.resolve_token(first["token"], "model/commandcode")
        replacement = self.store.context("one", "pi:main")
        self.assertNotEqual(replacement["token"], first["token"])
        self.assertEqual(replacement["routes"], [])
        self.assertNotIn("project-sentinel", self.store.path.read_text())

    def test_concurrent_writes_are_atomic_and_private(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda item: self.store.put(item, value="sentinel"), sorted(PROTECTED_NAMES)))
        self.assertEqual(self.store.describe()["revision"], 5)
        self.assertEqual(len(self.store.describe()["entries"]), 5)
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        with ThreadPoolExecutor(max_workers=5) as pool:
            tokens = list(pool.map(lambda _: self.store.context("same")["token"], range(5)))
        self.assertEqual(len(set(tokens)), 1)

    def test_symlinks_and_non_private_files_fail_closed(self):
        self.store.put("CMD_API_KEY", value="sentinel")
        self.store.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.describe()
        self.store.path.unlink()
        self.store.path.symlink_to(Path(self.temp.name) / "unrelated")
        with self.assertRaises(OSError):
            self.store.describe()

    def test_invalid_values_and_store_quota_do_not_persist(self):
        for value in ["", "space key", "key\nheader", "x" * 32769, "\ud800", 12, {"key": "sentinel"}]:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                self.store.put("CMD_API_KEY", value=value)
        with self.assertRaises(ValueError):
            self.store.put("NOT_PROTECTED", value="sentinel")
        with self.assertRaises(ValueError):
            self.store.put("CMD_API_KEY", state="suppressed")
        self.store.put("CMD_API_KEY", value="before")
        with patch("agent_console.credential_broker.MAX_STORE_BYTES", 500):
            with self.assertRaises(ValueError):
                self.store.put("CMD_API_KEY", value="x" * 1000)
        self.assertEqual(self.store.describe()["revision"], 1)


class BrokerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BrokerStore(Path(self.temp.name) / "broker")
        self.requests = []
        self.reply = lambda request: httpx.Response(200, json={"jsonrpc": "2.0", "result": {"tools": [{"name": "existing-tool"}]}})

        def upstream(request):
            self.requests.append(request)
            return self.reply(request)

        self.app = create_app(self.store, ADMIN, transport=httpx.MockTransport(upstream))
        self.client = TestClient(self.app).__enter__()
        self.admin = {"Authorization": "Bearer " + ADMIN}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def context(self, project_id=None, account_ref=None):
        result = self.client.post("/control/context", json={"project_id": project_id, "account_ref": account_ref}, headers=self.admin)
        self.assertEqual(result.status_code, 200, result.text)
        return {"Authorization": "Bearer " + result.json()["token"]}

    def test_all_six_routes_inject_upstream_auth_and_preserve_body(self):
        for name in PROTECTED_NAMES:
            self.store.put(name, value=name + "-synthetic")
        headers = self.context()
        for route, name in ROUTE_KEYS.items():
            path = "/" + route if route.startswith("mcp/") else "/" + route + "/v1/chat/completions"
            with self.subTest(route=route):
                response = self.client.post(path, json={"method": "tools/list", "params": {}}, headers={**headers, "Mcp-Session-Id": "session-id", "Cookie": "do-not-forward", "X-Api-Key": "do-not-forward"})
                self.assertEqual(response.status_code, 200, response.text)
                request = self.requests[-1]
                self.assertEqual(request.headers["authorization"], "Bearer " + name + "-synthetic")
                self.assertEqual(request.headers["mcp-session-id"], "session-id")
                self.assertNotIn("cookie", request.headers)
                self.assertNotIn("x-api-key", request.headers)
                expected = DEFAULT_UPSTREAMS[route] + ("/chat/completions" if route.startswith("model/") else "")
                self.assertEqual(str(request.url), expected)
                self.assertEqual(json.loads(request.content)["method"], "tools/list")
                self.assertEqual(response.json()["result"]["tools"][0]["name"], "existing-tool")
        self.assertEqual(len(self.requests), 6)
        for provider in ["commandcode", "openrouter"]:
            response = self.client.get(f"/model/{provider}/v1/models", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.requests[-1].method, "GET")

    def test_control_write_only_metadata_and_existing_token_changes_immediately(self):
        headers = self.context("one")
        self.assertEqual(self.client.post("/mcp/n8n", headers=headers).status_code, 503)
        response = self.client.put("/control/entries/N8N_MCP_TOKEN", json={"value": "first-sentinel"}, headers=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sentinel", response.text)
        self.assertEqual(self.client.post("/mcp/n8n", headers=headers).status_code, 200)
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer first-sentinel")
        self.client.put("/control/entries/N8N_MCP_TOKEN", json={"value": "second-sentinel"}, headers=self.admin)
        self.client.post("/mcp/n8n", headers=headers)
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer second-sentinel")
        self.client.put("/control/entries/N8N_MCP_TOKEN?project_id=one", json={"state": "suppressed"}, headers=self.admin)
        self.assertEqual(self.client.post("/mcp/n8n", headers=headers).status_code, 503)
        self.client.delete("/control/entries/N8N_MCP_TOKEN?project_id=one", headers=self.admin)
        self.assertEqual(self.client.post("/mcp/n8n", headers=headers).status_code, 200)
        self.client.delete("/control/projects/one", headers=self.admin)
        self.assertEqual(self.client.post("/mcp/n8n", headers=headers).status_code, 401)
        status = self.client.get("/control/status", headers=self.admin)
        self.assertNotIn("sentinel", status.text)
        self.assertNotIn(ADMIN, status.text)

    def test_admin_data_tokens_and_contexts_are_separate(self):
        headers = self.context("one")
        for method, path in [("get", "/control/status"), ("put", "/control/entries/CMD_API_KEY"), ("delete", "/control/entries/CMD_API_KEY"), ("post", "/control/context"), ("delete", "/control/projects/one")]:
            response = getattr(self.client, method)(path, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/control/status").status_code, 401)
        self.assertEqual(self.client.post("/mcp/n8n", headers=self.admin).status_code, 401)
        self.assertEqual(self.requests, [])

    def test_unsupported_routes_methods_queries_and_redirects_are_rejected(self):
        self.store.put("CMD_API_KEY", value="sentinel")
        headers = self.context()
        for path in ["/model/commandcode/v1/chat/completions?url=https://evil.test", "/mcp/http://evil.test", "/model/other/v1/chat/completions", "/model/commandcode/v1/completions"]:
            response = self.client.post(path, headers=headers)
            self.assertIn(response.status_code, [400, 404])
        self.assertEqual(self.client.put("/mcp/n8n", headers=headers).status_code, 405)
        self.assertEqual(self.client.post("/model/commandcode/v1/models", headers=headers).status_code, 405)
        self.assertEqual(self.requests, [])
        self.reply = lambda _: httpx.Response(302, headers={"Location": "https://evil.test/sentinel"})
        response = self.client.post("/model/commandcode/v1/chat/completions", headers=headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("evil.test", response.text)
        self.assertEqual(len(self.requests), 1)

    def test_transport_exception_and_upstream_errors_do_not_leak_keys_or_retry(self):
        self.store.put("CMD_API_KEY", value="secret-sentinel")
        headers = self.context()
        def broken(request):
            raise httpx.ConnectError("secret-sentinel upstream failure", request=request)
        self.reply = broken
        response = self.client.get("/model/commandcode/v1/models", headers=headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-sentinel", response.text)
        self.assertEqual(len(self.requests), 1)
        self.reply = lambda _: httpx.Response(401, json={"error": "Bearer secret-sentinel is invalid"}, headers={"Set-Cookie": "secret-sentinel", "WWW-Authenticate": "secret-sentinel"})
        response = self.client.get("/model/commandcode/v1/models", headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("secret-sentinel", response.text)
        self.assertIn("[redacted]", response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("www-authenticate", response.headers)
        self.assertEqual(len(self.requests), 2)

    def test_invalid_control_input_never_echoes_submitted_secrets(self):
        for body in [{"state": {"secret": "secret-sentinel"}}, {"value": {"secret": "secret-sentinel"}}, {"value": "secret-sentinel", "unexpected": 1}, {"value": "secret-sentinel\r\nX-Header: value"}]:
            response = self.client.put("/control/entries/CMD_API_KEY", json=body, headers=self.admin)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("secret-sentinel", response.text)
        response = self.client.put("/control/entries/CMD_API_KEY", content=b'{"value": "secret-sentinel", bad}', headers=self.admin)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("secret-sentinel", response.text)
        with patch("agent_console.credential_broker.MAX_CONTROL_BYTES", 3):
            response = self.client.put("/control/entries/CMD_API_KEY", json={"value": "secret-sentinel"}, headers=self.admin)
            self.assertEqual(response.status_code, 413)

    def test_base_endpoints_and_model_and_mcp_stream_headers(self):
        self.client.put("/control/bases/CMD_API_KEY?account_ref=pi-main", json={"value": "account-sentinel"}, headers=self.admin)
        headers = self.context(account_ref="pi-main")
        content = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: [DONE]\n\n'
        self.reply = lambda _: httpx.Response(200, content=content, headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "mcp-id", "Connection": "keep-alive"})
        response = self.client.post("/model/commandcode/v1/chat/completions", headers=headers)
        self.assertEqual(response.content, content)
        self.assertEqual(response.headers["content-type"], "text/event-stream")
        self.assertEqual(response.headers["mcp-session-id"], "mcp-id")
        self.assertNotIn("connection", response.headers)
        self.client.delete("/control/bases/CMD_API_KEY?account_ref=pi-main", headers=self.admin)
        self.assertEqual(self.client.get("/model/commandcode/v1/models", headers=headers).status_code, 503)

    def test_mcp_get_delete_and_upstream_405_allow_are_preserved(self):
        self.store.put("N8N_MCP_TOKEN", value="sentinel")
        headers = self.context()
        self.reply = lambda _: httpx.Response(405, json={"error": "POST required"}, headers={"Allow": "POST"})
        response = self.client.get("/mcp/n8n", headers=headers)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["allow"], "POST")
        self.assertEqual(self.requests[-1].method, "GET")
        self.client.delete("/mcp/n8n", headers=headers)
        self.assertEqual(self.requests[-1].method, "DELETE")


class BrokerStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_boundary_redaction_and_normal_streaming(self):
        class Stream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                for chunk in [b"data: hello\n\n", b"data: secret-", b"sentinel", b" end\n\n"]:
                    yield chunk
            async def aclose(self):
                self.closed = True
        stream = Stream()
        response = httpx.Response(200, stream=stream)
        iterator = _redacted_chunks(response, "secret-sentinel")
        self.assertEqual(await anext(iterator), b"data: hello\n\n")
        chunks = [chunk async for chunk in iterator]
        self.assertEqual(b"".join(chunks), b"data: [redacted] end\n\n")
        self.assertTrue(stream.closed)

    async def test_downstream_cancellation_closes_upstream(self):
        started = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b"first event\n\n"
                started.set()
                await asyncio.Event().wait()
            async def aclose(self):
                self.closed = True
        stream = Stream()
        iterator = _redacted_chunks(httpx.Response(200, stream=stream), "sentinel")
        self.assertEqual(await anext(iterator), b"first event\n\n")
        pending = asyncio.create_task(anext(iterator))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(stream.closed)

    async def test_late_upstream_failure_is_value_blind_and_closed(self):
        class Stream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b"first event\n\n"
                raise httpx.ReadError("secret-sentinel")
            async def aclose(self):
                self.closed = True
        stream = Stream()
        with self.assertLogs("agent_console.credential_broker", level="WARNING") as logs:
            chunks = [chunk async for chunk in _redacted_chunks(httpx.Response(200, stream=stream), "secret-sentinel")]
        self.assertEqual(b"".join(chunks), b"first event\n\n")
        self.assertNotIn("secret-sentinel", "".join(logs.output))
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
