#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

AGENT_CONSOLE_URL="${AGENT_CONSOLE_URL:-http://localhost:3210}"
AGENT_CONSOLE_TUNNEL_HOST="${AGENT_CONSOLE_TUNNEL_HOST:-localhost}"

agentctl doctor
agentctl session list >/dev/null
systemctl --user is-active agent-console-web.service
systemctl --user is-active agent-console-tailscale-tunnel.service
curl --fail --silent "${AGENT_CONSOLE_URL}/healthz"
ssh -o BatchMode=yes "${AGENT_CONSOLE_TUNNEL_HOST}" curl --fail --silent http://127.0.0.1:13210/healthz
tmux -S "/run/user/$(id -u)/agent-console/tmux.sock" list-sessions -F '#{session_name}\t#{session_attached}\t#{session_windows}'
printf '%s\n' 'Agent Console core verification passed.'
