#!/bin/bash
# Add disposable Python test environment support only to the held worker root.
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /etc/agc-worker/hold
root=/var/lib/agc-worker/toolchains/debian12
python3 - "$root" <<'PY'
import json,pathlib,socket,sys
p=pathlib.Path(sys.argv[1])
for node in (p,*p.parents):
 s=node.lstat();assert not node.is_symlink() and s.st_uid==0 and not s.st_mode&0o022
assert json.loads((p/'.agc-worker-toolchain.json').read_text())['version']==1
with socket.socket(socket.AF_UNIX) as client:
 client.settimeout(10);client.connect('/run/agc-worker/rpc.sock')
 client.sendall(b'{"protocol_version":1,"action":"status"}\n')
 with client.makefile('rb') as stream:s=json.loads(stream.readline(524288))
 assert s.get('ok') and s.get('active') is None
PY
export DEBIAN_FRONTEND=noninteractive
chroot "$root" /usr/bin/apt-get install -y --no-install-recommends python3-venv
chroot "$root" /bin/bash -s <<'CHECK'
set -euo pipefail
probe=$(mktemp -d /tmp/agc-test-python.XXXXXXXX)
trap 'rm -rf -- "$probe"' EXIT
python3 -m venv "$probe/venv"
"$probe/venv/bin/python3" -m pip --version
test "$(node --version)" = v22.23.1
test "$(pnpm --version)" = 11.16.0
CHECK
printf '%s\n' 'TV test runtime installed; worker remains held.'
