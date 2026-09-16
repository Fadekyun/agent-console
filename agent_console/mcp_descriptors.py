from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_environment import (
    configured_allowlist,
    environment_catalog,
    validate_allowlisted_names,
)


class McpDescriptorError(ValueError):
    pass


@dataclass(frozen=True)
class McpHeaderReference:
    env: str
    prefix: str = ""


@dataclass(frozen=True)
class McpServerDescriptor:
    name: str
    transport: str
    url_env: str
    headers: dict[str, McpHeaderReference]
    access: str = "read"

    def required_environment_names(self) -> tuple[str, ...]:
        values = [self.url_env]
        values.extend(reference.env for reference in self.headers.values())
        return tuple(dict.fromkeys(values))


def _parse_server(name: str, raw: Any) -> McpServerDescriptor:
    if not isinstance(raw, dict):
        raise McpDescriptorError(f"MCP server {name!r} must be an object")
    transport = str(raw.get("transport", "streamable-http")).strip()
    if transport not in {"streamable-http", "sse"}:
        raise McpDescriptorError(f"MCP server {name!r} has unsupported transport {transport!r}")
    url_env = str(raw.get("url_env", "")).strip()
    if not url_env:
        raise McpDescriptorError(f"MCP server {name!r} requires url_env")
    access = str(raw.get("access", "read")).strip()
    if access not in {"read", "write"}:
        raise McpDescriptorError(f"MCP server {name!r} access must be read or write")
    headers_raw = raw.get("headers", {})
    if not isinstance(headers_raw, dict):
        raise McpDescriptorError(f"MCP server {name!r} headers must be an object")
    headers: dict[str, McpHeaderReference] = {}
    for header, value in headers_raw.items():
        if not isinstance(value, dict) or not str(value.get("env", "")).strip():
            raise McpDescriptorError(
                f"MCP server {name!r} header {header!r} must reference an env name"
            )
        headers[str(header)] = McpHeaderReference(
            env=str(value["env"]).strip(),
            prefix=str(value.get("prefix", "")),
        )
    descriptor = McpServerDescriptor(
        name=name,
        transport=transport,
        url_env=url_env,
        headers=headers,
        access=access,
    )
    # Descriptor files contain names only. A referenced name must be operator-
    # allowlisted, but it may be unavailable so the UI can report that state.
    validate_allowlisted_names(descriptor.required_environment_names())
    return descriptor


def load_mcp_descriptors(path: Path) -> dict[str, McpServerDescriptor]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpDescriptorError("MCP descriptor file is unreadable or invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise McpDescriptorError("MCP descriptor file must have version 1")
    servers = payload.get("servers", {})
    if not isinstance(servers, dict):
        raise McpDescriptorError("MCP descriptor servers must be an object")
    result: dict[str, McpServerDescriptor] = {}
    for raw_name, raw in servers.items():
        name = str(raw_name).strip()
        if not name or len(name) > 80:
            raise McpDescriptorError("MCP server names must be 1-80 characters")
        result[name] = _parse_server(name, raw)
    return result


def mcp_catalog(path: Path) -> list[dict[str, object]]:
    availability = {item["name"]: bool(item["available"]) for item in environment_catalog()}
    allowed = set(configured_allowlist())
    rows: list[dict[str, object]] = []
    for descriptor in load_mcp_descriptors(path).values():
        required = descriptor.required_environment_names()
        rows.append(
            {
                "name": descriptor.name,
                "transport": descriptor.transport,
                "access": descriptor.access,
                "required_environment": [
                    {
                        "name": name,
                        "allowlisted": name in allowed,
                        "available": availability.get(name, False),
                    }
                    for name in required
                ],
                "ready": all(name in allowed and availability.get(name, False) for name in required),
            }
        )
    return rows
