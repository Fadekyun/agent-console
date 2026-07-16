#!/usr/bin/env bash
set -euo pipefail

stamp="$(date +%Y%m%d_%H%M%S)"
target="$HOME/.local/share/agent-console/backups/$stamp"
mkdir -p "$target"
chmod 700 "$target"
cp -a "$HOME/.local/share/agent-console/agent-console.sqlite3"* "$target/" 2>/dev/null || true
cp -a "$HOME/.local/share/agent-console/ssh-key-inventory.tsv" "$target/" 2>/dev/null || true
cp -a "$HOME/.config/systemd/user/agent-console-"*.service "$target/" 2>/dev/null || true
ssh -o BatchMode=yes "${AGENT_CONSOLE_TUNNEL_HOST:-localhost}" tailscale serve get-config --all > "$target/tunnel-serve-config.json" 2>/dev/null || true
find "$target" -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum > "$target/SHA256SUMS"
printf '%s\n' "$target"
