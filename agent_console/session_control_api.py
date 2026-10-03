from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from .session_control import SessionControl
from .workflow_service import authenticate_session

class ControlRequest(BaseModel):
    command: str = Field(min_length=1, max_length=32)
    payload: dict = Field(default_factory=dict)


def session_control_routes(manager):
    router = APIRouter()

    def identity(authorization: str | None=Header(default=None),
                 x_agent_console_session: str | None=Header(default=None)):
        token = authorization[7:] if authorization and authorization.startswith('Bearer ') else None
        try:
            return authenticate_session(manager, x_agent_console_session, token)
        except PermissionError:
            raise HTTPException(403, 'valid session reporting capability required') from None

    @router.post('/api/agent-sessions')
    def control(body: ControlRequest, caller=Depends(identity)):
        try:
            return SessionControl(manager, caller).run(body.command, body.payload)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except KeyError:
            raise HTTPException(404, 'session or required field not found') from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
    return router
