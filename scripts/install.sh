#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

_resolve_bin() {
  local var="$1" name="$2" required="${3:-1}"
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
      if [ "$required" = "1" ]; then
        printf 'FATAL: %s=%s is not an executable file.\n' "$var" "$val" >&2
        exit 1
      fi
      printf 'WARNING: %s=%s is not an executable file; skipping optional tool %s.\n' "$var" "$val" "$name" >&2
      printf ''
      return 0
    fi
    printf '%s' "$val"
    return 0
  fi
  command -v "$name" || true
}

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
state="${AGENT_CONSOLE_STATE_DIR:-$HOME/.local/share/agent-console}"
mkdir -p "$state" "$HOME/bin" "$HOME/.local/bin" "$HOME/.config/systemd/user"
chmod 700 "$state"
python3 -m venv "$state/venv"
"$state/venv/bin/pip" install -r "$root/web/requirements.txt"
npm_cmd="$(command -v npm || echo "$HOME/.local/bin/npm")"
codex_bin=$(_resolve_bin AGCONSOLE_CODEX_BIN codex)
claude_bin=$(_resolve_bin AGCONSOLE_CLAUDE_BIN claude 0)
opencode_bin=$(_resolve_bin AGCONSOLE_OPENCODE_BIN opencode)
hermes_bin=$(_resolve_bin AGCONSOLE_HERMES_BIN hermes)
(cd "$root" && "$npm_cmd" ci --omit=dev --no-audit --no-fund)


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

source_root="${AGENT_CONSOLE_SOURCE_ROOT:-$root}"
case "$source_root" in
  /*) ;;
  *)
    printf 'FATAL: AGENT_CONSOLE_SOURCE_ROOT=%s must be an absolute path.\n' "$source_root" >&2
    exit 1
    ;;
esac
if [ ! -d "$source_root" ]; then
  printf 'FATAL: AGENT_CONSOLE_SOURCE_ROOT=%s is not a directory.\n' "$source_root" >&2
  exit 1
fi
canary_bind="${AGENT_CONSOLE_CANARY_BIND:-127.0.0.1}"
canary_port="${AGENT_CONSOLE_CANARY_PORT:-33100}"
case "$canary_port" in
  ''|*[!0-9]*)
    printf 'FATAL: AGENT_CONSOLE_CANARY_PORT=%s is not numeric.\n' "$canary_port" >&2
    exit 1
    ;;
esac
if [ "$canary_port" -lt 1 ] || [ "$canary_port" -gt 65535 ]; then
  printf 'FATAL: AGENT_CONSOLE_CANARY_PORT=%s is out of range (1-65535).\n' "$canary_port" >&2
  exit 1
fi
deployment_mode="${AGENT_CONSOLE_DEPLOYMENT_MODE:-disabled}"
case "$deployment_mode" in
  disabled|staging) ;;
  *)
    printf 'FATAL: AGENT_CONSOLE_DEPLOYMENT_MODE=%s is not valid (must be disabled or staging).\n' "$deployment_mode" >&2
    exit 1
    ;;
esac

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
tunnel_host="${AGENT_CONSOLE_TUNNEL_HOST:-localhost}"

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
  echo "AGENT_CONSOLE_DB=${AGENT_CONSOLE_DB:-$state_dir/agent-console.sqlite3}"
  echo "AGENT_CONSOLE_CONFIG_DIR=$config_dir"
  echo "AGENT_CONSOLE_PROFILE_DIR=$profile_dir"
  echo "AGENT_CONSOLE_HANDOFF_DIR=$handoff_dir"
  echo "AGENT_CONSOLE_WORKTREE_ROOT=$worktree_root"
  echo "AGENT_CONSOLE_PORT=$port"
  echo "AGENT_CONSOLE_TUNNEL_PORT=$tunnel_port"
  echo "AGENT_CONSOLE_TUNNEL_HOST=$tunnel_host"
  echo "AGENT_CONSOLE_SOURCE_ROOT=$source_root"
  echo "AGENT_CONSOLE_BIND_HOST=$bind_host"
  echo "AGENT_CONSOLE_CANARY_BIND=$canary_bind"
  echo "AGENT_CONSOLE_CANARY_PORT=$canary_port"
  echo "AGENT_CONSOLE_DEPLOYMENT_MODE=$deployment_mode"
  [ -n "$codex_bin" ] && echo "AGCONSOLE_CODEX_BIN=$codex_bin"
  [ -n "$claude_bin" ] && echo "AGCONSOLE_CLAUDE_BIN=$claude_bin"
  [ -n "$opencode_bin" ] && echo "AGCONSOLE_OPENCODE_BIN=$opencode_bin"
  [ -n "$hermes_bin" ] && echo "AGCONSOLE_HERMES_BIN=$hermes_bin"
  echo "AGCONSOLE_SKILLS_ROOT=$skills_root"
  echo "AGCONSOLE_RETAINED_SKILLS=$retained_skills"
} > "$config_dir/runtime.env"
chmod 600 "$config_dir/runtime.env"

runner_path="$state/runner.sh"
cat > "$runner_path" <<RUNNEREOF
#!/usr/bin/env bash
set -euo pipefail
runner_state="$state"
runner_root="$root"
runner_bind="$bind_host"
runner_port="$port"
runner_releases="\$runner_state/releases"
runner_current="\$runner_state/releases/current"
if [ -L "\$runner_current" ]; then
  target="\$(readlink -f "\$runner_current")"
  case "\$target" in
    "\$runner_releases"/release-*) release_contained=true ;;
    *) release_contained=false ;;
  esac
  if \$release_contained && [ -d "\$target/agent_console" ]; then
    has_xterm=true
    for asset in node_modules/@xterm/xterm/lib/xterm.mjs node_modules/@xterm/xterm/css/xterm.css node_modules/@xterm/addon-fit/lib/addon-fit.mjs; do
      if [ ! -f "\$target/\$asset" ]; then has_xterm=false; break; fi
    done
    if \$has_xterm; then
      cd "\$target"
      PYTHONPATH="\$target" exec "\$runner_state/venv/bin/uvicorn" agent_console.web:app --host "\$runner_bind" --port "\$runner_port" --no-proxy-headers
    fi
  fi
fi
cd "\$runner_root"
PYTHONPATH="\$runner_root" exec "\$runner_state/venv/bin/uvicorn" agent_console.web:app --host "\$runner_bind" --port "\$runner_port" --no-proxy-headers
RUNNEREOF
chmod 700 "$runner_path"

cat > "$HOME/.config/systemd/user/agent-console-web.service" <<EOF
[Unit]
Description=Agent Console web terminal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$config_dir/runtime.env
ExecStart=$runner_path
Restart=on-failure
RestartSec=3
KillMode=process
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=default.target
EOF

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

# Bootstrap owns fresh initialization; upgrades retain the selected release.
# Missing current with an existing DB is refused, never a checkout fallback.
python3 -B "$root/scripts/install-entrypoints.py" \
  --home "$HOME" --state "$state_dir" --releases "$state_dir/releases" \
  --bootstrap-source "$root" --database "${AGENT_CONSOLE_DB:-$state_dir/agent-console.sqlite3}" \
  --config "$config_dir"

systemctl --user daemon-reload
systemctl --user enable agent-console-web.service
systemctl --user restart agent-console-web.service

PYTHONPATH="$state_dir/releases/current" python3 -B -m agent_console.cli doctor

printf '\nSkills setup (optional):\n'
printf '  export AGCONSOLE_SKILLS_ROOT=%s\n' "$skills_root"
printf '  export AGCONSOLE_RETAINED_SKILLS=%s\n' "$retained_skills"
if [ -d "$skills_root" ]; then
  printf '  agentctl skills sync\n'
fi
printf '  agentctl skills doctor\n'
printf '\nAgent Console installed. Tunnel service configured but not started (requires SSH setup).\n'
