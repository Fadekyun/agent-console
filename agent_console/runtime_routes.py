from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import Settings
from .manager import SessionManager
from .mcp_descriptors import McpDescriptorError, mcp_catalog
from .runtime_environment import (
    EnvironmentBindingError,
    apply_bindings,
    binding_status,
    environment_catalog,
)
from .validation import validate_session_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "web" / "static"


class EnvironmentBindingRequest(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=64)
    restart: bool = False


def _mcp_path(settings: Settings) -> Path:
    configured = os.getenv("AGENT_CONSOLE_MCP_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (settings.config_dir or settings.state_dir / "config") / "mcp-servers.json"


def install(app, identity_dependency) -> None:
    @app.get("/environment")
    async def environment_page(_=Depends(identity_dependency)) -> FileResponse:
        return FileResponse(STATIC_ROOT / "environment.html")

    @app.get("/api/runtime-environment")
    async def runtime_environment_catalog(_=Depends(identity_dependency)) -> dict[str, object]:
        # Names and availability only. There is deliberately no value endpoint.
        return {"entries": environment_catalog()}

    @app.get("/api/mcp-servers")
    async def runtime_mcp_catalog(_=Depends(identity_dependency)) -> dict[str, object]:
        settings = Settings.from_env()
        try:
            entries = mcp_catalog(_mcp_path(settings))
        except (McpDescriptorError, EnvironmentBindingError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"entries": entries}

    @app.get("/api/sessions/{name}/environment-bindings")
    async def session_environment_bindings(
        name: str,
        _=Depends(identity_dependency),
    ) -> dict[str, object]:
        validate_session_name(name)
        manager = SessionManager()
        try:
            session = manager.inspect(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"session not found: {name}") from exc
        if not session.get("managed") or session.get("socket_scope") != "canonical":
            raise HTTPException(status_code=403, detail="environment bindings require a managed canonical session")
        if not session.get("running"):
            raise HTTPException(status_code=409, detail="session is not running")
        return binding_status(manager.tmux, name)

    @app.put("/api/sessions/{name}/environment-bindings")
    async def update_session_environment_bindings(
        name: str,
        payload: EnvironmentBindingRequest,
        auth=Depends(identity_dependency),
    ) -> dict[str, object]:
        validate_session_name(name)
        manager = SessionManager()
        try:
            session = manager.inspect(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"session not found: {name}") from exc
        if not session.get("managed") or session.get("socket_scope") != "canonical":
            raise HTTPException(status_code=403, detail="environment bindings require a managed canonical session")
        if not session.get("running"):
            raise HTTPException(status_code=409, detail="session is not running")
        try:
            result = apply_bindings(manager.tmux, name, payload.names)
        except EnvironmentBindingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Audit names only. Values must never enter database-backed audit state.
        manager.database.audit(
            "session.environment.updated",
            name,
            "success",
            actor=getattr(auth, "actor", None),
            surface=getattr(auth, "access_surface", "web"),
            details={"bindings": [item["name"] for item in result["bindings"]]},
        )
        if payload.restart:
            manager.restart(name)
            result = binding_status(manager.tmux, name)
            result["restarted"] = True
        else:
            result["restarted"] = False
        return result
