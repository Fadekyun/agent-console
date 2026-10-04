from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import json
import logging
import os
import pty
import re
import signal
import sqlite3
import struct
import subprocess
import termios
from collections import defaultdict
from contextlib import ExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn.config
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .request_identity import (AuthContext, IdentityDenied, allowed_origins, authorize_browser_origin,
                               authorize_identity, proxy_networks)
from .config import Settings
from .database import Database
from .logging_config import configure_logging, configure_uvicorn_logging
from .manager import SessionManager
from .pty_transport import PtyTransport
from .integration_requests import IntegrationService
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
    # Hold one writer connection for the service lifetime so live WAL sidecars
    # stay materialized for the guarded read-only CLI inspection routes, which
    # must not create or change them. Skipped until the database exists so the
    # lifespan never creates state.
    with ExitStack() as stack:
        if s.database_path.is_file():
            stack.enter_context(Database(s.database_path).keepalive())
        if os.getenv('AGENT_CONSOLE_CANARY') == '1':
            yield
            return
        from .workflow_engine import WorkflowEngine
        engine=WorkflowEngine(app.state.session_manager)
        async def dispatch_loop():
            while True:
                try:await asyncio.to_thread(engine.tick)
                except Exception:log.exception('Workflow dispatch sweep failed; receipts retained for reconciliation')
                await asyncio.sleep(2)
        worker=asyncio.create_task(dispatch_loop())
        try:yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):await worker


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


TRUSTED_PROXY_NETWORKS = proxy_networks(os.getenv(
    "AGENT_CONSOLE_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128"))
ALLOWED_ORIGINS = allowed_origins(os.getenv(
    "AGENT_CONSOLE_ALLOWED_ORIGINS",
    "http://localhost:3210,http://127.0.0.1:3210"))


class CreateSessionRequest(BaseModel):
    tool: str
    profile: str = "general"
    name: str | None = Field(default=None, max_length=80)
    task: str | None = Field(default=None, max_length=12000)
    repository: str | None = None
    worktree: bool = False
    auth_context: str | None = Field(default=None, max_length=64)
    agent_mode: str | None = Field(default=None, pattern="^(plan|build|auto)$")
    provider: str | None = Field(default=None, pattern="^[a-z0-9-]+$")
    model: str | None = Field(default=None, max_length=240)
    reasoning_effort: str | None = Field(
        default=None, pattern="^(low|medium|high|xhigh|max|ultra)$"
    )
    plan_reasoning_effort: str | None = Field(
        default=None, pattern="^(low|medium|high|xhigh|max|ultra)$"
    )
    project_id: str | None = Field(default=None, max_length=80)


class ModelEstimateRequest(BaseModel):
    provider: str = Field(pattern="^[a-z0-9-]+$")
    uncached_input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class ConfirmRequest(BaseModel):
    confirmed: bool = False
    allow_unmanaged: bool = False
    understand_unmanaged: bool = False


class WaitForChildrenRequest(BaseModel):
    child_selectors: list[str] | None = Field(default=None, min_length=1, max_length=256)
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
    model: str | None = Field(default=None, max_length=240)
    reasoning_effort: str | None = Field(
        default=None, pattern="^(low|medium|high|xhigh|max|ultra)$"
    )
    plan_reasoning_effort: str | None = Field(
        default=None, pattern="^(low|medium|high|xhigh|max|ultra)$"
    )
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
    if os.getenv('AGENT_CONSOLE_CANARY') == '1':
        from .maintenance import CanaryOnlyMiddleware
        app.add_middleware(CanaryOnlyMiddleware)
    app.state.session_manager=session_manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def identity_for(connection):
        return authorize_identity(connection, expected_login=EXPECTED_LOGIN,
                                  lan_network=LAN_NETWORK, trusted_proxies=TRUSTED_PROXY_NETWORKS)

    def require_identity(request: Request) -> AuthContext:
        try:
            return identity_for(request)
        except IdentityDenied as exc:
            if exc.status == 403:
                peer = request.client.host if request.client else "unknown"
                session_manager.database.audit("authentication.denied", peer, "denied", actor=peer, surface="web")
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None

    # Presence reads share policy without manager/database side effects.
    def presence_identity(request: Request):
        try:
            return identity_for(request)
        except IdentityDenied as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None

    from .presence_routes import install as install_presence
    install_presence(app, presence_identity)

    from .jev_ghost import install as install_jev_ghost
    install_jev_ghost(app, require_identity)

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz(response: Response) -> str:
        response.headers['X-Agent-Console-Pid'] = str(os.getpid())
        response.headers['X-Agent-Console-Release'] = PROJECT_ROOT.name
        identity = os.getenv('AGENT_CONSOLE_HEALTH_IDENTITY')
        if identity:
            response.headers['X-Agent-Console-Identity'] = identity
        return "ok\n"

    @app.get("/")
    async def dashboard(_: AuthContext = Depends(require_identity)) -> FileResponse:
        page = "workbench.html" if os.getenv("AGENT_CONSOLE_UI") == "workbench" else "index.html"
        return FileResponse(STATIC_ROOT / page)

    @app.get("/work")
    async def workbench(_: AuthContext = Depends(require_identity)) -> FileResponse:
        return FileResponse(STATIC_ROOT / "workbench.html")

    def interface_links() -> dict[str, str]:
        from urllib.parse import urlsplit
        links = {"label": os.getenv("AGENT_CONSOLE_INSTANCE_LABEL", "Agent Console"),
                 "workspace": str(session_manager.settings.workspace_root),
                 "terminal_scroll": "tmux"}
        for key in ("current", "staging"):
            value = os.getenv(f"AGENT_CONSOLE_{key.upper()}_URL", "")
            try:
                parsed = urlsplit(value)
                safe = parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password
            except ValueError:
                safe = False
            links[f"{key}_url"] = value if safe else ""
        return links

    @app.get("/api/interface")
    async def interface(_: AuthContext = Depends(require_identity)) -> dict[str, str]:
        return interface_links()

    @app.get("/versions", response_class=HTMLResponse)
    async def versions(_: AuthContext = Depends(require_identity)) -> str:
        from html import escape
        links = interface_links()
        choices = "".join(
            f'<p><a href="{escape(links[key + "_url"], quote=True)}">{label}</a></p>'
            for key, label in (("current", "Current console"), ("staging", "New console · staging"))
            if links[key + "_url"]
        )
        return ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Choose your Agent Console</title><style>'
                'body{font:17px/1.6 system-ui;background:#f5f6f1;color:#20352b;max-width:600px;margin:12vh auto;padding:24px}'
                'a{display:block;padding:20px;border:1px solid #dce2d9;border-radius:10px;color:#25583d;background:white}'
                '</style><h1>Choose your console</h1>' + choices +
                '<p>Both versions stay available. Each keeps its own sessions, files and settings.</p></html>')

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
            "session_limits": {"managed": session_manager.settings.max_managed_sessions, "children": session_manager.settings.max_children_per_parent},
            "default_agent_modes": {"codex": "auto", "codex-pro": "auto", "opencode": "plan"},
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
            child_selectors=payload.child_selectors,
        )

    def require_session_identity(name: str, expected_id: str | None) -> None:
        if expected_id is None:
            return
        with session_manager.database.connect() as conn:
            current = conn.execute("SELECT id FROM sessions WHERE tmux_name=?", (name,)).fetchone()
        if current is None or current["id"] != expected_id:
            raise HTTPException(status_code=409, detail="session identity changed")

    @app.get("/api/sessions/{name}/review")
    async def review_session(
        name: str,
        response: Response,
        lines: int = Query(default=200, ge=1, le=1000),
        session_id: str | None = Query(default=None),
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        name = validate_session_name(name)
        require_session_identity(name, session_id)
        result = session_manager.review_session(name, lines=lines)
        require_session_identity(name, session_id)
        return result

    @app.get("/api/sessions/{name}/brief")
    async def session_brief(
        name: str, response: Response, session_id: str | None = Query(default=None),
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        name = validate_session_name(name)
        require_session_identity(name, session_id)
        result = session_manager.session_brief(name)
        require_session_identity(name, session_id)
        return result

    @app.get("/api/integration/plan-requests/{request_id}/result")
    async def integration_plan_result(
        request_id: str,
        response: Response,
        identity: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        if identity.access_surface != "tailscale" or identity.actor != EXPECTED_LOGIN:
            raise HTTPException(status_code=403, detail="owner identity is required")
        response.headers["Cache-Control"] = "no-store"
        return IntegrationService(session_manager).result(request_id)

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
        provider: str = Query(pattern="^[a-z0-9-]+$"),
        _: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        return session_manager.model_catalogue(provider)

    @app.post("/api/models/refresh")
    async def refresh_models(
        provider: str = Query(pattern="^[a-z0-9-]+$"),
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
            reasoning_effort=payload.reasoning_effort,
            plan_reasoning_effort=payload.plan_reasoning_effort,
            creator_surface="web",
            project_id=payload.project_id,
        )

    @app.post("/api/sessions/{parent}/children")
    async def add_child_session(
        parent: str,
        payload: CreateSessionRequest,
        auth: AuthContext = Depends(require_identity),
    ) -> dict[str, Any]:
        # A human starts this session explicitly. Agent delegation continues to
        # use its existing role restrictions and never gains this capability.
        source = session_manager.inspect(parent)
        if not source.get("managed") or source.get("execution_kind") == "integration-plan":
            raise HTTPException(status_code=400, detail="Choose a managed interactive parent session")
        result = session_manager.create(
            **{**payload.model_dump(),
               "repository": payload.repository or source.get("repository"),
               "project_id": payload.project_id or source.get("project_id")},
            parent_session_id=source["id"], creator_surface="web",
        )
        session_manager.database.audit(
            "session.child_added", result["tmux_name"], "success",
            actor=auth.actor, surface="web",
            details={"parent": parent, "profile": payload.profile},
        )
        return result

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
            model=payload.model,
            reasoning_effort=payload.reasoning_effort,
            plan_reasoning_effort=payload.plan_reasoning_effort,
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

    from .workflow_dispatch_api import dispatch_routes
    app.include_router(dispatch_routes(session_manager, require_identity))
    from .workflow_release_api import release_routes
    app.include_router(release_routes(session_manager, require_identity))
    from .workbench_state import workbench_routes
    app.include_router(workbench_routes(session_manager, require_identity))
    from .workbench_launch_api import launch_routes
    app.include_router(launch_routes(session_manager, require_identity))
    from .workflow_api import workflow_routes, agent_workflow_routes
    app.include_router(agent_workflow_routes(session_manager))
    from .session_control_api import session_control_routes
    app.include_router(session_control_routes(session_manager))
    app.include_router(workflow_routes(session_manager, require_identity))

    from .skill_api import skill_routes
    app.include_router(skill_routes(session_manager, require_identity))
    from .environment_api import environment_routes
    app.include_router(environment_routes(session_manager, require_identity, STATIC_ROOT))

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
        try:
            authorize_browser_origin(websocket, ALLOWED_ORIGINS)
            identity = identity_for(websocket)
        except IdentityDenied as exc:
            peer = websocket.client.host if websocket.client else "unknown"
            log.warning("ws session=%s peer=%s denied", name, peer)
            await websocket.close(code=4000 + exc.status, reason=exc.detail)
            return
        actor = identity.actor
        try:
            name = validate_session_name(name)
        except ValueError:
            await websocket.close(code=4400, reason="invalid session name")
            return
        try:
            tmux = session_manager.tmux_for_name(name)
            inspected = session_manager.inspect(name)
            session_id = inspected["id"]
            running = tmux.exists(name)
        except KeyError:
            await websocket.close(code=4404, reason="session is not running")
            return
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            log.warning("ws session=%s inspection error=%s", name, exc)
            await websocket.close(code=1011, reason="session inspection unavailable; retry")
            return
        expected_session_id = websocket.query_params.get("session_id")
        if expected_session_id is not None and expected_session_id != session_id:
            await websocket.close(code=4409, reason="session identity changed")
            return
        view_only = inspected.get("execution_kind") == "integration-plan"
        if not running:
            await websocket.close(code=4404, reason="session is not running")
            return
        client_key = f"{tmux.scope}:{session_id}"
        if pty_clients[client_key] >= MAX_PTY_CLIENTS:
            await websocket.close(code=4429, reason="PTY client limit reached")
            return

        await websocket.accept()
        # Accept yields control: another connection may have reserved the final
        # slot while this one was handshaking. Reserve without another await.
        if pty_clients[client_key] >= MAX_PTY_CLIENTS:
            await websocket.close(code=4429, reason="PTY client limit reached")
            return
        pty_clients[client_key] += 1
        master_fd = slave_fd = None
        process = None
        transport = None
        try:
            # Pin a tmux runtime identity while rename holds the same writer
            # lock. The attach subprocess then remains safe after lock release,
            # even if a rename immediately makes this URL's old name reusable.
            with session_manager.database.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT tmux_name, socket_scope, execution_kind FROM sessions WHERE id=?",
                    (session_id,),
                ).fetchone()
                if current is None or current["socket_scope"] != tmux.scope:
                    raise RuntimeError("connected session identity is unavailable")
                current_name = validate_session_name(current["tmux_name"])
                runtime_id = tmux.run(
                    "display-message", "-p", "-t", tmux.pane_target(current_name),
                    "#{session_id}", timeout=2,
                ).stdout.strip()
                if not re.fullmatch(r"\$[0-9]+", runtime_id):
                    raise RuntimeError("invalid tmux session identity")
                view_only = current["execution_kind"] == "integration-plan"
                master_fd, slave_fd = pty.openpty()
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                process = subprocess.Popen(
                    tmux.command("attach-session", "-t", runtime_id),
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                    start_new_session=True,
                    env={**os.environ, "TERM": "xterm-256color"},
                )
            os.close(slave_fd)
            slave_fd = None
            transport = PtyTransport(master_fd)
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            if process is not None and process.poll() is None:
                process.terminate()
            for fd in (slave_fd, master_fd):
                if fd is not None:
                    with suppress(OSError):
                        os.close(fd)
            pty_clients[client_key] = max(0, pty_clients[client_key] - 1)
            log.warning("ws session=%s attach error=%s", name, exc)
            await websocket.close(code=4001, reason="session unavailable")
            return
        async def read_pty() -> None:
            try:
                while True:
                    data = await transport.read()
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except (OSError, RuntimeError, WebSocketDisconnect) as pty_exc:
                log.warning("ws session=%s pty error=%s", name, pty_exc, exc_info=True)
                return
            finally:
                # Any reader exit ends this attachment; do not leave input
                # connected to a terminal whose output pump has failed.
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close(code=4001, reason="session ended")

        def scroll_connected_session(lines: int) -> None:
            # Rename holds the same database writer lock while changing tmux.
            # Resolve the identity inside that lock so an old name can never
            # redirect this open socket's history control to a replacement.
            with session_manager.database.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT tmux_name, socket_scope FROM sessions WHERE id=?",
                    (session_id,),
                ).fetchone()
                if current is None or current["socket_scope"] != tmux.scope:
                    raise RuntimeError("connected session identity is unavailable")
                tmux.scroll_history(validate_session_name(current["tmux_name"]), lines)

        reader = asyncio.create_task(read_pty())
        try:
            session_manager.database.audit(
                "session.attached",
                name,
                "success",
                actor=actor,
                surface="web",
            )
            log.info("ws session=%s actor=%s surface=web attached", name, actor)

            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    if view_only:
                        await websocket.close(code=4403, reason="integration session is view-only")
                        break
                    await transport.write(message["bytes"])
                elif message.get("text"):
                    control = json.loads(message["text"])
                    if control.get("type") == "resize":
                        cols = max(20, min(int(control.get("cols", 80)), 500))
                        rows = max(5, min(int(control.get("rows", 24)), 200))
                        transport.resize(rows, cols, process.pid)
                    elif control.get("type") == "scroll":
                        lines = control.get("lines")
                        if type(lines) is int and -50 <= lines <= 50:
                            await asyncio.to_thread(scroll_connected_session, lines)
                    elif control.get("type") == "detach":
                        await websocket.close(code=4000, reason="detached by user")
                        break
        except (json.JSONDecodeError, OSError, RuntimeError, sqlite3.Error, ValueError, WebSocketDisconnect) as ws_exc:
            log.warning("ws session=%s error=%s", name, ws_exc, exc_info=True)
            with suppress(RuntimeError):
                await websocket.close(code=4001, reason="session unavailable")
        finally:
            reader.cancel()
            # Release local resources before awaiting process exit. A closing
            # websocket may cancel this task while that await is outstanding.
            transport.close()
            pty_clients[client_key] = max(0, pty_clients[client_key] - 1)
            if process.poll() is None:
                process.terminate()
            with suppress(asyncio.CancelledError):
                await reader
            try:
                await asyncio.to_thread(process.wait, 3)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 3)

    return app


app = create_app()
