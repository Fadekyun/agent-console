#!/usr/bin/env python3
"""Verify installed Pi selected-snapshot discovery in disposable, credential-free roots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.providers import PiAdapter
from agent_console.skill_registry import snapshot_skill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi-root', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--expected-version', required=True)
    args = parser.parse_args()
    package = args.pi_root.resolve()
    version = json.loads((package / 'package.json').read_text())['version']
    if version != args.expected_version:
        raise SystemExit(f'Installed Pi {version} does not match requested verification {args.expected_version}')
    with tempfile.TemporaryDirectory(prefix='console-native-pi-') as temporary:
        root = Path(temporary)
        library, snapshot, project, agent = [root / name for name in ('library', 'snapshot', 'project', 'agent')]
        for directory in (library, snapshot, project, agent): directory.mkdir()
        for name in ('selected-fixture', 'unselected-fixture'):
            skill = library / name; skill.mkdir()
            (skill / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Native discovery verification fixture.\n---\nRead [support](support.txt).\n')
            (skill / 'support.txt').write_text('fixture-support-only\n')
        snapshot_skill(library / 'selected-fixture', snapshot / 'selected-fixture')
        PiAdapter(None).configure_shared_skills(environment={'PI_CODING_AGENT_DIR': str(agent)}, isolated_skills_root=snapshot)
        probe = root / 'probe.mjs'
        modules = {name: str(package / 'dist/core' / filename) for name, filename in (
            ('skills', 'skills.js'), ('resources', 'resource-loader.js'), ('settings', 'settings-manager.js'))}
        probe.write_text('''import {loadSkills} from SKILLS;
import {DefaultResourceLoader} from RESOURCES;
import {SettingsManager} from SETTINGS;
import fs from 'node:fs';
import path from 'node:path';
const options = OPTIONS;
const low = loadSkills({...options, skillPaths: [], includeDefaults: true});
const loader = new DefaultResourceLoader({...options, settingsManager: SettingsManager.inMemory({packages:[]}), noExtensions:true, noPromptTemplates:true, noThemes:true});
await loader.reload();
function describe(result) {
  return {names:result.skills.map(s=>s.name).sort(), diagnostics:result.diagnostics,
    paths:result.skills.map(s=>fs.realpathSync(s.filePath)),
    support:result.skills.map(s=>fs.readFileSync(path.join(path.dirname(s.filePath),'support.txt'),'utf8'))};
}
console.log(JSON.stringify({low:describe(low), full:describe(loader.getSkills())}));
'''.replace('SKILLS', json.dumps(modules['skills'])).replace('RESOURCES', json.dumps(modules['resources'])).replace('SETTINGS', json.dumps(modules['settings'])).replace('OPTIONS', json.dumps({'cwd': str(project), 'agentDir': str(agent)})))
        # Only this short-lived process receives a fixture home. No host credentials.
        env = {'HOME': str(root), 'PATH': os.defpath, 'PI_CODING_AGENT_DIR': str(agent)}
        result = subprocess.run([str(args.node.resolve()), str(probe)], env=env, cwd=project,
                                capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise RuntimeError(f'Native Pi probe failed: {result.stderr.strip()}')
        native = json.loads(result.stdout)
        for name, check in native.items():
            if (check['names'] != ['selected-fixture'] or check['diagnostics'] or
                    check['paths'] != [str(snapshot / 'selected-fixture/SKILL.md')] or
                    check['support'] != ['fixture-support-only\n']):
                raise RuntimeError(f'{name} native discovery mismatch: {check}')
        print(json.dumps({'harness': 'pi', 'version': version, 'checks': ['native-skills-loader', 'default-resource-loader', 'selected-snapshot-path', 'relative-support-file', 'unselected-library-absent'],
                          'loader_sha256': hashlib.sha256(Path(modules['resources']).read_bytes()).hexdigest(),
                          'model_called': False, 'credentials_loaded': False, 'pass': True}))


if __name__ == '__main__':
    main()
