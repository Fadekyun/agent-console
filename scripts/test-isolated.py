#!/usr/bin/env python3
"""Run tests without reading or modifying an operator's Console configuration."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='agent-console-tests-') as directory:
    scratch = Path(directory)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('AGENT_CONSOLE_', 'AGCONSOLE_'))
           and key not in {'N8N_MCP_TOKEN', 'DIRECTUS_MCP_TOKEN', 'BUSHI_MCP_TOKEN',
                          'OPENROUTER_API_KEY', 'CMD_API_KEY'}}
    # Exercise actual executable discovery/identity checks without requiring paid
    # harness installations or accidentally starting an operator's real agent.
    # Tests of native task protocols supply their own protocol-aware executables.
    binaries = scratch / 'bin'
    binaries.mkdir()
    for tool in ('codex', 'codex-pro', 'claude', 'opencode', 'pi', 'hermes'):
        launcher = binaries / tool
        launcher.write_text(
            '#!/bin/sh\n'
            'case "$1" in\n'
            '  --version) printf "Console test harness 0.0.0\\n"; exit 0 ;;\n'
            '  exec|models|--help) exit 2 ;;\n'
            'esac\n'
            'exec /bin/bash --noprofile --norc\n', encoding='utf-8')
        launcher.chmod(0o700)
        env['AGCONSOLE_' + tool.upper().replace('-', '_') + '_BIN'] = str(launcher)
    env['AGCONSOLE_SHELL_BIN'] = '/bin/bash'
    env['PATH'] = str(binaries) + os.pathsep + env.get('PATH', os.defpath)
    env.update({
        'AGENT_CONSOLE_WORKSPACE_ROOT': str(scratch / 'workspace'),
        'AGENT_CONSOLE_STATE_DIR': str(scratch / 'state'),
        'AGENT_CONSOLE_CONFIG_DIR': str(scratch / 'config'),
        'AGENT_CONSOLE_DB': str(scratch / 'state' / 'tests.sqlite3'),
        'AGENT_CONSOLE_PROFILE_DIR': str(root / 'agent-profiles'),
        'AGENT_CONSOLE_TMUX_SOCKET_PATH': str(scratch / 'tmux.sock'),
        'AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH': str(scratch / 'unused.sock'),
        'AGCONSOLE_SKILLS_ROOT': str(scratch / 'skills'),
        'AGENT_CONSOLE_HANDOFF_DIR': str(scratch / 'handoffs'),
        'AGENT_CONSOLE_WORKTREE_ROOT': str(scratch / 'worktrees'),
    })
    (scratch / 'workspace').mkdir()
    args = sys.argv[1:] or ['-q', 'tests']
    result = subprocess.run([sys.executable, '-m', 'pytest', *args], cwd=root, env=env)
    raise SystemExit(result.returncode)
