from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import json
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn.config
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .config import Settings
from .logging_config import configure_logging, configure_uvicorn_logging
from .manager import SessionManager
from .profiles import profile_summaries
from .skills import (
    approve_superpower,
    assign_skill,
    doctor_skills,
    enrich_catalog_with_assignments,
    get_effective_skills,
    list_assignments,
    list_superpower_approvals,
    revoke_superpower,
    skill_catalog,
    sync_skills,
    unassign_skill,
    validate_profile_skills,
)
from .validation import TOOLS, validate_session_name


uvicorn.config.LOGGING_CONFIG = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = Settings.from_env()
    configure_logging(log_dir=s.log_dir, retention_days=s.log_retention_days, backup_count=s.log_backup_count)
    configure_uvicorn_logging()
    yield


configure_logging()
configure_uvicorn_logging()
log = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "web" / "static"
XTERM_ROOT = PROJECT_ROOT / "node_modules" / "@xterm"
EXPECTED_LOGIN = os.getenv("AGENT_CONSOLE_TAILSCALE_LOGIN", "").strip().lower()
MAX_PTY_CLIENTS = int(os.getenv("AGENT_CONSOLE_MAX_PTY_CLIENTS", "2"))
LAN_NETWORK = ipaddress.ip_network(
    os.getenv("AGENT_CONSOLE_LAN_CIDR", "127.0.0.1/32"), strict=False
)
TRUSTED_HOSTS = [
    value.strip()
    for value in os.getenv(
        "AGENT_CONSOLE_TRUSTED_HOSTS",
        "localhost,127.0.0.1",
    ).split(",")
    if value.strip()
]


@dataclass(frozen=True)
class AuthContext:
    actor: str
    access_surface: str


class CreateSessionRequest(BaseModel):
    tool: str
    profile: str = "general"
    name: str | None = Field(default=None, max_length=80)
    task: str | None = Field(default=None, max_length=12000)
    repository: str | None = None
    worktree: bool = False
    auth_context: str | None = Field(default=None, max_length=64)
    agent_mode: str | None = Field(default=None, pattern="^(plan|build|auto)$")
    provider: str | None = Field(default=None, pattern="^(openrouter|opencode-go)$")
    model: str | None = Field(default=None, max_length=240)
    project_id: str | None = Field(default=None, max_length=80)


class ModelEstimateRequest(BaseModel):
    provider: str = Field(pattern="^(openrouter|opencode-go)$")
    uncached_input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class ConfirmRequest(BaseModel):
    confirmed: bool = False
    allow_unmanaged: bool = False
    understand_unmanaged: bool = False


class WaitForChildrenRequest(BaseModel):
    timeout: int | None = Field(default=None, ge=1, le=3600)
    poll_interval: int | None = Field(default=None, ge=1, le=120)


class AttentionRequest(BaseModel):
    state: str = Field(pattern="^(normal|needs_input|blocked|ready_for_review)$")
    note: str | None = Field(default=None, max_length=1000)


class DelegationRequest(BaseModel):
    profile: str = Field(min_length=1)
    tool: str = "codex"
    auth_context: str | None = Field(default=None, max_length=64)
    agent_mode: str | None = Field(default=None, pattern="^(plan|build|auto)$")
    task: str = Field(min_length=1, max_length=12000)
    repository: str | None = None
    name: str | None = Field(default=None, max_length=80)


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    purpose: str | None = Field(default=None, max_length=2000)
    parent_session: str | None = Field(default=None, max_length=80)

class GroupMemberRequest(BaseModel):
    session_name: str = Field(min_length=1, max_length=80)

class PlanExecuteRequest(BaseModel):
    confirmed: bool = False
    profile: str = Field(default="coder", pattern="^(coder|bugfix)$")
    name: str | None = Field(default=None, max_length=80)
    allow_revision_change: bool = False
    project_id: str | None = Field(default=None, max_length=80)

class RecordEvidenceRequest(BaseModel):
    evidence_type: str = Field(min_length=1)
    result: str = Field(min_length=1)
    candidate_sha: str = Field(min_length=1, max_length=128)
    detail: str | None = Field(default=None, max_length=5000)
    capability: str | None = Field(default=None, min_length=1, max_length=256)

class PromoteRequest(BaseModel):
    confirmed: bool = False
    candidate_sha: str | None = Field(default=None, max_length=128)

class AssignSessionRequest(BaseModel):
    session_name: str = Field(min_length=1, max_length=80)

class SkillAssignRequest(BaseModel):
    profile: str = Field(min_length=1, pattern="^[a-z_]+$")
    skill_name: str = Field(min_length=1, max_length=200)

class SkillEffectiveRequest(BaseModel):
    profile: str = Field(min_length=1, pattern="^[a-z_]+$")

class SkillApprovalRequest(BaseModel):
    profile: str = Field(min_length=1, pattern="^[a-z_]+$")
    skill_name: str = Field(min_length=1, max_length=200)

class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repository: str | None = Field(default=None, max_length=1000)
    description: str | None = Field(default=None, max_length=2000)

class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    repository: str | None = Field(default=None, max_length=1000)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(active|paused|completed)$")


def create_app(manager: SessionManager | None = None) -> FastAPI:
    session_manager = manager or SessionManager()
    pty_clients: dict[str, int] = defaultdict(int)
    app = FastAPI(title="Agent Console", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def require_identity(
        request: Request,
        tailscale_user_login: str | None = Header(default=None),
    ) -> AuthContext:
        if not EXPECTED_LOGIN:
            raise HTTPException(status_code=503, detail="Tailscale login allowlist is not configured")
        login = (tailscale_user_login or "").strip().lower()
        if login:
            if login == EXPECTED_LOGIN:
                return AuthContext(actor=login, access_surface="tailscale")
            session_manager.database.audit(
                "authentication.denied",
                login,
                "denied",
                actor=login,
                surface="web",
            )
            raise HTTPException(status_code=403, detail="Tailscale identity is not allowed")
        client_host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(client_host) in LAN_NETWORK
        except ValueError:
            local = False
        if not local:
            session_manager.database.audit(
                "authentication.denied",
                client_host or None,
                "denied",
                actor=client_host or "unknown",
                surface="web",
            )
            raise HTTPException(status_code=403, detail="Tailscale identity or trusted LAN is required")
        return AuthContext(actor=client_host, access_surface="local-lan")

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok\n"

    @app.get("/")
    async def dashboard(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/desktop")
    async def desktop(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/mobile")
    async def mobile(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(STATIC_ROOT / "mobile.html")

    @app.get("/terminal")
    async def terminal(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(STATIC_ROOT / "terminal.html")

    @app.get("/vendor/xterm.mjs")
    async def xterm_module(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(XTERM_ROOT / "xterm" / "lib" / "xterm.mjs")

    @app.get("/vendor/xterm.css")
    async def xterm_css(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(XTERM_ROOT / "xterm" / "css" / "xterm.css")

    @app.get("/vendor/addon-fit.mjs")
    async def fit_module(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(XTERM_ROOT / "addon-fit" / "lib" / "addon-fit.mjs")

    @app.get("/api/me")
    async def me(auth: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        catalog = session_manager.tool_catalog()
        return {
            "login": auth.actor,
            "access_surface": auth.access_surface,
            "tools": [item["name"] for item in catalog],
            "tool_status": catalog,
            "auth_contexts": session_manager.auth_contexts(),
            "default_tool": "codex",
            "default_agent_modes": {"codex": "auto", "opencode": "plan"},
            "profiles": profile_summaries(),
        }

    @app.get("/api/sessions")
    async def sessions(
        state: str = "all", _: AuthContext = Depends(require_identity)
    ) -> list[dict[str, Any]]:
        if state not in {"active", "history", "all"}:
            raise HTTPException(status_code=400, detail="state must be active, history, or all")
        rows = session_manager.list_sessions()
        if state == "active":
            return [row for row in rows if row["running"]]
        if state == "history":
            return [row for row in rows if not row["running"]]
        return rows

    @app.get("/api/profiles")
    async def profiles(_: AuthContext = Depends(require_identity)) -> list[dict[str, Any]]:
        return session_manager.list_profiles()

    @app.get("/api/profiles/{name}")
    async def inspect_profile_api(
        name: str, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any]:
        return session_manager.inspect_profile(name)

    class ProfileUpdateRequest(BaseModel):
        content: str = Field(min_length=1)

    @app.put("/api/profiles/{name}")
    async def write_profile_api(
        name: str,
        payload: ProfileUpdateRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.write_profile(name, payload.content)

    @app.get("/api/plans")
    async def plans(_: AuthContext = Depends(require_identity)) -> list[dict[str, Any]]:
        return session_manager.list_plans()

    @app.get("/api/delegations")
    async def delegations(_: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        return session_manager.session_tree()

    @app.get("/api/session-groups")
    async def session_groups(_: AuthContext = Depends(require_identity)) -> list[dict[str, Any]]:
        return session_manager.list_groups()

    @app.post("/api/session-groups")
    async def create_session_group(
        payload: GroupCreateRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.create_group(
            payload.name, payload.purpose, payload.parent_session,
            actor=auth.actor, surface="web",
        )

    @app.get("/api/session-groups/{group_id}")
    async def get_session_group(
        group_id: str, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any]:
        return session_manager.get_group(group_id)

    @app.post("/api/session-groups/{group_id}/members")
    async def add_group_member(
        group_id: str,
        payload: GroupMemberRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.add_group_session(
            group_id, payload.session_name,
            actor=auth.actor, surface="web",
        )

    @app.delete("/api/session-groups/{group_id}/members/{session_name}")
    async def remove_group_member(
        group_id: str,
        session_name: str,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.remove_group_session(
            group_id, validate_session_name(session_name),
            actor=auth.actor, surface="web",
        )

    @app.post("/api/session-groups/{group_id}/open")
    async def open_session_group(
        group_id: str, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any]:
        return session_manager.open_group(group_id)

    @app.get("/api/projects")
    async def projects_list(_: AuthContext = Depends(require_identity)) -> list[dict[str, Any]]:
        return session_manager.list_projects()

    @app.post("/api/projects")
    async def projects_create(
        payload: ProjectCreateRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.create_project(
            payload.name, payload.repository, payload.description,
            actor=auth.actor, surface="web",
        )

    @app.get("/api/projects/{project_id}")
    async def projects_get(
        project_id: str, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any]:
        return session_manager.get_project(project_id)

    @app.put("/api/projects/{project_id}")
    async def projects_update(
        project_id: str,
        payload: ProjectUpdateRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.update_project(
            project_id,
            name=payload.name,
            repository=payload.repository,
            description=payload.description,
            status=payload.status,
            actor=auth.actor, surface="web",
        )

    @app.delete("/api/projects/{project_id}")
    async def projects_delete(
        project_id: str,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, str]:
        session_manager.delete_project(
            project_id, actor=auth.actor, surface="web",
        )
        return {"status": "deleted"}

    @app.post("/api/projects/{project_id}/assign")
    async def projects_assign(
        project_id: str,
        payload: AssignSessionRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.assign_session_to_project(
            payload.session_name, project_id,
            actor=auth.actor, surface="web",
        )

    @app.post("/api/projects/{project_id}/unassign")
    async def projects_unassign(
        project_id: str,
        payload: AssignSessionRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.unassign_session_from_project(
            payload.session_name, project_id,
            actor=auth.actor, surface="web",
        )

    @app.get("/api/sessions/{name}/wait-status")
    async def wait_status(
        name: str, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any] | None:
        return session_manager.wait_status(name)

    @app.post("/api/sessions/{name}/wait-for-children")
    async def wait_for_children(
        name: str,
        payload: WaitForChildrenRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.wait_for_children(
            name,
            timeout=payload.timeout,
            poll_interval=payload.poll_interval,
        )

    @app.get("/api/sessions/{name}/review")
    async def review_session(
        name: str,
        response: Response,
        lines: int = Query(default=200, ge=1, le=1000),
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return session_manager.review_session(validate_session_name(name), lines=lines)

    @app.get("/api/sessions/{name}/brief")
    async def session_brief(
        name: str, response: Response, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return session_manager.session_brief(validate_session_name(name))

    @app.patch("/api/sessions/{name}/attention")
    async def update_attention(
        name: str,
        payload: AttentionRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.set_attention(
            validate_session_name(name),
            state=payload.state,
            note=payload.note,
            actor=auth.actor,
            surface="web",
        )

    @app.get("/api/models")
    async def models(
        provider: str = Query(pattern="^(openrouter|opencode-go)$"),
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.model_catalogue(provider)

    @app.post("/api/models/refresh")
    async def refresh_models(
        provider: str = Query(pattern="^(openrouter|opencode-go)$"),
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.model_catalogue(provider, refresh=True)

    @app.post("/api/models/estimate")
    async def estimate_models_api(
        payload: ModelEstimateRequest, _: AuthContext = Depends(require_identity)
    ) -> dict[str, Any]:
        return session_manager.estimate_models(
            payload.provider,
            uncached_input_tokens=payload.uncached_input_tokens,
            cached_input_tokens=payload.cached_input_tokens,
            output_tokens=payload.output_tokens,
            reasoning_tokens=payload.reasoning_tokens,
        )

    @app.get("/api/plans/{plan_id}")
    async def inspect_plan(
        plan_id: str,
        response: Response,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return session_manager.inspect_plan(plan_id)

    @app.post("/api/sessions")
    async def create_session(
        payload: CreateSessionRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.create(
            tool=payload.tool,
            profile=payload.profile,
            name=payload.name,
            task=payload.task,
            repository=payload.repository,
            worktree=payload.worktree,
            auth_context=payload.auth_context,
            agent_mode=payload.agent_mode,
            provider=payload.provider,
            model=payload.model,
            creator_surface="web",
            project_id=payload.project_id,
        )

    @app.post("/api/sessions/{parent}/delegations")
    async def create_delegation(
        parent: str,
        payload: DelegationRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.delegate(
            profile=payload.profile,
            parent=parent,
            task=payload.task,
            repository=payload.repository,
            tool=payload.tool,
            name=payload.name,
            auth_context=payload.auth_context,
            agent_mode=payload.agent_mode,
            creator_surface="web",
        )

    @app.post("/api/plans/{plan_id}/execute")
    async def execute_plan(
        plan_id: str,
        payload: PlanExecuteRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        if not payload.confirmed:
            raise HTTPException(status_code=400, detail="plan execution confirmation is required")
        return session_manager.execute_plan(
            plan_id,
            profile=payload.profile,
            name=payload.name,
            allow_revision_change=payload.allow_revision_change,
            creator_surface="web",
            project_id=payload.project_id,
        )

    @app.post("/api/plans/{plan_id}/evidence")
    async def record_evidence(
        plan_id: str,
        payload: RecordEvidenceRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.record_evidence(
            plan_id,
            evidence_type=payload.evidence_type,
            result=payload.result,
            candidate_sha=payload.candidate_sha,
            detail=payload.detail,
            capability=payload.capability,
        )

    @app.get("/api/plans/{plan_id}/gate")
    async def release_gate(
        plan_id: str,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.check_release_gate(plan_id)

    @app.post("/api/plans/{plan_id}/promote")
    async def promote_plan_api(
        plan_id: str,
        payload: PromoteRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        if not payload.confirmed:
            raise HTTPException(status_code=400, detail="promote confirmation is required")
        return session_manager.promote_plan(
            plan_id,
            candidate_sha=payload.candidate_sha,
            actor=auth.actor,
            surface="web",
        )

    @app.post("/api/sessions/{name}/interrupt")
    async def interrupt(name: str, _: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        return session_manager.interrupt(validate_session_name(name))

    @app.post("/api/sessions/{name}/restart")
    async def restart(name: str, _: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        return session_manager.restart(validate_session_name(name))

    @app.get("/api/skills")
    async def skills_api(_: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        catalog = skill_catalog()
        assignments = list_assignments(session_manager.database)
        return enrich_catalog_with_assignments(catalog, assignments)

    class SkillsSyncRequest(BaseModel):
        pass

    @app.post("/api/skills/sync")
    async def skills_sync(_: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        return sync_skills()

    @app.post("/api/skills/doctor")
    async def skills_doctor(_: AuthContext = Depends(require_identity)) -> dict[str, Any]:
        return doctor_skills()

    @app.get("/api/deploy/releases")
    async def deploy_releases(_: AuthContext = Depends(require_identity)) -> list[dict[str, Any]]:
        return session_manager.list_releases()

    @app.get("/api/deploy/current")
    async def deploy_current(_: AuthContext = Depends(require_identity)) -> dict[str, Any] | None:
        return session_manager.current_release()

    @app.get("/api/deploy/canary")
    async def deploy_canary(_: AuthContext = Depends(require_identity)) -> dict[str, Any] | None:
        return session_manager.canary_release()

    @app.get("/api/skills/assignments")
    async def skills_assignments(_: AuthContext = Depends(require_identity)) -> list[dict[str, Any]]:
        return list_assignments(session_manager.database)

    @app.post("/api/skills/assign")
    async def skills_assign(
        payload: SkillAssignRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return assign_skill(
            session_manager.database,
            payload.profile,
            payload.skill_name,
            actor=auth.actor,
            surface=auth.access_surface,
        )

    @app.post("/api/skills/unassign")
    async def skills_unassign(
        payload: SkillAssignRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return unassign_skill(
            session_manager.database,
            payload.profile,
            payload.skill_name,
            actor=auth.actor,
            surface=auth.access_surface,
        )

    @app.post("/api/skills/effective")
    async def skills_effective(
        payload: SkillEffectiveRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return get_effective_skills(session_manager.database, payload.profile)

    @app.post("/api/skills/validate")
    async def skills_validate(
        payload: SkillEffectiveRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return validate_profile_skills(session_manager.database, payload.profile)

    @app.post("/api/skills/approve")
    async def skills_approve(
        payload: SkillApprovalRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return approve_superpower(
            session_manager.database,
            payload.profile,
            payload.skill_name,
            actor=auth.actor,
            surface=auth.access_surface,
        )

    @app.post("/api/skills/revoke")
    async def skills_revoke(
        payload: SkillApprovalRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return revoke_superpower(
            session_manager.database,
            payload.profile,
            payload.skill_name,
            actor=auth.actor,
            surface=auth.access_surface,
        )

    @app.get("/api/skills/approvals")
    async def skills_approvals_list(
        _: AuthContext = Depends(require_identity),
    ) -> list[dict[str, Any]]:
        return list_superpower_approvals(session_manager.database)

    @app.post("/api/sessions/{name}/kill")
    async def kill(
        name: str,
        payload: ConfirmRequest,
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        name = validate_session_name(name)
        session = session_manager.inspect(name)
        if not payload.confirmed:
            raise HTTPException(status_code=400, detail="kill confirmation is required")
        if not session["managed"] and not payload.understand_unmanaged:
            raise HTTPException(status_code=400, detail="unmanaged-session acknowledgement is required")
        if not session["managed"] and not payload.allow_unmanaged:
            raise HTTPException(status_code=400, detail="allow_unmanaged is required")
        return session_manager.kill(name, allow_unmanaged=payload.allow_unmanaged)

    async def operation_error(request: Request, exc: Exception):
        from fastapi.responses import JSONResponse

        log.warning("request=%s %s error=%s", request.method, request.url.path, exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    for exception_type in (
        ValueError,
        KeyError,
        FileExistsError,
        FileNotFoundError,
        PermissionError,
        RuntimeError,
    ):
        app.add_exception_handler(exception_type, operation_error)

    @app.websocket("/ws/sessions/{name}")
    async def session_terminal(websocket: WebSocket, name: str) -> None:
        login = (websocket.headers.get("tailscale-user-login") or "").strip().lower()
        client_host = websocket.client.host if websocket.client else ""
        if login:
            allowed = bool(EXPECTED_LOGIN and login == EXPECTED_LOGIN)
            actor = login
        else:
            try:
                allowed = ipaddress.ip_address(client_host) in LAN_NETWORK
            except ValueError:
                allowed = False
            actor = client_host
        if not allowed:
            log.warning("ws session=%s actor=%s denied", name, actor)
            await websocket.close(code=4403, reason="Tailscale identity or trusted LAN is required")
            return
        try:
            name = validate_session_name(name)
        except ValueError:
            await websocket.close(code=4400, reason="invalid session name")
            return
        tmux = session_manager.tmux_for_name(name)
        if not tmux.exists(name):
            await websocket.close(code=4404, reason="session is not running")
            return
        client_key = f"{tmux.scope}:{name}"
        if pty_clients[client_key] >= MAX_PTY_CLIENTS:
            await websocket.close(code=4429, reason="PTY client limit reached")
            return

        await websocket.accept()
        pty_clients[client_key] += 1
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        process = subprocess.Popen(
            tmux.command("attach-session", "-t", name),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid,
            env={**os.environ, "TERM": "xterm-256color"},
        )
        os.close(slave_fd)
        session_manager.database.audit(
            "session.attached",
            name,
            "success",
            actor=actor,
            surface="web",
        )
        log.info("ws session=%s actor=%s surface=web attached", name, actor)

        async def read_pty() -> None:
            try:
                while process.poll() is None:
                    data = await asyncio.to_thread(os.read, master_fd, 8192)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except (OSError, RuntimeError, WebSocketDisconnect) as pty_exc:
                log.warning("ws session=%s pty error=%s", name, pty_exc, exc_info=True)
                return
            finally:
                if process.poll() is not None:
                    try:
                        await websocket.close(code=4001, reason="session ended")
                    except RuntimeError:
                        pass

        reader = asyncio.create_task(read_pty())
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    os.write(master_fd, message["bytes"])
                elif message.get("text"):
                    control = json.loads(message["text"])
                    if control.get("type") == "resize":
                        cols = max(20, min(int(control.get("cols", 80)), 500))
                        rows = max(5, min(int(control.get("rows", 24)), 200))
                        fcntl.ioctl(
                            master_fd,
                            termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0),
                        )
                        os.killpg(process.pid, signal.SIGWINCH)
                    elif control.get("type") == "detach":
                        await websocket.close(code=4000, reason="detached by user")
                        break
        except (json.JSONDecodeError, OSError, WebSocketDisconnect) as ws_exc:
            log.warning("ws session=%s error=%s", name, ws_exc, exc_info=True)
        finally:
            reader.cancel()
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 3)
                except subprocess.TimeoutExpired:
                    process.kill()
            os.close(master_fd)
            pty_clients[client_key] = max(0, pty_clients[client_key] - 1)

    return app


app = create_app()
