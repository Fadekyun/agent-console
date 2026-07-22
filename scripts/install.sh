#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
state="$HOME/.local/share/agent-console"
mkdir -p "$state" "$HOME/bin" "$HOME/.local/bin" "$HOME/.config/systemd/user"
chmod 700 "$state"
python3 -m venv "$state/venv"
"$state/venv/bin/pip" install -r "$root/web/requirements.txt"
npm_cmd="$(command -v npm || echo "$HOME/.local/bin/npm")"
codex_bin="$(command -v codex || true)"
claude_bin="$(command -v claude || true)"
opencode_bin="$(command -v opencode || true)"
hermes_bin="$(command -v hermes || true)"
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

workspace_root="${AGENT_CONSOLE_WORKSPACE_ROOT:-$root}"
state_dir="${AGENT_CONSOLE_STATE_DIR:-$HOME/.local/share/agent-console}"
config_dir="${AGENT_CONSOLE_CONFIG_DIR:-$HOME/.config/agent-console}"
profile_dir="${AGENT_CONSOLE_PROFILE_DIR:-$workspace_root/agent-profiles}"
handoff_dir="${AGENT_CONSOLE_HANDOFF_DIR:-$workspace_root/handoffs}"
worktree_root="${AGENT_CONSOLE_WORKTREE_ROOT:-$workspace_root/worktrees}"
skills_root="${AGCONSOLE_SKILLS_ROOT:-$HOME/codex/skills}"
retained_skills="${AGCONSOLE_RETAINED_SKILLS:-}"

mkdir -p "$config_dir"
cat > "$config_dir/runtime.env" <<EOF
AGENT_CONSOLE_TAILSCALE_LOGIN=$tailscale_login
AGENT_CONSOLE_MAX_PTY_CLIENTS=2
AGENT_CONSOLE_TMUX_SOCKET_PATH=/run/user/$(id -u)/agent-console/tmux.sock
AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH=/tmp/tmux-$(id -u)/default
AGENT_CONSOLE_LAN_CIDR=$lan_cidr
AGENT_CONSOLE_TRUSTED_HOSTS=$trusted_hosts
AGENT_CONSOLE_WORKSPACE_ROOT=$workspace_root
AGENT_CONSOLE_STATE_DIR=$state_dir
AGENT_CONSOLE_CONFIG_DIR=$config_dir
AGENT_CONSOLE_PROFILE_DIR=$profile_dir
AGENT_CONSOLE_HANDOFF_DIR=$handoff_dir
AGENT_CONSOLE_WORKTREE_ROOT=$worktree_root
AGENT_CONSOLE_PORT=$port
AGCONSOLE_CODEX_BIN=$codex_bin
AGCONSOLE_CLAUDE_BIN=$claude_bin
AGCONSOLE_OPENCODE_BIN=$opencode_bin
AGCONSOLE_HERMES_BIN=$hermes_bin
AGCONSOLE_SKILLS_ROOT=$skills_root
AGCONSOLE_RETAINED_SKILLS=$retained_skills
EOF
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
ExecStart=/usr/bin/ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:13210:$bind_host:$port $tunnel_host
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now agent-console-web.service

profile_count=$(ls -1 "$profile_dir"/*.md 2>/dev/null | wc -l)
if [ "$profile_count" -eq 0 ]; then
  printf 'WARNING: profile dir %s has no *.md files. Set AGENT_CONSOLE_PROFILE_DIR if profiles are elsewhere.\n' "$profile_dir"
fi

"$HOME/bin/agentctl" doctor

if [ -n "$skills_root" ] && [ -d "$skills_root" ]; then
  "$HOME/bin/agentctl" skills sync 2>/dev/null || true
fi

printf '%s\n' 'Agent Console installed. Tunnel service configured but not started (requires SSH setup).'
