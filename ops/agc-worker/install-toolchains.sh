#!/bin/bash
# Operator-only, run through Setup-Worker.ps1 inside the held independent VM.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -e /etc/agc-worker/hold
pins=/opt/agc-worker-bootstrap/toolchain-pins.json
cat > "$pins" <<'AGC_TOOLCHAIN_PINS'
{
  "images": {
    "12": {
      "reference": "docker.io/library/debian@sha256:a4672c0cb26fbdde88e38fa2dfb6c681942306680e41e4378b28770b6e79ee91",
      "index_digest": "sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587",
      "layers": [
        {
          "digest": "sha256:7fd1ebe418b5079d4aa36f416af9b143d127bc75b0155d71cb5fd559b5e04d39",
          "size": 28249323
        }
      ]
    },
    "13": {
      "reference": "docker.io/library/debian@sha256:918311b7b6c4c6f68b232ba516584925f6c78ad82b6fd534b98979df6438e483",
      "index_digest": "sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f",
      "layers": [
        {
          "digest": "sha256:ecc510c1e359bdc007b39802570bb7e4dec7d6ebccf357c3201a74299113e05e",
          "size": 29843350
        }
      ]
    }
  },
  "node": {
    "22.22.2": {
      "url": "https://nodejs.org/dist/v22.22.2/node-v22.22.2-linux-x64.tar.xz",
      "sha256": "88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a"
    },
    "22.23.1": {
      "url": "https://nodejs.org/dist/v22.23.1/node-v22.23.1-linux-x64.tar.xz",
      "sha256": "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
    }
  },
  "pnpm": {
    "version": "11.16.0",
    "url": "https://registry.npmjs.org/pnpm/-/pnpm-11.16.0.tgz",
    "integrity": "sha512-t2fpqY/IeuwPQtrvzQ6ElBws20XXPE4eaIYPsr39vgMqtZIQVHnl4Fwjf3QMil/9u7UjhXl93CKWdFobu4g+jQ=="
  }
}
AGC_TOOLCHAIN_PINS
chmod 0600 "$pins"
exec 9>/var/lib/agc-worker/toolchain-setup.lock
flock -n 9
# apt and image extraction stay inside the setup unit's 2 GiB/one CPU cap.
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl xz-utils skopeo umoci
base=/var/lib/agc-worker/toolchains
install -d -m 0755 "$base"
for debian in 12 13; do
  node=22.22.2
  test "$debian" != 12 || node=22.23.1
  destination="$base/debian$debian"
  read -r image node_url node_sha < <(python3 - "$pins" "$debian" "$node" <<'PY'
import json,sys
p=json.load(open(sys.argv[1])); n=p['node'][sys.argv[3]]
print(p['images'][sys.argv[2]]['reference'],n['url'],n['sha256'])
PY
)
  if test -f "$destination/.agc-worker-toolchain.json"; then
    python3 - "$destination/.agc-worker-toolchain.json" "$image" "$node" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]));assert p['version']==1 and p['image']==sys.argv[2] and p['node']=='v'+sys.argv[3]
PY
    echo "Already installed: debian$debian"
    continue
  fi
  # Never replace an incomplete root automatically: retain it for reconciliation.
  test ! -e "$destination"
  temporary="$base/.debian$debian-setup"
  test ! -e "$temporary"
  install -d -m 0700 "$temporary"
  skopeo copy --retry-times 3 "docker://$image" "oci:$temporary/image:rootfs"
  umoci unpack --image "$temporary/image:rootfs" "$temporary/bundle"
  root="$temporary/bundle/rootfs"
  test -d "$root/etc"
  cp /etc/resolv.conf "$root/etc/resolv.conf"
  printf '#!/bin/sh\nexit 101\n' > "$root/usr/sbin/policy-rc.d"
  chmod 0755 "$root/usr/sbin/policy-rc.d"
  # Only standard pseudo-devices in the new root, never a host device bind.
  for spec in 'null 1 3' 'zero 1 5' 'random 1 8' 'urandom 1 9'; do
    read -r device major minor <<<"$spec"
    test -e "$root/dev/$device" || mknod -m 0666 "$root/dev/$device" c "$major" "$minor"
  done
  chroot "$root" /usr/bin/apt-get update -qq
  chroot "$root" /usr/bin/apt-get install -y --no-install-recommends ca-certificates curl xz-utils git python3 build-essential pkg-config sqlite3
  # Match the supervisor's numeric identity inside each chroot, without any
  # staff account/profile. Node and browser libraries can resolve their home.
  worker_uid=$(id -u agcjob)
  worker_gid=$(id -g agcjob)
  chroot "$root" /usr/sbin/groupadd --gid "$worker_gid" agcjob
  chroot "$root" /usr/sbin/useradd --uid "$worker_uid" --gid "$worker_gid" --home-dir /var/lib/agc-job-home --create-home --shell /usr/sbin/nologin agcjob
  archive="$temporary/node.tar.xz"
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --max-time 300 "$node_url" -o "$archive"
  printf '%s  %s\n' "$node_sha" "$archive" | sha256sum --check --status
  tar -xJf "$archive" --strip-components=1 -C "$root/usr/local"
  test "$(chroot "$root" /usr/local/bin/node --version)" = "v$node"
  libc=$(chroot "$root" /usr/bin/getconf GNU_LIBC_VERSION)
  if test "$debian" = 12; then
    test "$libc" = 'glibc 2.36'
    read -r pnpm_url pnpm_integrity < <(python3 - "$pins" <<'PY_PNPM'
import json,sys
p=json.load(open(sys.argv[1]))['pnpm'];print(p['url'],p['integrity'])
PY_PNPM
)
    pnpm_archive="$root/tmp/agc-pnpm-11.16.0.tgz"
    curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --max-time 300 "$pnpm_url" -o "$pnpm_archive"
    python3 - "$pnpm_archive" "$pnpm_integrity" <<'PY_PNPM'
import base64,hashlib,sys
with open(sys.argv[1],'rb') as f: actual='sha512-'+base64.b64encode(hashlib.file_digest(f,'sha512').digest()).decode()
assert actual==sys.argv[2]
PY_PNPM
    chroot "$root" /usr/local/bin/npm install --global --ignore-scripts --no-audit --no-fund /tmp/agc-pnpm-11.16.0.tgz
    rm -- "$pnpm_archive"
    test "$(chroot "$root" /usr/local/bin/pnpm --version)" = 11.16.0
  else
    test "$libc" = 'glibc 2.41'
  fi
  chroot "$root" /usr/bin/dpkg-query -W -f='${Package}\t${Version}\n' > "$root/.agc-worker-packages.tsv"
  chroot "$root" /usr/bin/apt-get clean
  python3 - "$root/.agc-worker-toolchain.json" "$image" "$node" "$node_sha" "$debian" "$pins" <<'PY'
from pathlib import Path
import datetime,json,sys
path=Path(sys.argv[1]); path.write_text(json.dumps({'version':1,'image':sys.argv[2],
 'node':'v'+sys.argv[3],'node_sha256':sys.argv[4],'debian':sys.argv[5],'pnpm':json.load(open(sys.argv[6])).get('pnpm') if sys.argv[5]=='12' else None,
 'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()},sort_keys=True)+'\n')
path.chmod(0o600)
PY
  chown root:root "$root"
  chmod 0755 "$root"
  mv "$root" "$destination"
  # These are setup-only image/archive copies; installed root and receipt remain.
  rm -rf -- "$temporary"
  echo "Installed: debian$debian node$node"
done
