"""Authenticated, hash-bound skill controls shared by the workbench."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .skill_registry import SkillRegistry, read_deliveries
from .skills import _resolve_canonical_root, resolve_session_skills, _discover_skills
from .validation import validate_profile, validate_tool, contained_path, validate_session_name
from .providers import provider_adapter


class PreviewRequest(BaseModel):
    profile: str
    tool: str
    repository: str | None = None


class ImportRequest(BaseModel):
    source: str = Field(min_length=1, max_length=2000)
    revision: str = Field(default='HEAD', min_length=1, max_length=200)
    subdirectory: str = Field(default='', max_length=500)


class DecisionRequest(BaseModel):
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['reviewed', 'blocked'] = 'reviewed'
    services_verified: bool = False


class ApprovalRequest(BaseModel):
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    profile: str


def skill_routes(manager, require_identity):
    router = APIRouter(dependencies=[Depends(require_identity)])

    def registry():
        return SkillRegistry(_resolve_canonical_root(), manager.database.path.parent)

    def audit(action, target, auth, result):
        manager.database.audit('skill.' + action, target, 'success', actor=auth.actor,
                               surface=auth.access_surface,
                               details={'hash': result.get('hash')})
        return result

    @router.get('/api/skill-registry')
    def catalog():
        reg = registry()
        return {'entries': [reg.inspect(e['name']) for e in _discover_skills(reg.root)],
                'imports': reg.imports()}

    @router.post('/api/skill-registry/preview')
    def preview(payload: PreviewRequest):
        validate_profile(payload.profile); validate_tool(payload.tool)
        repository = str(contained_path(Path(payload.repository), manager.settings.workspace_root)) if payload.repository else str(manager.settings.workspace_root)
        reg = registry()
        selected = resolve_session_skills(manager.database, payload.profile, payload.tool,
                                         shared_allowlist=manager.settings.shared_skills,
                                         repository=repository)
        names = {s['name'] for s in selected['materialized']}
        # Include denied assignments, which are deliberately absent from materialized.
        from .skills import get_profile_assignments
        names.update(a['skill_name'] for a in get_profile_assignments(manager.database, payload.profile))
        policies = [reg.explain(name, payload.profile, payload.tool, repository) for name in sorted(names)]
        isolating = provider_adapter(payload.tool, manager.auth).can_isolate_skills
        if selected['validation']['effective'] and not isolating:
            selected['validation']['valid'] = False
            selected['validation']['issues'].append('This tool cannot isolate profile assignments; select an isolating tool or remove assignments.')
        from .skill_capabilities import SKILL_TOOL_CAPABILITIES
        capability = SKILL_TOOL_CAPABILITIES.get(payload.tool)
        with manager.database.connect() as db:
            affected = [dict(row) for row in db.execute(
                "SELECT id,tmux_name,tool,profile FROM sessions WHERE profile=? AND managed=1 "
                "AND status IN ('attached','detached')", (payload.profile,))]
        return {**selected, 'policies': policies, 'isolation': isolating,
                'delivery_capability': capability.as_dict() if capability else None,
                'affected_sessions': affected, 'restart_required': bool(affected),
                'notice': 'Console snapshots cover selected library skills. Tool-bundled, plugin and repository skills may also be discovered by the harness.'}

    @router.get('/api/skill-registry/imports/{identifier}')
    def inspect_import(identifier: str):
        return registry().inspect_import(identifier)

    @router.post('/api/skill-registry/imports')
    def stage(payload: ImportRequest, auth=Depends(require_identity)):
        if '://' in payload.source:
            result = registry().stage_git(payload.source, revision=payload.revision, subdirectory=payload.subdirectory)
        else:
            if payload.revision != 'HEAD' or payload.subdirectory:
                raise ValueError('revision and subdirectory options require a Git HTTPS source')
            source = contained_path(Path(payload.source), manager.settings.workspace_root)
            result = registry().stage(source)
        return audit('imported', result['id'], auth, result)

    @router.post('/api/skill-registry/imports/{identifier}/activate')
    def activate(identifier: str, payload: DecisionRequest, auth=Depends(require_identity)):
        if payload.decision != 'reviewed':
            raise ValueError('activation requires a reviewed revision')
        result = registry().activate(identifier, expected_hash=payload.expected_hash,
                                     actor=auth.actor, services_verified=payload.services_verified)
        return audit('activated', identifier, auth, result)

    @router.get('/api/skill-registry/{name}')
    def inspect(name: str):
        return registry().inspect(name)

    @router.post('/api/skill-registry/{name}/review')
    def review(name: str, payload: DecisionRequest, auth=Depends(require_identity)):
        result = registry().decide(name, decision=payload.decision, expected_hash=payload.expected_hash,
                                   actor=auth.actor, services_verified=payload.services_verified)
        return audit('reviewed', name, auth, result)

    @router.post('/api/skill-registry/{name}/approve')
    def approve(name: str, payload: ApprovalRequest, auth=Depends(require_identity)):
        result = registry().approve(name, payload.profile, expected_hash=payload.expected_hash, actor=auth.actor)
        return audit('approved', payload.profile + '/' + name, auth, result)

    @router.post('/api/skill-registry/{name}/revoke')
    def revoke(name: str, payload: ApprovalRequest, auth=Depends(require_identity)):
        validate_profile(payload.profile)
        registry().revoke(name, payload.profile)
        return audit('revoked', payload.profile + '/' + name, auth, {'revoked': True})

    @router.get('/api/sessions/{name}/skills')
    def delivered(name: str, session_id: str | None = None):
        session = manager.inspect(validate_session_name(name))
        if session_id is not None and session['id'] != session_id:
            raise HTTPException(status_code=409, detail='session identity changed')
        return read_deliveries(manager.settings.state_dir, session['id'])

    return router
