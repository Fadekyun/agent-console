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
           if not key.startswith(('AGENT_CONSOLE_', 'AGCONSOLE_'))}
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
