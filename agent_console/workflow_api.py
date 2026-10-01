from typing import Literal
from fastapi import APIRouter, Depends, Response, Header, HTTPException
from pydantic import BaseModel, Field
from .workflow_service import WorkflowService


class ResultRequest(BaseModel):
    kind: Literal['ready','final']
    outcome: Literal['pass','fail','blocked']
    summary: str = Field(min_length=1, max_length=32000)
    checks: list[str] = Field(default_factory=list, max_length=50)
    artifacts: list[dict] = Field(default_factory=list, max_length=16)
    request_key: str = Field(min_length=1, max_length=100)


class SendRequest(BaseModel):
    target_session_id: str
    request_key: str = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=4000)


class AckRequest(BaseModel):
    state: Literal['delivered','consumed']


class DependencyRequest(BaseModel):
    source_id: str = Field(min_length=1,max_length=100)
    readiness: Literal['after-ready','after-final','alongside']


class DependenciesRequest(BaseModel):
    expected_version: int = Field(ge=0)
    dependencies: list[DependencyRequest] = Field(default_factory=list,max_length=32)


class AttachRequest(DependenciesRequest):
    session_id: str = Field(min_length=1,max_length=100)
    purpose: str = Field(min_length=1,max_length=4000)


class DeliverRequest(BaseModel):
    expected_version: int = Field(ge=1)
    expected_signature: str = Field(pattern=r'^[a-f0-9]{64}$')


def workflow_routes(manager, require_identity):
    router = APIRouter(dependencies=[Depends(require_identity)])

    def service(): return WorkflowService(manager)

    @router.get('/api/sessions/{identity}/results')
    def results(identity: str, before: int=2147483647):
        svc=service()
        return {'results':svc.store.results(svc.session(identity)['id'],before=before)}

    @router.post('/api/sessions/{identity}/results')
    def publish(identity: str, payload: ResultRequest, auth=Depends(require_identity)):
        return service().publish(identity,payload.model_dump(),auth.actor)

    @router.post('/api/results/{result_id}/send')
    def send(result_id: str, payload: SendRequest, auth=Depends(require_identity)):
        return service().send(result_id,payload.target_session_id,request_key=payload.request_key,note=payload.note,actor=auth.actor)

    @router.get('/api/results/{result_id}/artifacts/{index}')
    def artifact(result_id: str, index: int):
        data, artifact=service().store.artifact(result_id,index)
        filename='artifact.tar.gz' if artifact['kind']=='commit' else 'artifact.txt'
        return Response(data,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{filename}"','X-Content-Type-Options':'nosniff'})

    @router.get('/api/sessions/{identity}/inbox')
    def inbox(identity: str, after: int=0):
        svc=service()
        return svc.store.inbox(svc.session(identity)['id'],after=after)

    @router.post('/api/sessions/{identity}/inbox/{item_id}/ack')
    def acknowledge(identity: str,item_id: str,payload: AckRequest,auth=Depends(require_identity)):
        svc=service()
        return svc.store.acknowledge(item_id,svc.session(identity)['id'],state=payload.state,actor=auth.actor)


    @router.get('/api/sessions/{identity}/connections')
    def connections(identity: str):
        svc=service()
        return svc.graph().inspect(svc.session(identity)['id'])

    @router.post('/api/sessions/{identity}/connections/attach')
    def attach(identity: str,payload: AttachRequest,auth=Depends(require_identity)):
        return service().attach(identity,payload.session_id,purpose=payload.purpose,
              dependencies=[d.model_dump() for d in payload.dependencies],expected_version=payload.expected_version,actor=auth.actor)

    @router.post('/api/sessions/{identity}/connections/dependencies')
    def dependencies(identity: str,payload: DependenciesRequest,auth=Depends(require_identity)):
        svc=service()
        return svc.graph().dependencies(svc.session(identity)['id'],dependencies=[d.model_dump() for d in payload.dependencies],
                                       expected_version=payload.expected_version,actor=auth.actor)

    @router.post('/api/sessions/{identity}/connections/deliver')
    def deliver(identity: str,payload: DeliverRequest,auth=Depends(require_identity)):
        svc=service()
        return svc.graph().deliver(svc.session(identity)['id'],expected_version=payload.expected_version,
                                  expected_signature=payload.expected_signature,actor=auth.actor)

    return router


class AgentRequest(BaseModel):
    command: Literal['publish','results','result','send','inbox','ack','connections']
    payload: dict = Field(default_factory=dict)


def agent_workflow_routes(manager):
    router=APIRouter()

    def agent_identity(authorization: str | None=Header(default=None), x_agent_console_session: str | None=Header(default=None)):
        from .workflow_service import authenticate_session
        token=authorization.removeprefix('Bearer ') if authorization and authorization.startswith('Bearer ') else None
        try:return authenticate_session(manager,x_agent_console_session,token)
        except PermissionError:raise HTTPException(status_code=403,detail='valid session reporting capability required') from None

    @router.post('/api/agent-workflow')
    def report(body: AgentRequest, identity=Depends(agent_identity)):
        svc=WorkflowService(manager);payload=body.payload;actor='session:'+identity['id']
        if body.command=='publish':
            validated=ResultRequest.model_validate(payload)
            return svc.publish(identity['id'],validated.model_dump(),actor)
        if body.command=='connections':return svc.graph().inspect(identity['id'])
        if body.command=='inbox':return svc.store.inbox(identity['id'],after=payload.get('after',0))
        if body.command=='results':return {'results':svc.store.results(identity['id'],before=payload.get('before',2147483647))}
        if body.command=='send':
            result_id=payload.get('result_id');data=SendRequest.model_validate(payload)
            return svc.send(result_id,data.target_session_id,request_key=data.request_key,note=data.note,actor=actor,source=identity['id'])
        if body.command=='ack':
            data=AckRequest.model_validate(payload)
            return svc.store.acknowledge(payload.get('item_id'),identity['id'],state=data.state,actor=actor)
        result=svc.store.result(payload.get('result_id'))
        with svc.store.connect() as db:
            received=db.execute('SELECT 1 FROM inbox WHERE target_session_id=? AND result_id=?',(identity['id'],result['id'])).fetchone()
        if result['session_id']!=identity['id'] and not received:
            raise HTTPException(status_code=403,detail='result was not addressed to this session')
        return result

    return router
