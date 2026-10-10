#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

systemctl --user disable --now agent-console-tailscale-tunnel.service agent-console-web.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/agent-console-tailscale-tunnel.service"
rm -f "$HOME/.config/systemd/user/agent-console-web.service"
systemctl --user daemon-reload
for name in agentctl agent-selector agent-console-status agent-console-logs; do
  rm -f "$HOME/bin/$name" "$HOME/.local/bin/$name"
done
printf '%s\n' 'Services and wrappers removed. Sessions, state, plans, worktrees, profiles, and keys were preserved.'
