"""Owner-authenticated write-only environment management."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .environment_service import describe, mutate


def environment_routes(manager, require_identity, static_root):
    router = APIRouter(dependencies=[Depends(require_identity)])

    @router.get('/environment')
    def page():
        return FileResponse(Path(static_root) / 'environment.html')

    @router.get('/api/environment')
    def get(project_id: str | None = None):
        return describe(manager, project_id)

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
            mutate(manager, project_id, lambda: manager.environment.put(name, project_id=project_id, **payload))
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(400, "Invalid environment request: check name, value, state and reserved variable rules") from None
        manager.database.audit('environment.updated', name, 'success', actor=auth.actor,
                               surface=auth.access_surface, details={'scope': manager.environment.scope(project_id)})
        return describe(manager, project_id)

    @router.delete('/api/environment/{name}')
    def delete(name: str, project_id: str | None = None, auth=Depends(require_identity)):
        mutate(manager, project_id, lambda: manager.environment.delete(name, project_id=project_id))
        manager.database.audit('environment.deleted', name, 'success', actor=auth.actor,
                               surface=auth.access_surface, details={'scope': manager.environment.scope(project_id)})
        return describe(manager, project_id)

    return router
