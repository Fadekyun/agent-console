"""Fixed routes; scoped writer is rejected everywhere else, including WebSockets."""
from __future__ import annotations

import asyncio
import os

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from .device_presence import GET, POST, HEADER, LIMIT, Rejected
from .presence_service import request as receiver_request


class ScopedWriterBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        values = [v for k, v in scope.get('headers', []) if k.lower() == HEADER.encode()]
        if values and not (scope['type'] == 'http' and scope.get('method') == 'POST'
                           and scope.get('path') == POST and len(values) == 1):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4403})
            else:
                await JSONResponse({'error': 'presence_scope_denied'}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install(app, read_identity, *, directory=None, generation=None):
    # Runtime env/MCP reads and managed-session binding writes share the normal
    # Console identity policy. The presence header remains scoped to presence only.
    from .runtime_routes import install as install_runtime_routes
    install_runtime_routes(app, read_identity)

    # Explicit injected dummy configuration is for isolated application fixtures.
    enabled = os.environ.get('AGCONSOLE_DEVICE_PRESENCE') == '1'
    if directory is None:
        directory = os.environ.get('AGCONSOLE_DEVICE_PRESENCE_DIR') if enabled else None
        generation = os.environ.get('AGCONSOLE_PRESENCE_GENERATION') if enabled else None
    app.add_middleware(ScopedWriterBoundary)

    active = 0

    async def invoke(operation, body=None, token=None):
        nonlocal active
        if not directory or not generation:
            return JSONResponse({'error': 'presence_unavailable'}, status_code=503)
        if active >= 8:
            return JSONResponse({'error': 'presence_unavailable'}, status_code=503)
        active += 1
        job = asyncio.create_task(asyncio.to_thread(receiver_request, directory, generation, operation, body, token))
        def completed(_):
            nonlocal active
            active -= 1
        job.add_done_callback(completed)
        try:
            result = await asyncio.shield(job)
            return JSONResponse(result['body'], status_code=result['status'])
        except Rejected as exc:
            return JSONResponse({'error': 'presence_rejected'}, status_code=exc.status)
        except Exception:
            return JSONResponse({'error': 'presence_unavailable'}, status_code=503)

    @app.post(POST)
    async def write_presence(request: Request):
        tokens = request.headers.getlist(HEADER)
        lengths = request.headers.getlist('content-length')
        if len(tokens) != 1 or not 32 <= len(tokens[0]) <= 256:
            return JSONResponse({'error': 'presence_scope_denied'}, status_code=403)
        if (request.headers.get('transfer-encoding') or len(lengths) != 1
                or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 1024
                or request.headers.get('content-type', '').split(';')[0].strip() != 'application/json'):
            return JSONResponse({'error': 'presence_rejected'}, status_code=400)
        try:
            async def bounded_body():
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 1024:
                        raise Rejected()
                if len(raw) != int(lengths[0]):
                    raise Rejected()
                return bytes(raw).decode('utf-8')
            body = await asyncio.wait_for(bounded_body(), timeout=1)
        except Exception:
            return JSONResponse({'error': 'presence_rejected'}, status_code=400)
        return await invoke('write', body, tokens[0])

    @app.get(GET)
    async def read_presence(_=Depends(read_identity)):
        return await invoke('read')
