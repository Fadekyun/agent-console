#!/bin/bash
# Install only the pinned headless browser dependencies in the held worker root.
# Package closure: AGC a1f425c2; permits replacement of the exact failed prior lock only.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -e /etc/agc-worker/hold
root=/var/lib/agc-worker/toolchains/debian13
test -f "$root/.agc-worker-toolchain.json"
install -d -m 0755 "$root/opt/agc-browser"
python3 - <<'PY_BROWSER'
import base64,pathlib,hashlib
files={'package.json': 'ewogICJuYW1lIjogImFnYy1jb21wdXRlLWJyb3dzZXIiLAogICJ2ZXJzaW9uIjogIjEuMC4wIiwKICAicHJpdmF0ZSI6IHRydWUsCiAgImRlcGVuZGVuY2llcyI6IHsKICAgICJwbGF5d3JpZ2h0IjogIjEuNjIuMSIsCiAgICAiY3N2LXBhcnNlIjogIjUuNi4wIgogIH0KfQo=', 'package-lock.json': 'ewogICJuYW1lIjogImFnYy1jb21wdXRlLWJyb3dzZXIiLAogICJ2ZXJzaW9uIjogIjEuMC4wIiwKICAibG9ja2ZpbGVWZXJzaW9uIjogMywKICAicmVxdWlyZXMiOiB0cnVlLAogICJwYWNrYWdlcyI6IHsKICAgICIiOiB7CiAgICAgICJuYW1lIjogImFnYy1jb21wdXRlLWJyb3dzZXIiLAogICAgICAidmVyc2lvbiI6ICIxLjAuMCIsCiAgICAgICJkZXBlbmRlbmNpZXMiOiB7CiAgICAgICAgInBsYXl3cmlnaHQiOiAiMS42Mi4xIiwKICAgICAgICAiY3N2LXBhcnNlIjogIjUuNi4wIgogICAgICB9CiAgICB9LAogICAgIm5vZGVfbW9kdWxlcy9wbGF5d3JpZ2h0IjogewogICAgICAidmVyc2lvbiI6ICIxLjYyLjEiLAogICAgICAicmVzb2x2ZWQiOiAiaHR0cHM6Ly9yZWdpc3RyeS5ucG1qcy5vcmcvcGxheXdyaWdodC8tL3BsYXl3cmlnaHQtMS42Mi4xLnRneiIsCiAgICAgICJpbnRlZ3JpdHkiOiAic2hhNTEyLTBNK0wzTEFEOC9ubTU1NExPbGE5QXl4MGowdG1GWjBGQmNvUTdGMVZ1VkhwTS9YcGlDOFJjRHpCUUI4VzUraEE4TDIyVEh4RUx6ZUYrMldjVXp2Y0xnPT0iLAogICAgICAibGljZW5zZSI6ICJBcGFjaGUtMi4wIiwKICAgICAgImRlcGVuZGVuY2llcyI6IHsKICAgICAgICAicGxheXdyaWdodC1jb3JlIjogIjEuNjIuMSIKICAgICAgfSwKICAgICAgImJpbiI6IHsKICAgICAgICAicGxheXdyaWdodCI6ICJjbGkuanMiCiAgICAgIH0sCiAgICAgICJlbmdpbmVzIjogewogICAgICAgICJub2RlIjogIj49MjAiCiAgICAgIH0sCiAgICAgICJvcHRpb25hbERlcGVuZGVuY2llcyI6IHsKICAgICAgICAiZnNldmVudHMiOiAiMi4zLjIiCiAgICAgIH0KICAgIH0sCiAgICAibm9kZV9tb2R1bGVzL3BsYXl3cmlnaHQtY29yZSI6IHsKICAgICAgInZlcnNpb24iOiAiMS42Mi4xIiwKICAgICAgInJlc29sdmVkIjogImh0dHBzOi8vcmVnaXN0cnkubnBtanMub3JnL3BsYXl3cmlnaHQtY29yZS8tL3BsYXl3cmlnaHQtY29yZS0xLjYyLjEudGd6IiwKICAgICAgImludGVncml0eSI6ICJzaGE1MTItd1BZU3dFQkpZOUdIcmFJU1hxeXF0eDBuYTBMcE8zWEVYN2pORGhudGJleDd0elVTN2tMblpzT2xGcnVGSkI0SGkvcmhETWpYR3FIZXdEWjY4bllaVnc9PSIsCiAgICAgICJsaWNlbnNlIjogIkFwYWNoZS0yLjAiLAogICAgICAiYmluIjogewogICAgICAgICJwbGF5d3JpZ2h0LWNvcmUiOiAiY2xpLmpzIgogICAgICB9LAogICAgICAiZW5naW5lcyI6IHsKICAgICAgICAibm9kZSI6ICI+PTIwIgogICAgICB9CiAgICB9LAogICAgIm5vZGVfbW9kdWxlcy9mc2V2ZW50cyI6IHsKICAgICAgInZlcnNpb24iOiAiMi4zLjIiLAogICAgICAicmVzb2x2ZWQiOiAiaHR0cHM6Ly9yZWdpc3RyeS5ucG1qcy5vcmcvZnNldmVudHMvLS9mc2V2ZW50cy0yLjMuMi50Z3oiLAogICAgICAiaW50ZWdyaXR5IjogInNoYTUxMi14aXFNUVI0eEFlSFR1Qjl1V20rZkZSY0lPZ0tCTWlPQlArZVhpeVQ3anNnVkNxMWJrVnlndDAwb0FTb3dCN0VkdHBPSGFhUGdLdDgxMlA5YWIrRERLQT09IiwKICAgICAgImhhc0luc3RhbGxTY3JpcHQiOiB0cnVlLAogICAgICAibGljZW5zZSI6ICJNSVQiLAogICAgICAib3B0aW9uYWwiOiB0cnVlLAogICAgICAib3MiOiBbCiAgICAgICAgImRhcndpbiIKICAgICAgXSwKICAgICAgImVuZ2luZXMiOiB7CiAgICAgICAgIm5vZGUiOiAiXjguMTYuMCB8fCBeMTAuNi4wIHx8ID49MTEuMC4wIgogICAgICB9CiAgICB9LAogICAgIm5vZGVfbW9kdWxlcy9jc3YtcGFyc2UiOiB7CiAgICAgICJ2ZXJzaW9uIjogIjUuNi4wIiwKICAgICAgInJlc29sdmVkIjogImh0dHBzOi8vcmVnaXN0cnkubnBtanMub3JnL2Nzdi1wYXJzZS8tL2Nzdi1wYXJzZS01LjYuMC50Z3oiLAogICAgICAiaW50ZWdyaXR5IjogInNoYTUxMi1sM256M2V1dWIyUU1nNW91dTVVMDlFdzlXZjYvd1E4SSsrY2gxbG9RMGxqbXpobWZaWXJIOWZmbFMyMmkvUFFFdnNQdnhDd3hnejVxN1VCOEsxSk80UT09IgogICAgfQogIH0KfQo='}
root=pathlib.Path('/var/lib/agc-worker/toolchains/debian13/opt/agc-browser')
for name,value in files.items():
 p=root/name;data=base64.b64decode(value)
 if p.exists() and p.read_bytes()!=data and not (name=='package-lock.json' and hashlib.sha256(p.read_bytes()).hexdigest()=='f56812dbf4dc451121dd8c0f12c862c41cabacab3c99d8768381d185693c6721'):raise RuntimeError('Existing browser dependency source differs')
 p.write_bytes(data);p.chmod(0o644)
PY_BROWSER
chroot "$root" /bin/sh -c 'cd /opt/agc-browser && /usr/local/bin/npm ci --ignore-scripts --no-audit --no-fund'
chroot "$root" /usr/bin/apt-get update -qq
chroot "$root" /usr/bin/apt-get install -y --no-install-recommends python3-bs4
PLAYWRIGHT_BROWSERS_PATH=/opt/agc-browser/browsers chroot "$root" /usr/local/bin/node /opt/agc-browser/node_modules/playwright/cli.js install --with-deps --only-shell chromium
install -d -m 0755 "$root/var/lib/agc-job-home/.cache"
if test -e "$root/var/lib/agc-job-home/.cache/ms-playwright" || test -L "$root/var/lib/agc-job-home/.cache/ms-playwright"; then
  test "$(readlink "$root/var/lib/agc-job-home/.cache/ms-playwright")" = /opt/agc-browser/browsers
else
  ln -s /opt/agc-browser/browsers "$root/var/lib/agc-job-home/.cache/ms-playwright"
fi
chmod -R a+rX "$root/opt/agc-browser"
chroot "$root" /usr/bin/apt-get clean
chroot "$root" /usr/bin/dpkg-query -W -f='${Package}\t${Version}\n' > "$root/.agc-worker-packages.tsv"
chroot "$root" /usr/local/bin/node -e "console.log(JSON.stringify({playwright:require('/opt/agc-browser/node_modules/playwright/package.json').version,node:process.version}))"
