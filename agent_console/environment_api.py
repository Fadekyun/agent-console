"""Owner-authenticated write-only environment management."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .environment import read_private


def environment_routes(manager, require_identity, static_root):
    router = APIRouter(dependencies=[Depends(require_identity)])

    def scope(project_id):
        manager.environment.scope(project_id)
        if project_id is not None:
            with manager.database.connect() as conn:
                if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
                    raise KeyError("Project does not exist")
        return project_id

    def describe(project_id):
        result = manager.environment.describe(scope(project_id))
        with manager.database.connect() as conn:
            rows = conn.execute("SELECT id, tmux_name, project_id, status FROM sessions WHERE managed=1 AND execution_kind!='integration-plan'").fetchall()
        sessions = []
        for row in rows:
            if project_id and row["project_id"] != project_id:
                continue
            snapshot = read_private(manager.settings.state_dir / "environment-launches" / f"{row['id']}.json")
            revision = snapshot.get("revision") if snapshot else None
            sessions.append({"id": row["id"], "name": row["tmux_name"], "status": row["status"],
                             "revision": revision, "refresh_required": revision != manager.environment.revision(row["project_id"])})
        result["sessions"] = sessions
        return result

    @router.get('/environment')
    def page():
        return FileResponse(Path(static_root) / 'environment.html')

    @router.get('/api/environment')
    def get(project_id: str | None = None):
        return describe(project_id)

    @router.put('/api/environment/{name}')
    async def put(name: str, request: Request, project_id: str | None = None, auth=Depends(require_identity)):
        # Do not use request-model errors: framework validation includes submitted
        # input by default and would echo rejected secret values to clients.
        body = await request.body()
        if len(body) > 200000:
            raise HTTPException(400, "Environment request is too large")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) - {'value', 'state'}:
                raise ValueError()
            manager.environment.put(name, project_id=scope(project_id), **payload)
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(400, "Invalid environment request: check name, value, state and reserved variable rules") from None
        manager.database.audit('environment.updated', name, 'success', actor=auth.actor,
                               surface=auth.access_surface, details={'scope': manager.environment.scope(project_id)})
        return describe(project_id)

    @router.delete('/api/environment/{name}')
    def delete(name: str, project_id: str | None = None, auth=Depends(require_identity)):
        manager.environment.delete(name, project_id=scope(project_id))
        manager.database.audit('environment.deleted', name, 'success', actor=auth.actor,
                               surface=auth.access_surface, details={'scope': manager.environment.scope(project_id)})
        return describe(project_id)

    return router
