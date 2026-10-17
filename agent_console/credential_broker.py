"""Fixed-destination credential proxy, run under its own operating-system user.

The control API accepts secrets but only returns metadata. Data credentials are
shared by project/account context and are resolved against current state on each
request. Neither endpoint is a general-purpose HTTP proxy.
"""
from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import logging
import os
import re
import secrets
import stat
import tempfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

PROTECTED_NAMES = frozenset({
    "N8N_MCP_TOKEN", "DIRECTUS_MCP_TOKEN", "BUSHI_MCP_TOKEN",
    "OPENROUTER_API_KEY", "CMD_API_KEY",
})
ROUTE_KEYS = {
    "mcp/n8n": "N8N_MCP_TOKEN", "mcp/directus": "DIRECTUS_MCP_TOKEN",
    "mcp/bushi": "BUSHI_MCP_TOKEN", "mcp/openrouter": "OPENROUTER_API_KEY",
    "model/commandcode": "CMD_API_KEY", "model/openrouter": "OPENROUTER_API_KEY",
}
DEFAULT_UPSTREAMS = {
    "mcp/n8n": "http://192.168.1.73/mcp-server/http",
    "mcp/directus": "http://192.168.1.71:8055/mcp",
    "mcp/bushi": "http://192.168.1.67:8791/mcp",
    "mcp/openrouter": "https://mcp.openrouter.ai/mcp",
    "model/commandcode": "https://api.commandcode.ai/provider/v1",
    "model/openrouter": "https://openrouter.ai/api/v1",
}
REQUEST_HEADERS = frozenset({"accept", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id"})
RESPONSE_HEADERS = frozenset({"content-type", "cache-control", "mcp-session-id", "mcp-protocol-version", "retry-after", "allow"})
LOG = logging.getLogger(__name__)
MAX_CONTEXTS = 2048
MAX_STORE_BYTES = 4 * 1024 * 1024
MAX_CONTROL_BYTES = 65536
MAX_REQUEST_BYTES = 16 * 1024 * 1024


def _identity(value, *, project=False):
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid broker context")
    if project and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise ValueError("Invalid broker context")
    return value


def _name(name):
    if name not in PROTECTED_NAMES:
        raise ValueError("Unsupported protected variable")
    return name


def _value(value):
    # All supported upstreams use HTTP bearer tokens: whitespace/control bytes
    # are never valid credentials. Do not echo rejected input.
    if not isinstance(value, str) or not 1 <= len(value) <= 32768 or not all(33 <= ord(c) <= 126 for c in value):
        raise ValueError("Invalid protected credential")
    return value


def _private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Broker file must be owner-only")
        with os.fdopen(fd, encoding="utf-8") as stream:
            fd = -1
            return stream.read(MAX_STORE_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)


class BrokerStore:
    """Single private atomic JSON store with serialized writers; no shell input."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "credentials.json"

    @staticmethod
    def scope(project_id=None):
        return "global" if _identity(project_id, project=True) is None else "project:" + project_id

    @contextmanager
    def _lock(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Broker directory must be owner-only")
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Broker lock must be owner-only")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            raw = _private_file(self.path)
        except FileNotFoundError:
            return {"version": 1, "revision": 0, "scopes": {}, "bases": {}, "contexts": []}
        try:
            data = json.loads(raw)
            if len(raw.encode()) > MAX_STORE_BYTES or data["version"] != 1 or not isinstance(data["revision"], int):
                raise ValueError()
            if not isinstance(data["scopes"], dict) or not isinstance(data["bases"], dict) or not isinstance(data["contexts"], list):
                raise ValueError()
            return data
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise ValueError("Invalid broker store") from None

    def _write(self, data):
        encoded = json.dumps(data, ensure_ascii=True).encode()
        if len(encoded) > MAX_STORE_BYTES:
            raise ValueError("Broker store capacity reached")
        if self.path.is_symlink():
            raise ValueError("Broker file cannot be a symbolic link")
        fd, temporary = tempfile.mkstemp(prefix=".broker-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _resolve(data, project_id, account_ref):
        values, sources = {}, {}
        bases = ["host"] + (["account:" + account_ref] if account_ref else [])
        for base in bases:
            for name, entry in data["bases"].get(base, {}).items():
                values[_name(name)] = _value(entry["value"])
                sources[name] = "host" if base == "host" else "credential"
        scopes = ["global"] + ([BrokerStore.scope(project_id)] if project_id else [])
        for scope in scopes:
            for name, entry in data["scopes"].get(scope, {}).items():
                _name(name)
                if entry["state"] == "suppressed":
                    values.pop(name, None)
                    sources.pop(name, None)
                elif entry["state"] == "enabled":
                    values[name] = _value(entry["value"])
                    sources[name] = scope
        return values, sources

    def describe(self, project_id=None, account_ref=None):
        scope = self.scope(project_id)
        _identity(account_ref)
        with self._lock():
            data = self._read()
        values, sources = self._resolve(data, project_id, account_ref)
        entries = [{"name": name, "state": entry["state"], "revision": entry["revision"],
                    "updated_at": entry["updated_at"], "scope": scope, "has_value": "value" in entry,
                    "overrides_host": name in data["bases"].get("host", {}),
                    "overrides_global": project_id is not None and name in data["scopes"].get("global", {})}
                   for name, entry in sorted(data["scopes"].get(scope, {}).items())]
        return {"scope": scope, "revision": data["revision"], "entries": entries,
                "effective": [{"name": name, "source": sources.get(name), "available": name in values}
                              for name in sorted(PROTECTED_NAMES)],
                "host_names": sorted(data["bases"].get("host", {})),
                "routes": [route for route, key in ROUTE_KEYS.items() if key in values]}

    def put(self, name, *, value=None, state="enabled", project_id=None):
        _name(name)
        scope = self.scope(project_id)
        if not isinstance(state, str) or state not in {"enabled", "disabled", "suppressed"} or (state == "suppressed" and project_id is None):
            raise ValueError("Invalid protected credential state")
        if value is not None:
            _value(value)
        with self._lock():
            data = self._read()
            entries = data["scopes"].setdefault(scope, {})
            old = entries.get(name, {})
            if state != "suppressed" and value is None and "value" not in old:
                raise ValueError("A credential is required")
            data["revision"] += 1
            entry = {"state": state, "revision": data["revision"], "updated_at": datetime.now(timezone.utc).isoformat()}
            if state != "suppressed":
                entry["value"] = old.get("value") if value is None else value
            entries[name] = entry
            self._write(data)
        return self.describe(project_id)

    def delete(self, name, *, project_id=None):
        _name(name)
        scope = self.scope(project_id)
        with self._lock():
            data = self._read()
            if name not in data["scopes"].get(scope, {}):
                raise KeyError("Protected variable is not configured in this scope")
            del data["scopes"][scope][name]
            data["revision"] += 1
            self._write(data)
        return self.describe(project_id)

    def put_base(self, name, value, *, account_ref=None):
        _name(name)
        _value(value)
        _identity(account_ref)
        base = "host" if account_ref is None else "account:" + account_ref
        with self._lock():
            data = self._read()
            data["revision"] += 1
            data["bases"].setdefault(base, {})[name] = {"value": value}
            self._write(data)
        return self.describe(account_ref=account_ref)

    def delete_base(self, name, *, account_ref=None):
        _name(name)
        _identity(account_ref)
        base = "host" if account_ref is None else "account:" + account_ref
        with self._lock():
            data = self._read()
            if name not in data["bases"].get(base, {}):
                raise KeyError("Protected base is not configured")
            del data["bases"][base][name]
            data["revision"] += 1
            self._write(data)
        return self.describe(account_ref=account_ref)

    def context(self, project_id=None, account_ref=None):
        self.scope(project_id)
        _identity(account_ref)
        with self._lock():
            data = self._read()
            context = next((entry for entry in data["contexts"] if entry["project_id"] == project_id and entry["account_ref"] == account_ref), None)
            if context is None:
                if len(data["contexts"]) >= MAX_CONTEXTS:
                    raise ValueError("Broker context capacity reached")
                context = {"project_id": project_id, "account_ref": account_ref, "token": secrets.token_urlsafe(32)}
                data["contexts"].append(context)
                self._write(data)
            values, _ = self._resolve(data, project_id, account_ref)
            return {**context, "routes": [route for route, key in ROUTE_KEYS.items() if key in values]}

    def resolve_token(self, token, route):
        """Private data-plane lookup; the key never enters an API response."""
        with self._lock():
            data = self._read()
        context = next((entry for entry in data["contexts"] if hmac.compare_digest(entry["token"], token)), None)
        if context is None:
            raise PermissionError("Invalid broker credential")
        values, _ = self._resolve(data, context["project_id"], context["account_ref"])
        return values.get(ROUTE_KEYS[route])

    def clear_project(self, project_id):
        if project_id is None:
            raise ValueError("A project is required")
        scope = self.scope(project_id)
        with self._lock():
            data = self._read()
            data["scopes"].pop(scope, None)
            data["contexts"] = [entry for entry in data["contexts"] if entry["project_id"] != project_id]
            data["revision"] += 1
            self._write(data)
        return {"ok": True}


async def _body(request, limit):
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > limit:
            raise HTTPException(413, "Broker request is too large")
    return bytes(result)


async def _payload(request, allowed):
    try:
        payload = json.loads(await _body(request, MAX_CONTROL_BYTES))
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError()
        return payload
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid broker request") from None


def _bearer(request):
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 512 or not token.isascii():
        raise HTTPException(401, "Broker authentication required")
    return token


async def _redacted_chunks(response, key):
    """Redact even a credential split across arbitrary upstream stream chunks."""
    needle = key.encode("ascii")
    pending = b""
    try:
        async for chunk in response.aiter_bytes():
            pending += chunk
            # Only hold a suffix that could be the beginning of the credential.
            # Ordinary SSE events are yielded immediately, with no full-response
            # buffering and no delay proportional to the API key's length.
            pending = pending.replace(needle, b"[redacted]")
            keep = 0
            for length in range(min(len(needle) - 1, len(pending)), 0, -1):
                if pending.endswith(needle[:length]):
                    keep = length
                    break
            if len(pending) > keep:
                yield pending[:-keep] if keep else pending
                pending = pending[-keep:] if keep else b""
        if pending:
            yield pending.replace(needle, b"[redacted]")
    except httpx.HTTPError:
        # The downstream status is already sent. End the stream; never leak
        # upstream exception strings, which may embed headers or a URL.
        LOG.warning("broker upstream stream ended unexpectedly")
    finally:
        await response.aclose()


def create_app(store: BrokerStore, admin_token: str, upstreams=None, *, transport=None):
    _value(admin_token)
    if len(admin_token) < 24:
        raise ValueError("Broker administration credential is too short")
    destinations = dict(DEFAULT_UPSTREAMS)
    if upstreams:
        if set(upstreams) - set(ROUTE_KEYS):
            raise ValueError("Unknown broker upstream")
        destinations.update(upstreams)
    for value in destinations.values():
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid broker upstream configuration")

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                    timeout=httpx.Timeout(1200, connect=10, pool=10)) as client:
            app.state.upstream = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    @app.exception_handler(ValueError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "Invalid broker request or configuration"}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(_request, _exc):
        return JSONResponse({"detail": "Protected setting does not exist"}, status_code=404)

    @app.exception_handler(OSError)
    async def inaccessible(_request, _exc):
        return JSONResponse({"detail": "Broker storage is unavailable"}, status_code=503)

    def admin(request: Request):
        if not hmac.compare_digest(_bearer(request), admin_token):
            raise HTTPException(403, "Broker administration requires owner authentication")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/control/status", dependencies=[Depends(admin)])
    def status(project_id: str | None = None, account_ref: str | None = None):
        return store.describe(project_id, account_ref)

    @app.put("/control/entries/{name}", dependencies=[Depends(admin)])
    async def put(name: str, request: Request, project_id: str | None = None):
        payload = await _payload(request, {"value", "state"})
        return store.put(name, project_id=project_id, **payload)

    @app.delete("/control/entries/{name}", dependencies=[Depends(admin)])
    def delete(name: str, project_id: str | None = None):
        return store.delete(name, project_id=project_id)

    @app.put("/control/bases/{name}", dependencies=[Depends(admin)])
    async def put_base(name: str, request: Request, account_ref: str | None = None):
        payload = await _payload(request, {"value"})
        if "value" not in payload:
            raise HTTPException(400, "A protected credential is required")
        return store.put_base(name, payload["value"], account_ref=account_ref)

    @app.delete("/control/bases/{name}", dependencies=[Depends(admin)])
    def delete_base(name: str, account_ref: str | None = None):
        return store.delete_base(name, account_ref=account_ref)

    @app.post("/control/context", dependencies=[Depends(admin)])
    async def context(request: Request):
        return store.context(**await _payload(request, {"project_id", "account_ref"}))

    @app.delete("/control/projects/{project_id}", dependencies=[Depends(admin)])
    def clear_project(project_id: str):
        return store.clear_project(project_id)

    async def proxy(request, route, suffix=""):
        if route not in ROUTE_KEYS:
            raise HTTPException(404, "Unsupported broker connection")
        if request.url.query:
            raise HTTPException(400, "Broker connections do not accept query parameters")
        try:
            key = store.resolve_token(_bearer(request), route)
        except PermissionError:
            raise HTTPException(401, "Invalid broker credential") from None
        if key is None:
            raise HTTPException(503, "Protected credential is not configured for this connection")
        body = await _body(request, MAX_REQUEST_BYTES)
        headers = {name: value for name, value in request.headers.items() if name in REQUEST_HEADERS}
        headers["authorization"] = "Bearer " + key
        headers["accept-encoding"] = "identity"
        client = request.app.state.upstream
        try:
            upstream_request = client.build_request(request.method, destinations[route].rstrip("/") + suffix, headers=headers, content=body)
            response = await client.send(upstream_request, stream=True, follow_redirects=False)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "Broker upstream is unavailable") from None
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise HTTPException(502, "Broker upstream redirect refused")
        response_headers = {name: value.replace(key, "[redacted]") for name, value in response.headers.items() if name in RESPONSE_HEADERS}
        response_headers["cache-control"] = "no-store"
        LOG.info("broker request route=%s method=%s status=%d", route, request.method, response.status_code)
        return StreamingResponse(_redacted_chunks(response, key), status_code=response.status_code, headers=response_headers)

    @app.api_route("/mcp/{connection}", methods=["GET", "POST", "DELETE"])
    async def mcp(connection: str, request: Request):
        return await proxy(request, "mcp/" + connection)

    @app.get("/model/{connection}/v1/models")
    async def models(connection: str, request: Request):
        return await proxy(request, "model/" + connection, "/models")

    @app.post("/model/{connection}/v1/chat/completions")
    async def completions(connection: str, request: Request):
        return await proxy(request, "model/" + connection, "/chat/completions")

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the private Agent Console credential broker")
    parser.add_argument("--state-dir", default="/var/lib/agent-console-broker")
    parser.add_argument("--admin-token-file", required=True)
    parser.add_argument("--upstreams-file", help="Host-admin JSON object of fixed route destinations (no credentials)")
    parser.add_argument("--port", default=8792, type=int)
    args = parser.parse_args(argv)
    # File permissions belong to the broker UID; Console receives its own
    # separately provisioned copy, never the broker's upstream-key store.
    token = _private_file(Path(args.admin_token_file)).strip()
    upstreams = json.loads(Path(args.upstreams_file).read_text()) if args.upstreams_file else None
    app = create_app(BrokerStore(args.state_dir), token, upstreams)
    # Avoid URL/query logging and debug transport dumps. Our own logger includes
    # only the fixed route, HTTP method and status code.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="info")


if __name__ == "__main__":
    main()
