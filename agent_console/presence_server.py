"""Explicit web master entrypoint for opt-in shared presence authority.

Default existing uvicorn startup is unchanged. Enabling presence requires a
reviewed service launch through this module; a bare web-worker import never
starts the authority. No service or credentials are installed by this module.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import secrets


def main():
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--workers', type=int, default=1, choices=range(1, 9))
    args = parser.parse_args()
    if os.environ.get('AGCONSOLE_DEVICE_PRESENCE') != '1':
        raise SystemExit('presence startup disabled')
    directory = os.environ.get('AGCONSOLE_DEVICE_PRESENCE_DIR')
    if not directory:
        raise SystemExit('presence configuration unavailable')
    from .presence_service import serve
    context = multiprocessing.get_context('spawn')
    read, write = context.Pipe(duplex=False)
    generation = secrets.token_hex(32)
    child = context.Process(target=serve, args=(directory, generation, write), daemon=True)
    child.start()
    write.close()
    try:
        if not read.poll(3) or read.recv() is not True:
            raise SystemExit('presence authority unavailable')
        os.environ['AGCONSOLE_PRESENCE_GENERATION'] = generation
        uvicorn.run('agent_console.web:app', host=args.host, port=args.port,
                    workers=args.workers, proxy_headers=False)
    finally:
        read.close()
        if child.is_alive():
            child.terminate()
        child.join(2)
        if child.is_alive():
            child.kill()
            child.join(1)
        os.environ.pop('AGCONSOLE_PRESENCE_GENERATION', None)


if __name__ == '__main__':
    main()
