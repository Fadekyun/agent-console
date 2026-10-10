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


def bootstrap_console_state():
    """Complete first-install writes before spawning any presence/web child.

    Workers retain normal guarded initialization. Preinitializing WAL, schema
    and the default auth registry removes concurrent first-creation transitions;
    this does not suppress migration failures or skip worker schema guards.
    Only this explicitly opted-in master uses this path.
    """
    from .config import Settings
    from .auth import AuthRegistry
    from .database import Database
    settings = Settings.from_env()
    settings.ensure_state_dirs()
    AuthRegistry(settings.config_dir or settings.state_dir / 'config')
    Database(settings.database_path).migrate()


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
    # A bootstrap failure must leave neither a receiver authority nor workers.
    bootstrap_console_state()
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
