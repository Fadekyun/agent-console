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
(cd "$root" && "$npm_cmd" ci --omit=dev --no-audit --no-fund)
for name in agentctl agent-selector agent-console-status agent-console-logs; do
  ln -sfn "$root/scripts/$name" "$HOME/bin/$name"
  ln -sfn "$HOME/bin/$name" "$HOME/.local/bin/$name"
done

tailscale_login="${AGENT_CONSOLE_TAILSCALE_LOGIN:-your-email@example.com}"
lan_cidr="${AGENT_CONSOLE_LAN_CIDR:-127.0.0.1/32}"
trusted_hosts="${AGENT_CONSOLE_TRUSTED_HOSTS:-localhost,127.0.0.1}"

cat > "$HOME/.config/systemd/user/agent-console-web.service" <<EOF
[Unit]
Description=Agent Console web terminal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$root
Environment=PYTHONPATH=$root
Environment=AGENT_CONSOLE_TAILSCALE_LOGIN=$tailscale_login
Environment=AGENT_CONSOLE_MAX_PTY_CLIENTS=2
Environment=AGENT_CONSOLE_TMUX_SOCKET_PATH=/run/user/$(id -u)/agent-console/tmux.sock
Environment=AGENT_CONSOLE_LEGACY_TMUX_SOCKET_PATH=/tmp/tmux-$(id -u)/default
Environment=AGENT_CONSOLE_LAN_CIDR=$lan_cidr
Environment=AGENT_CONSOLE_TRUSTED_HOSTS=$trusted_hosts
ExecStart=$state/venv/bin/uvicorn agent_console.web:app --host 0.0.0.0 --port 3210 --no-proxy-headers
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
ExecStart=/usr/bin/ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:13210:0.0.0.0:3210 $tunnel_host
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now agent-console-web.service
"$HOME/bin/agentctl" doctor
printf '%s\n' 'Agent Console installed. Tunnel service configured but not started (requires SSH setup).'
