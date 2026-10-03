#!/usr/bin/env python3
"""Offline native skill discovery probes using disposable homes and no secrets.

Pass real binaries/package roots rather than wrappers that source host secrets.
No model request is made. Other harnesses are covered by launch adapter tests;
a passed probe does not assert suppression of native project/plugin sources.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opencode', type=Path)
    parser.add_argument('--hermes-root', type=Path)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix='console-skill-probe-') as temporary:
        root = Path(temporary)
        selected = root/'selected'
        fixture = selected/'console-delivery-probe'
        fixture.mkdir(parents=True)
        (fixture/'SKILL.md').write_text('---\nname: console-delivery-probe\ndescription: Local fixture discovery probe.\n---\nFixture.\n')
        env = {'PATH':os.defpath, 'HOME':temporary, 'HERMES_HOME':temporary,
               **{'XDG_'+kind+'_HOME':str(root/kind.lower()) for kind in ('CONFIG','DATA','CACHE','STATE')}}
        if args.opencode:
            binary = str(args.opencode.resolve(strict=True))
            config = {**env,'OPENCODE_CONFIG_CONTENT':json.dumps({'skills':[str(selected)]}),
                      'OPENCODE_DISABLE_PROJECT_CONFIG':'true'}
            result = subprocess.run([binary,'debug','skill'],cwd=root,env=config,
                                    capture_output=True,text=True,timeout=45)
            try:
                found = any(item['name']=='console-delivery-probe' and str(item['location']).startswith(str(selected))
                            for item in json.loads(result.stdout))
            except (ValueError,KeyError,TypeError):
                found = False
            results.append({'tool':'opencode','passed':result.returncode==0 and found})
        if args.hermes_root:
            package = args.hermes_root.resolve(strict=True)
            (root/'config.yaml').write_text(json.dumps({'skills':{'external_dirs':[str(selected)]}}))
            code = ('import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
                    'from agent.skill_utils import get_external_skills_dirs; '
                    'print(get_external_skills_dirs()==[Path(sys.argv[2])])')
            result = subprocess.run([str(package/'venv/bin/python'),'-c',code,str(package),str(selected)],
                                    cwd=root,env=env,capture_output=True,text=True,timeout=30)
            results.append({'tool':'hermes','passed':result.returncode==0 and result.stdout.strip()=='True'})
    print(json.dumps({'probes':results,'scope':'Selected snapshot discovery only; no model/authentication test.'},indent=2))
    return 0 if results and all(result['passed'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
