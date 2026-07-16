#!/usr/bin/env zsh
set -euo pipefail

BEGIN_MARKER='# BEGIN N100 AGENT CONSOLE'
END_MARKER='# END N100 AGENT CONSOLE'
CONFIG_PATH="${HOME}/.ssh/config"

usage() {
  print 'Usage:'
  print '  install-client.zsh install --tailscale-host HOST --lan-ip IP --identity-file PATH'
  print '  install-client.zsh uninstall'
}

remove_managed_block() {
  python3 - "$CONFIG_PATH" "$BEGIN_MARKER" "$END_MARKER" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
begin, end = sys.argv[2:4]
if not path.exists():
    raise SystemExit(0)
lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
out, inside = [], False
for line in lines:
    marker = line.rstrip('\r\n')
    if marker == begin:
        inside = True
        continue
    if marker == end and inside:
        inside = False
        continue
    if not inside:
        out.append(line)
path.write_text(''.join(out).lstrip('\r\n'), encoding='utf-8')
PY
}

action="${1:-}"
[[ -n "$action" ]] || { usage; exit 2; }
shift

if [[ "$action" == uninstall ]]; then
  remove_managed_block
  chmod 700 "${HOME}/.ssh"
  [[ ! -e "$CONFIG_PATH" ]] || chmod 600 "$CONFIG_PATH"
  print 'Removed only the N100 Agent Console managed SSH block.'
  exit 0
fi

[[ "$action" == install ]] || { usage; exit 2; }
tailscale_host=''
lan_ip=''
identity_file=''
while (( $# )); do
  case "$1" in
    --tailscale-host) tailscale_host="$2"; shift 2 ;;
    --lan-ip) lan_ip="$2"; shift 2 ;;
    --identity-file) identity_file="$2"; shift 2 ;;
    *) print -u2 "Unknown argument: $1"; usage; exit 2 ;;
  esac
done

[[ -n "$tailscale_host" && -n "$lan_ip" && -n "$identity_file" ]] || { usage; exit 2; }
identity_file="${identity_file/#\~/$HOME}"
[[ -f "$identity_file" ]] || { print -u2 "Private key not found: $identity_file"; exit 2; }

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
touch "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
backup="${CONFIG_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
cp -p "$CONFIG_PATH" "$backup"
remove_managed_block

block="$(mktemp)"
trap 'rm -f "$block"' EXIT
cat > "$block" <<EOF
$BEGIN_MARKER
# Web console: https://${tailscale_host}/
# SSH reaches N100 through its LAN address; remote Tailscale clients use CT 969's subnet route.
Host n100-lan
    HostName ${lan_ip}
    User ${USER:-$(whoami)}
    IdentityFile ${identity_file}
    IdentitiesOnly yes

Host codex claude opencode hermes
    HostName ${lan_ip}
    User ${USER:-$(whoami)}
    IdentityFile ${identity_file}
    IdentitiesOnly yes
    RequestTTY force
    RemoteCommand /home/${USER:-$(whoami)}/bin/agent-selector --tool %n

Host codex-direct claude-direct opencode-direct hermes-direct
    HostName ${lan_ip}
    User ${USER:-$(whoami)}
    IdentityFile ${identity_file}
    IdentitiesOnly yes
    RequestTTY force
    RemoteCommand /home/${USER:-$(whoami)}/bin/agent-selector --direct %n

Host ai-*
    HostName ${lan_ip}
    User ${USER:-$(whoami)}
    IdentityFile ${identity_file}
    IdentitiesOnly yes
    RequestTTY force
    RemoteCommand /home/${USER:-$(whoami)}/bin/agent-selector --session %n
$END_MARKER
EOF

python3 - "$CONFIG_PATH" "$block" <<'PY'
import pathlib, sys
config, block = map(pathlib.Path, sys.argv[1:3])
existing = config.read_text(encoding='utf-8')
managed = block.read_text(encoding='utf-8').rstrip() + '\n\n'
config.write_text(managed + existing.lstrip(), encoding='utf-8')
PY
chmod 600 "$CONFIG_PATH"

for alias in n100-lan codex claude opencode hermes codex-direct ai-test; do
  ssh -G "$alias" >/dev/null
done

print "Installed aliases: n100-lan, codex, claude, opencode, hermes, *-direct, ai-*"
print "Backup: $backup"
print "Web console: https://${tailscale_host}/"
