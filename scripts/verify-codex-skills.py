#!/usr/bin/env python3
"""Credential-free installed Codex skill discovery through native app-server."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--guides', type=Path, help='Optional validated Console guide packages to discover')
    args = parser.parse_args()
    binary = str(args.binary.resolve())
    version = subprocess.check_output([binary, '--version'], text=True).strip()
    assert version == 'codex-cli 0.159.2', version
    with tempfile.TemporaryDirectory(prefix='console-codex-skills-') as temporary:
        root = Path(temporary)
        home, project, native, snapshot = [root / part for part in ('home', 'project', 'native', 'snapshot')]
        for directory in (home, project, native, snapshot):
            directory.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        def skill(path, name):
            path.mkdir(parents=True)
            (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Native discovery fixture.\n---\nFixture only.\n')
        skill(snapshot / 'console-snapshot', 'console-snapshot')
        guides = []
        if args.guides:
            from agent_console.skill_registry import snapshot_skill
            for source in sorted(args.guides.glob('*/SKILL.md')):
                receipt = snapshot_skill(source.parent, snapshot / source.parent.name)
                guides.append(receipt['name'])
        (native / 'skills').symlink_to(snapshot, target_is_directory=True)
        skill(home / '.agents/skills/console-user', 'console-user')
        skill(project / '.agents/skills/console-project', 'console-project')
        skill(project / '.codex/skills/console-legacy-project', 'console-legacy-project')
        env = {'HOME': str(home), 'CODEX_HOME': str(native), 'PATH': os.defpath,
               'RUST_LOG': 'off'}
        process = subprocess.Popen([binary, 'app-server', '--stdio'], env=env, cwd=project,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, bufsize=0)
        buffer = b''
        def request(identifier, method, params):
            nonlocal buffer
            process.stdin.write((json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n').encode())
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    message = json.loads(line)
                    if message.get('id') == identifier:
                        assert 'error' not in message, message
                        return message['result']
                if select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                    chunk = os.read(process.stdout.fileno(), 1000000)
                    if not chunk:
                        raise AssertionError('Native app-server exited before response')
                    buffer += chunk
            raise AssertionError('Native discovery timed out')
        try:
            request(1, 'initialize', {'clientInfo': {'name': 'console_skill_probe', 'version': '1'},
                                      'capabilities': {'experimentalApi': True}})
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            result = request(2, 'skills/list', {'cwds': [str(project)], 'forceReload': True})
            entry = result['data'][0]
            names = sorted(skill['name'] for skill in entry['skills'] if skill['name'].startswith('console-') or skill['name'] in guides)
            expected = ['console-legacy-project', 'console-project', 'console-snapshot', 'console-user']
            assert names == sorted(expected + guides), {'discovered': names, 'errors': entry['errors']}
            assert not entry['errors'], entry['errors']
            print(json.dumps({'harness': 'codex/codex-pro', 'version': '0.159.2',
                              'native_skills': names, 'model_called': False,
                              'project_and_user_sources_remain_active': True}))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == '__main__':
    main()
