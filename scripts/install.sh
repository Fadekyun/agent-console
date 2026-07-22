#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

_resolve_bin() {
  local var="$1" name="$2"
  local val="${!var:-}"
  if [ -n "$val" ]; then
    case "$val" in
      /*) ;;
      *)
        printf 'FATAL: %s=%s must be an absolute path.\n' "$var" "$val" >&2
        exit 1
        ;;
    esac
    if [ ! -x "$val" ]; then
      printf 'FATAL: %s=%s is not an executable file.\n' "$var" "$val" >&2
      exit 1
    fi
    printf '%s' "$val"
    return 0
  fi
  command -v "$name" || true
}

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
state="$HOME/.local/share/agent-console"
mkdir -p "$state" "$HOME/bin" "$HOME/.local/bin" "$HOME/.config/systemd/user"
chmod 700 "$state"
python3 -m venv "$state/venv"
"$state/venv/bin/pip" install -r "$root/web/requirements.txt"
npm_cmd="$(command -v npm || echo "$HOME/.local/bin/npm")"
codex_bin=$(_resolve_bin AGCONSOLE_CODEX_BIN codex)
claude_bin=$(_resolve_bin AGCONSOLE_CLAUDE_BIN claude)
opencode_bin=$(_resolve_bin AGCONSOLE_OPENCODE_BIN opencode)
hermes_bin=$(_resolve_bin AGCONSOLE_HERMES_BIN hermes)
(cd "$root" && "$npm_cmd" ci --omit=dev --no-audit --no-fund)
for name in agentctl agent-selector agent-console-status agent-console-logs; do
  ln -sfn "$root/scripts/$name" "$HOME/bin/$name"
  ln -sfn "$HOME/bin/$name" "$HOME/.local/bin/$name"
done

tailscale_login="${AGENT_CONSOLE_TAILSCALE_LOGIN:-your-email@example.com}"
lan_cidr="${AGENT_CONSOLE_LAN_CIDR:-127.0.0.1/32}"
trusted_hosts="${AGENT_CONSOLE_TRUSTED_HOSTS:-localhost,127.0.0.1}"
bind_host="${AGENT_CONSOLE_BIND_HOST:-127.0.0.1}"

port="${AGENT_CONSOLE_PORT:-3210}"
case "$port" in
  ''|*[!0-9]*)
    printf 'FATAL: AGENT_CONSOLE_PORT=%s is not numeric.\n' "$port" >&2
    exit 1
    ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
  printf 'FATAL: AGENT_CONSOLE_PORT=%s is out of range (1-65535).\n' "$port" >&2
  exit 1
fi

tunnel_port="${AGENT_CONSOLE_TUNNEL_PORT:-13210}"
case "$tunnel_port" in
  ''|*[!0-9]*)
    printf 'FATAL: AGENT_CONSOLE_TUNNEL_PORT=%s is not numeric.\n' "$tunnel_port" >&2
    exit 1
    ;;
esac
if [ "$tunnel_port" -lt 1 ] || [ "$tunnel_port" -gt 65535 ]; then
  printf 'FATAL: AGENT_CONSOLE_TUNNEL_PORT=%s is out of range (1-65535).\n' "$tunnel_port" >&2
  exit 1
fi

workspace_root="${AGENT_CONSOLE_WORKSPACE_ROOT:-$root}"
state_dir="${AGENT_CONSOLE_STATE_DIR:-$HOME/.local/share/agent-console}"
config_dir="${AGENT_CONSOLE_CONFIG_DIR:-$HOME/.config/agent-console}"
profile_dir="${AGENT_CONSOLE_PROFILE_DIR:-$root/agent-profiles}"
handoff_dir="${AGENT_CONSOLE_HANDOFF_DIR:-$workspace_root/handoffs}"
worktree_root="${AGENT_CONSOLE_WORKTREE_ROOT:-$workspace_root/worktrees}"
skills_root="${AGCONSOLE_SKILLS_ROOT:-$HOME/codex/skills}"
retained_skills="${AGCONSOLE_RETAINED_SKILLS:-}"

mkdir -p "$config_dir"

shopt -s nullglob
profile_files=( "$profile_dir"/*.md )
shopt -u nullglob
if [ ! -d "$profile_dir" ] || [ ${#profile_files[@]} -eq 0 ]; then
  printf 'FATAL: profile dir %s has no *.md files. Set AGENT_CONSOLE_PROFILE_DIR.\n' "$profile_dir" >&2
  exit 1
fi

{
  echo "AGENT_CONSOLE_TAILSCALE_LOGIN=$tailscale_login"
  echo "AGENT_CONSOLE_MAX_PTY_CLIENTS=2"
  echo "AGENT_CONSOLE_TMUX_SOCKET_PATH=/run/user/$(id -u)/agent-console/tmux.sock"
  echo "AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH=/tmp/tmux-$(id -u)/default"
  echo "AGENT_CONSOLE_LAN_CIDR=$lan_cidr"
  echo "AGENT_CONSOLE_TRUSTED_HOSTS=$trusted_hosts"
  echo "AGENT_CONSOLE_WORKSPACE_ROOT=$workspace_root"
  echo "AGENT_CONSOLE_STATE_DIR=$state_dir"
  echo "AGENT_CONSOLE_CONFIG_DIR=$config_dir"
  echo "AGENT_CONSOLE_PROFILE_DIR=$profile_dir"
  echo "AGENT_CONSOLE_HANDOFF_DIR=$handoff_dir"
  echo "AGENT_CONSOLE_WORKTREE_ROOT=$worktree_root"
  echo "AGENT_CONSOLE_PORT=$port"
  echo "AGENT_CONSOLE_TUNNEL_PORT=$tunnel_port"
  [ -n "$codex_bin" ] && echo "AGCONSOLE_CODEX_BIN=$codex_bin"
  [ -n "$claude_bin" ] && echo "AGCONSOLE_CLAUDE_BIN=$claude_bin"
  [ -n "$opencode_bin" ] && echo "AGCONSOLE_OPENCODE_BIN=$opencode_bin"
  [ -n "$hermes_bin" ] && echo "AGCONSOLE_HERMES_BIN=$hermes_bin"
  echo "AGCONSOLE_SKILLS_ROOT=$skills_root"
  echo "AGCONSOLE_RETAINED_SKILLS=$retained_skills"
} > "$config_dir/runtime.env"
chmod 600 "$config_dir/runtime.env"

cat > "$HOME/.config/systemd/user/agent-console-web.service" <<EOF
[Unit]
Description=Agent Console web terminal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$root
Environment=PYTHONPATH=$root
EnvironmentFile=$config_dir/runtime.env
ExecStart=$state/venv/bin/uvicorn agent_console.web:app --host $bind_host --port $port --no-proxy-headers
Restart=on-failure
RestartSec=3
KillMode=process
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=default.target
EOF

tunnel_host="${AGENT_CONSOLE_TUNNEL_HOST:-localhost}"

cat > "$HOME/.config/systemd/user/agent-console-tailscale-tunnel.service" <<EOF
[Unit]
Description=Agent Console reverse tunnel
After=network-online.target agent-console-web.service
Wants=network-online.target agent-console-web.service

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:$tunnel_port:$bind_host:$port $tunnel_host
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable agent-console-web.service
systemctl --user restart agent-console-web.service

"$HOME/bin/agentctl" doctor

printf '\nSkills setup (optional):\n'
printf '  export AGCONSOLE_SKILLS_ROOT=%s\n' "$skills_root"
printf '  export AGCONSOLE_RETAINED_SKILLS=%s\n' "$retained_skills"
if [ -d "$skills_root" ]; then
  printf '  agentctl skills sync\n'
fi
printf '  agentctl skills doctor\n'
printf '\nAgent Console installed. Tunnel service configured but not started (requires SSH setup).\n'
