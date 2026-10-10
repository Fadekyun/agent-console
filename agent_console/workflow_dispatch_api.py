from typing import Literal
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from .workflow_api import DependencyRequest
from .workflow_engine import WorkflowEngine


class ProposalRequest(BaseModel):
    task: str=Field(min_length=1,max_length=12000)
    reason: str=Field(min_length=1,max_length=4000)
    expected_output: str=Field(min_length=1,max_length=4000)
    config: dict=Field(default_factory=dict)
    dependencies: list[DependencyRequest]=Field(default_factory=list,max_length=32)
    request_key: str=Field(min_length=1,max_length=100)


class EditRequest(BaseModel):
    task: str=Field(min_length=1,max_length=12000)
    reason: str=Field(min_length=1,max_length=4000)
    expected_output: str=Field(min_length=1,max_length=4000)
    config: dict=Field(default_factory=dict)
    dependencies: list[DependencyRequest]=Field(default_factory=list,max_length=32)
    expected_version: int=Field(ge=1)


class DecisionRequest(BaseModel):
    decision: Literal['accepted','rejected']
    expected_version: int=Field(ge=1)
    preview_hash: str|None=None


class PolicyRequest(BaseModel):
    policy: dict
    expected_version: int=Field(ge=0)


class ControlRequest(BaseModel):
    state: Literal['running','paused','stopped']


class ReconcileRequest(BaseModel):
    outcome: Literal['not-started','pass','fail','blocked']
    summary: str=Field(min_length=1,max_length=4000)


def dispatch_routes(manager,require_identity):
    router=APIRouter(dependencies=[Depends(require_identity)])
    def engine():return WorkflowEngine(manager)

    @router.get('/api/sessions/{identity}/workflow')
    def inspect(identity:str):return engine().inspect(identity)

    @router.post('/api/sessions/{identity}/workflow/proposals')
    def propose(identity:str,payload:ProposalRequest,auth=Depends(require_identity)):
        return engine().propose(identity,**payload.model_dump(),actor=auth.actor)

    @router.post('/api/sessions/{identity}/workflow/policy')
    def policy(identity:str,payload:PolicyRequest,auth=Depends(require_identity)):
        return engine().configure(identity,**payload.model_dump(),actor=auth.actor)

    @router.post('/api/sessions/{identity}/workflow/control')
    def control(identity:str,payload:ControlRequest,auth=Depends(require_identity)):
        return engine().control(identity,state=payload.state,actor=auth.actor)

    @router.post('/api/workflow/steps/{step_id}/preview')
    def preview(step_id:str):
        flow=engine();step=flow.step(step_id)
        return flow.preview(step['root_id'],step['config'])

    @router.post('/api/workflow/steps/{step_id}/review')
    def review(step_id:str,payload:DecisionRequest,auth=Depends(require_identity)):
        return engine().decide(step_id,**payload.model_dump(),actor=auth.actor)

    @router.post('/api/workflow/steps/{step_id}/edit')
    def edit(step_id:str,payload:EditRequest,auth=Depends(require_identity)):
        return engine().edit(step_id,**payload.model_dump(),actor=auth.actor)

    @router.post('/api/workflow/steps/{step_id}/retry')
    def retry(step_id:str,auth=Depends(require_identity)):
        return engine().retry(step_id,actor=auth.actor)

    @router.post('/api/workflow/attempts/{attempt_id}/reconcile')
    def reconcile(attempt_id:str,payload:ReconcileRequest,auth=Depends(require_identity)):
        return engine().reconcile(attempt_id,**payload.model_dump(),actor=auth.actor)

    return router
