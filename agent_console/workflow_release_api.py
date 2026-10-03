from typing import Literal
from fastapi import APIRouter,Depends
from pydantic import BaseModel,ConfigDict,Field
from .workflow_release import ReleaseService

class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_result_id:str=Field(min_length=1,max_length=100)
    evidence_result_ids:list[str]=Field(min_length=1,max_length=16)
    action:Literal['push','merge','deploy','release']
    target:str=Field(min_length=1,max_length=80)

class Authorize(Preview):
    expected_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    request_key:str=Field(min_length=1,max_length=100)

class Start(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['apply','probe']
    request_key:str=Field(min_length=1,max_length=100)


def release_routes(manager,require_identity):
    router=APIRouter(dependencies=[Depends(require_identity)])
    def service():return ReleaseService(manager)
    @router.get('/api/workflow/release-targets')
    def targets():return service().catalog()
    @router.get('/api/workflow/releases/evidence/{candidate_id}')
    def evidence(candidate_id:str):return service().evidence_options(candidate_id)
    @router.post('/api/workflow/releases/preview')
    def preview(payload:Preview):return service().preview(**payload.model_dump())
    @router.post('/api/workflow/releases/authorize')
    def authorize(payload:Authorize,auth=Depends(require_identity)):
        return service().authorize(**payload.model_dump(),actor=auth.actor)
    @router.get('/api/sessions/{identity}/releases')
    def releases(identity:str):return service().list(identity)
    @router.get('/api/workflow/releases/{identity}')
    def inspect(identity:str):return service().inspect(identity)
    @router.post('/api/workflow/releases/{identity}/attempts')
    def start(identity:str,payload:Start,auth=Depends(require_identity)):
        return service().start(identity,**payload.model_dump(),actor=auth.actor)
    return router
