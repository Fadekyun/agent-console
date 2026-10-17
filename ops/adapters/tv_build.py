#!/usr/bin/env python3
"""Fixed TV build adapter; signing and application activation remain central.

Input source tar contains a Git bundle named repository.bundle. The controller
creates it from the exact reviewed verified-main commit, never an open PR. This
preserves the existing packager's clean-checkout/source/ABI verification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess


def run(args, cwd=None, timeout=120):
    result = subprocess.run(args, cwd=cwd, check=False, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout)
    if result.returncode:
        raise ValueError('TV build command failed: ' + Path(args[0]).name)
    return result.stdout.strip()


def verify(manifest):
    if (manifest.get('version') != 1 or manifest.get('kind') != 'tv.build'
            or manifest.get('production_writes') is not False
            or not re.fullmatch('[0-9a-f]{40}', manifest.get('source_sha', ''))
            or manifest.get('parameters') != {}):
        raise ValueError('Invalid immutable TV build manifest')
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('TV build requires Linux AMD64')
    if platform.libc_ver() != ('glibc', '2.36'):
        raise ValueError('TV build requires the Debian12 toolchain')
    if run(['node', '--version']) != 'v22.23.1' or run(['pnpm', '--version']) != '11.16.0':
        raise ValueError('TV build requires pinned Node/pnpm')


def main():
    parser = argparse.ArgumentParser()
    for name in ('manifest', 'workspace', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    verify(manifest)
    root = Path(args.workspace).resolve()
    bundle = root / 'source' / 'repository.bundle'
    if not bundle.is_file() or bundle.is_symlink():
        raise ValueError('Missing fixed TV source bundle')
    checkout = root / 'tv-source'
    if checkout.exists():
        raise ValueError('TV checkout already exists; reconcile prior attempt')
    run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet', '--no-checkout', str(bundle), str(checkout)])
    run(['git', '-c', 'core.hooksPath=/dev/null', 'checkout', '--quiet', '--detach', manifest['source_sha']], cwd=checkout)
    if run(['git', 'rev-parse', 'HEAD'], cwd=checkout) != manifest['source_sha']:
        raise ValueError('TV source binding mismatch')
    output = Path(args.output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    package = output / 'tv-release.tar.gz'
    # Existing approved release-package.py invokes its pinned fresh build helper.
    # No signing key, runtime env, database or desktop context reaches this job.
    run(['python3', str(checkout / 'scripts/release-package.py'), 'create',
         '--source', str(checkout), '--source-sha', manifest['source_sha'],
         '--output', str(package), '--target-node', 'v22.23.1'], timeout=3300)
    with package.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    result = {'version': 1, 'kind': 'tv.build', 'complete': True,
              'source_sha': manifest['source_sha'], 'worker_binding': manifest.get('worker_binding'),
              'target': {'os': 'linux', 'arch': 'amd64', 'glibc': '2.36',
                         'node': 'v22.23.1', 'pnpm': '11.16.0'},
              'files': [{'path': package.name, 'sha256': checksum, 'size': package.stat().st_size}]}
    temporary = output / '.artifact-manifest.tmp'
    with temporary.open('x') as stream:
        json.dump(result, stream, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output / 'artifact-manifest.json')
    print(json.dumps({'complete': True, 'source_sha': manifest['source_sha'], 'sha256': checksum}))


if __name__ == '__main__':
    main()
