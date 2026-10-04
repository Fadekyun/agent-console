"""Operator-only recipe, launch preview and continuation controls."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .workbench_launch import LaunchCatalog, MissingLaunchRequest


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request: dict
    source_session_id: str | None = Field(default=None, max_length=80)


class Run(Preview):
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_key: str = Field(min_length=1, max_length=100)


class Recipe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)
    request: dict
    expected_revision: int | None = Field(default=None, ge=1)


class Remove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)


def launch_routes(manager, require_identity):
    router = APIRouter(dependencies=[Depends(require_identity)])
    catalog = LaunchCatalog(manager)

    @router.get('/api/workbench/sessions/{identity}/configuration')
    def configuration(identity: str):
        return catalog.configuration(identity)

    @router.get('/api/workbench/recipes')
    def recipes():
        return catalog.recipes()

    @router.post('/api/workbench/recipes')
    def save(payload: Recipe, auth=Depends(require_identity)):
        return catalog.save_recipe(payload.title, payload.request, actor=auth.actor)

    @router.post('/api/workbench/recipes/{recipe_id}')
    def update(recipe_id: str, payload: Recipe, auth=Depends(require_identity)):
        return catalog.save_recipe(payload.title, payload.request, actor=auth.actor,
                                   recipe_id=recipe_id, expected_revision=payload.expected_revision)

    @router.post('/api/workbench/recipes/{recipe_id}/remove')
    def remove(recipe_id: str, payload: Remove, auth=Depends(require_identity)):
        return catalog.remove_recipe(recipe_id, payload.expected_revision, actor=auth.actor)

    @router.post('/api/workbench/launches/preview')
    def preview(payload: Preview):
        return catalog.preview(payload.request, source_id=payload.source_session_id)

    @router.post('/api/workbench/launches')
    def run(payload: Run, auth=Depends(require_identity)):
        return catalog.launch(payload.request, expected_hash=payload.expected_hash,
                              request_key=payload.request_key, actor=auth.actor,
                              source_id=payload.source_session_id)

    @router.get('/api/workbench/launches/{request_key}')
    def status(request_key: str):
        try:
            return catalog.launch_status(request_key)
        except MissingLaunchRequest as error:
            raise HTTPException(status_code=404, detail='Launch request not found') from error

    return router
