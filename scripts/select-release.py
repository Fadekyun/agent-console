#!/usr/bin/env python3
"""Maintenance-only current-state guarded release selection."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.deployer import Deployer


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--releases',type=Path,required=True)
    p.add_argument('--release',type=Path,required=True)
    a=p.parse_args()
    try:
        target=a.release.resolve(strict=True)
        if target.parent != a.releases.resolve(strict=True):
            raise ValueError('release must be an immediate contained child')
        d=Deployer(a.releases,object(),database_path=a.database,config_dir=a.config,state_dir=a.state)
        d.select_release(target.name)
        print('guarded release selected')
        return 0
    except (OSError,ValueError):
        print('release selection refused; current state retained',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
