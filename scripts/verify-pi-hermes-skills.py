#!/usr/bin/env python3
"""Verify selected guide snapshots with installed native readers, without models."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.providers import PiAdapter, HermesAdapter
from agent_console.skill_registry import snapshot_skill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('guides', 'pi-root', 'node', 'hermes-root', 'hermes-python'):
        parser.add_argument('--' + argument, type=Path, required=True)
    args = parser.parse_args()
    assert json.loads((args.pi_root / 'package.json').read_text())['version'] == '0.99.2'
    with tempfile.TemporaryDirectory(prefix='console-native-guides-') as temporary:
        root = Path(temporary)
        snapshot, project, pi, hermes = [root / part for part in ('snapshot', 'project', 'pi', 'hermes')]
        for directory in (snapshot, project, pi, hermes):
            directory.mkdir()
        names = []
        for source in sorted(args.guides.glob('*/SKILL.md')):
            names.append(snapshot_skill(source.parent, snapshot / source.parent.name)['name'])
        assert names, 'No guide packages supplied'
        PiAdapter(None).configure_shared_skills(environment={'PI_CODING_AGENT_DIR': str(pi)}, isolated_skills_root=snapshot)
        HermesAdapter(None).configure_shared_skills(environment={'HERMES_HOME': str(hermes)}, isolated_skills_root=snapshot)
        probe = root / 'pi.mjs'
        probe.write_text('import {DefaultResourceLoader} from ' + json.dumps(str(args.pi_root / 'dist/core/resource-loader.js')) + ';\n' +
                         'const loader = new DefaultResourceLoader(' + json.dumps({'cwd': str(project), 'agentDir': str(pi), 'noExtensions': True, 'noPromptTemplates': True, 'noThemes': True}) + ');\n' +
                         'await loader.reload(); const result = loader.getSkills();\n' +
                         'if(result.diagnostics.length) throw Error("native diagnostics");\n' +
                         'console.log(JSON.stringify(result.skills.map(s=>s.name).sort()));\n')
        env = {'HOME': str(root), 'PATH': os.defpath, 'PI_CODING_AGENT_DIR': str(pi), 'HERMES_HOME': str(hermes)}
        result = subprocess.run([str(args.node), str(probe)], env=env, cwd=project, capture_output=True, text=True, check=True, timeout=20)
        assert json.loads(result.stdout) == sorted(names), result.stdout
        print(json.dumps({'harness': 'pi', 'version': '0.99.2', 'native_guides': names, 'model_called': False}))
        code = '''import sys,json
sys.path.insert(0,sys.argv[1])
from hermes_cli import __version__
from tools.skills_tool import skills_list
assert __version__ == '0.21.4'
result=json.loads(skills_list())
assert result['success']
print(json.dumps(sorted(item['name'] for item in result['skills'])))
'''
        result = subprocess.run([str(args.hermes_python), '-c', code, str(args.hermes_root)], env=env, cwd=project, capture_output=True, text=True, check=True, timeout=20)
        assert json.loads(result.stdout) == sorted(names), result.stdout
        print(json.dumps({'harness': 'hermes', 'version': '0.21.4', 'native_guides': names, 'model_called': False}))


if __name__ == '__main__':
    main()
