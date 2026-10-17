"""Operator-only compute queue controls; execution stays with the local dispatcher."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .request_identity import IdentityDenied, authorize_browser_origin


class ComputeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class JobRequest(ComputeRequest):
    kind: Literal['maintenance.report', 'yuyutei.collect', 'yuyutei.parse', 'agc.build', 'agc.test', 'tv.build', 'points.weekly', 'points.player_events', 'bushi.run', 'bushi.emergency']
    execution_target: Literal['n100', 'agc-laptop']
    source_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    input_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_key: str = Field(min_length=1, max_length=100)
    manifest: dict | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=0, ge=0, le=100)
    not_before: float | None = Field(default=None, ge=0)
    deadline: float | None = Field(default=None, ge=0)


class HoldRequest(ComputeRequest):
    held: bool


class ReconcileRequest(ComputeRequest):
    evidence: str = Field(min_length=1, max_length=4000)


class ExternalOutcomeReconcile(ComputeRequest):
    generation: int = Field(ge=1)
    outcome: Literal['applied', 'not_applied']
    evidence: str = Field(min_length=1, max_length=4000)


class PublicationReconcile(ComputeRequest):
    outcome: Literal['completed', 'abandoned']
    evidence: str = Field(min_length=1, max_length=4000)


class ComputeRoute(APIRoute):
    """Do not echo commands, credentials or other rejected request content."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(400, 'Invalid compute request') from None
            except (ValueError, LookupError):
                raise HTTPException(400, 'Compute request could not be applied') from None
            except PermissionError:
                raise HTTPException(403, 'Compute operation is not permitted') from None
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        return protected


def compute_routes(engine_callable, require_identity, *, permitted_origins=frozenset()):
    """Require the application's trusted-proxy owner identity and origin policy.

    Managed agent reporting capabilities deliberately grant no compute controls.
    Headerless LAN access is insufficient; local owners can use the owner CLI.
    No endpoint accepts telemetry, commands, credentials or execution requests.
    """

    def operator(request: Request, auth=Depends(require_identity)):
        if auth.access_surface != 'tailscale':
            raise HTTPException(403, 'Compute controls require authenticated owner identity')
        if any(name in request.headers for name in (
            'x-agent-console-session', 'x-agent-console-capability', 'authorization',
        )):
            raise HTTPException(403, 'Compute controls require operator identity')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            try:
                authorize_browser_origin(request, permitted_origins)
            except IdentityDenied:
                raise HTTPException(403, 'Compute browser origin is not allowed') from None
        return auth

    router = APIRouter(route_class=ComputeRoute, dependencies=[Depends(operator)])

    @router.get('/compute', include_in_schema=False)
    def page():
        return FileResponse(Path(__file__).resolve().parent.parent / 'web' / 'static' / 'compute.html')

    @router.get('/api/compute')
    def status():
        return engine_callable().status()

    @router.get('/api/compute/jobs/{job_id}')
    def inspect(job_id: str):
        return engine_callable().queue.inspect(job_id)

    @router.post('/api/compute/jobs')
    def enqueue(payload: JobRequest, auth=Depends(operator)):
        return engine_callable().queue.enqueue(**payload.model_dump(exclude_unset=True), actor=auth.actor)

    @router.post('/api/compute/hold')
    def hold(payload: HoldRequest, auth=Depends(operator)):
        return engine_callable().queue.set_hold(held=payload.held, actor=auth.actor)

    @router.post('/api/compute/jobs/{job_id}/retry')
    def retry(job_id: str, payload: ComputeRequest, auth=Depends(operator)):
        return engine_callable().queue.retry(job_id, actor=auth.actor)

    @router.post('/api/compute/jobs/{job_id}/cancel')
    def cancel(job_id: str, payload: ComputeRequest, auth=Depends(operator)):
        return engine_callable().queue.cancel(job_id, actor=auth.actor)

    @router.post('/api/compute/attempts/{attempt_id}/reconcile')
    def reconcile(attempt_id: str, payload: ReconcileRequest, auth=Depends(operator)):
        return engine_callable().queue.reconcile_terminated(
            attempt_id, evidence=payload.evidence, actor=auth.actor,
        )

    @router.post('/api/compute/attempts/{attempt_id}/reconcile-external')
    def reconcile_external(attempt_id: str, payload: ExternalOutcomeReconcile, auth=Depends(operator)):
        return engine_callable().queue.reconcile_external(attempt_id, **payload.model_dump(), actor=auth.actor)

    @router.get('/api/compute/publications')
    def publications():
        from .compute_publication import ComputePublications
        return ComputePublications(engine_callable().queue).status()

    @router.post('/api/compute/publications/{receipt_id}/reconcile')
    def reconcile_publication(receipt_id: str, payload: PublicationReconcile, auth=Depends(operator)):
        from .compute_publication import ComputePublications
        return ComputePublications(engine_callable().queue).reconcile(
            receipt_id, **payload.model_dump(), actor=auth.actor)

    return router
