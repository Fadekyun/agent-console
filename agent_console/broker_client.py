"""Value-blind client for the optional separate-user credential broker."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

PROTECTED_NAMES = frozenset({"N8N_MCP_TOKEN", "DIRECTUS_MCP_TOKEN", "BUSHI_MCP_TOKEN",
                             "OPENROUTER_API_KEY", "CMD_API_KEY"})
CAPABILITY = "AGENT_CONSOLE_BROKER_CAPABILITY"
BROKER_URL = "AGENT_CONSOLE_BROKER_URL"
CONTROL_PREFIX = "AGENT_CONSOLE_BROKER_ADMIN"


class BrokerUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Credential broker unavailable; check its service and control configuration")


class BrokerClient:
    def __init__(self, url, admin_file, *, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"})):
            raise ValueError("Broker URL must use loopback HTTP or authenticated HTTPS")
        self.url = url.rstrip("/")
        self.admin_file = Path(admin_file) if admin_file else None
        self.transport = transport

    @classmethod
    def configured(cls):
        url, path = os.getenv(BROKER_URL), os.getenv("AGENT_CONSOLE_BROKER_ADMIN_FILE")
        # Managed subprocesses inherit only the data capability. A workflow
        # runner may construct a manager for bookkeeping without admin access.
        # Keep broker mode active so any attempted new launch fails closed.
        if os.getenv(CAPABILITY) and not path:
            if not url:
                raise BrokerUnavailable()
            return cls(url, None)
        if not url and not path:
            return None
        if not url or not path:
            raise BrokerUnavailable()
        return cls(url, path)

    def _token(self):
        if self.admin_file is None:
            raise BrokerUnavailable()
        fd = os.open(self.admin_file, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in {0, os.getuid()}:
                raise BrokerUnavailable()
            with os.fdopen(fd, encoding="ascii") as stream:
                fd = -1
                token = stream.read(4097).strip()
            if not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise BrokerUnavailable()
            return token
        finally:
            if fd >= 0:
                os.close(fd)

    def request(self, method, path, *, project_id=None, account_ref=None, payload=None):
        import httpx
        params = {k: v for k, v in {"project_id": project_id, "account_ref": account_ref}.items() if v is not None}
        try:
            with httpx.Client(transport=self.transport, timeout=10, trust_env=False, follow_redirects=False) as client:
                response = client.request(method, self.url + path, params=params, json=payload,
                                          headers={"Authorization": "Bearer " + self._token()})
            if response.status_code == 404:
                raise KeyError("Protected variable is not configured in this scope")
            if response.status_code in {400, 422}:
                raise ValueError("Invalid protected environment request")
            if not response.is_success:
                raise BrokerUnavailable()
            try:
                data = response.json()
            except ValueError:
                raise BrokerUnavailable() from None
            if not isinstance(data, dict):
                raise BrokerUnavailable()
            return data
        except (OSError, UnicodeError, httpx.HTTPError):
            raise BrokerUnavailable() from None

    def describe(self, project_id=None, account_ref=None):
        data = self.request("GET", "/control/status", project_id=project_id, account_ref=account_ref)
        if (not isinstance(data.get("revision"), int)
                or not all(isinstance(data.get(k), list) for k in ("entries", "effective", "routes"))
                or not all(isinstance(e, dict) and e.get("name") in PROTECTED_NAMES for e in data["entries"] + data["effective"])
                or not all(isinstance(route, str) for route in data["routes"])):
            raise BrokerUnavailable()
        return data

    def put(self, name, *, value=None, state="enabled", project_id=None):
        payload = {"state": state}
        if value is not None:
            payload["value"] = value
        return self.request("PUT", "/control/entries/" + name, project_id=project_id, payload=payload)

    def delete(self, name, *, project_id=None):
        return self.request("DELETE", "/control/entries/" + name, project_id=project_id)

    def clear_project(self, project_id):
        return self.request("DELETE", "/control/projects/" + project_id)

    def context(self, project_id=None, account_ref=None):
        data = self.request("POST", "/control/context", payload={"project_id": project_id, "account_ref": account_ref})
        token = data.get("token")
        if (not isinstance(token, str) or not 20 <= len(token) <= 4096
                or not all(33 <= ord(c) <= 126 for c in token)
                or not isinstance(data.get("routes"), list)
                or not all(isinstance(route, str) for route in data["routes"])
                or data.get("project_id") != project_id or data.get("account_ref") != account_ref):
            raise BrokerUnavailable()
        return data

    def environment(self, project_id=None, account_ref=None):
        data = self.context(project_id, account_ref)
        return {BROKER_URL: self.url, CAPABILITY: data["token"]}

    def require_model(self, context, project_id=None):
        provider = context.get("provider")
        if provider not in {"commandcode", "openrouter"}:
            return
        status = self.describe(project_id, context.get("secret_ref") or context.get("name"))
        if "model/" + provider not in status.get("routes", []):
            raise ValueError("Set the selected provider key in Environment before starting this session")


def strip_protected(values):
    """Remove raw upstream keys and Console-only admin settings before launch."""
    return {k: v for k, v in values.items() if k not in PROTECTED_NAMES and not k.startswith(CONTROL_PREFIX)}
