#!/usr/bin/env python3
"""Native Claude initialization only; no credentials, model turn or installation."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.manager import SessionManager
from agent_console.providers import ClaudeAdapter, TOOL_BINARIES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--guides', type=Path, help='Optional validated Console guide packages to discover')
    args = parser.parse_args()
    binary = args.binary.resolve()
    assert subprocess.check_output([str(binary), '--version'], text=True).strip() == '2.1.287 (Claude Code)'
    with tempfile.TemporaryDirectory(prefix='console-claude-skills-') as temporary:
        root = Path(temporary)
        home, project, snapshot, state = [root / name for name in ('home', 'project', 'snapshot', 'state')]
        for directory in (home, project, snapshot, state / 'launchers'):
            directory.mkdir(parents=True)
        def skill(path, name):
            path.mkdir(parents=True)
            (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Native discovery fixture.\n---\nNo actions.\n')
        skill(snapshot / 'console-snapshot', 'console-snapshot')
        guides = []
        if args.guides:
            from agent_console.skill_registry import snapshot_skill
            for source in sorted(args.guides.glob('*/SKILL.md')):
                receipt = snapshot_skill(source.parent, snapshot / source.parent.name)
                guides.append(receipt['name'])
        skill(home / '.claude/skills/console-user', 'console-user')
        skill(project / '.claude/skills/console-project', 'console-project')
        # Exercise the actual Console overlay and shell launcher, not a hand-built
        # approximation of the additional-directory argument.
        manager = SessionManager.__new__(SessionManager)
        manager.settings = SimpleNamespace(state_dir=state, service_bind='127.0.0.1', service_port=1)
        overlay = manager._create_session_tool_overlay('fixture', 'claude', {}, snapshot)
        with patch.dict(TOOL_BINARIES, {'claude': binary}):
            spec = ClaudeAdapter(None).build_launch_spec(context={}, role='Fixture only.',
                                                        read_only=False)
        spec.argv.extend(['-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                      '--verbose', '--no-session-persistence'])
        launcher = manager._write_launcher('fixture', 'fixture-id', spec, overlay_env=overlay)
        env = {'HOME': str(home), 'PATH': os.defpath, 'DISABLE_AUTOUPDATER': '1',
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
               'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1'}
        process = subprocess.Popen([str(launcher)], env=env, cwd=project, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        try:
            process.stdin.write((json.dumps({'type': 'control_request', 'request_id': 'init',
                                            'request': {'subtype': 'initialize'}}) + '\n').encode())
            buffer = b''
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    message = json.loads(line)
                    if message.get('type') != 'control_response':
                        continue
                    response = message['response']
                    assert response['subtype'] == 'success', response['subtype']
                    data = response['response']
                    names = sorted(command['name'] for command in data['commands'] if command['name'].startswith('console-') or command['name'] in guides)
                    assert names == sorted(['console-project', 'console-snapshot', 'console-user'] + guides), names
                    assert data['account']['tokenSource'] == 'none'
                    print(json.dumps({'harness': 'claude', 'version': '2.1.287', 'native_skills': names,
                                      'model_called': False, 'account_token': False,
                                      'project_and_user_sources_remain_active': True}))
                    return
                if select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                    chunk = os.read(process.stdout.fileno(), 1000000)
                    if not chunk:
                        raise AssertionError('Native initialization exited without response')
                    buffer += chunk
            raise AssertionError('Native initialization timed out')
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == '__main__':
    main()
