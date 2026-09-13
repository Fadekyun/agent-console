#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
state="${AGENT_CONSOLE_STATE_DIR:-$HOME/.local/share/agent-console}"
config_dir="${AGENT_CONSOLE_CONFIG_DIR:-$HOME/.config/agent-console}"
runtime="$config_dir/runtime.env"
unit="$HOME/.config/systemd/user/agent-console-web.service"
tunnel_unit="$HOME/.config/systemd/user/agent-console-tailscale-tunnel.service"
runner="$state/runner.sh"
requested_sha="${1:-}"

if ! [[ "$requested_sha" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'Usage: %s <40-character-approved-main-sha>\n' "$0" >&2
  exit 2
fi
if [ ! -f "$runtime" ]; then
  printf 'FATAL: existing trusted runtime configuration is required.\n' >&2
  exit 1
fi
# Resolve the configured state before choosing locks, backup paths or the runner.
set -a
source "$runtime"
set +a
state="${AGENT_CONSOLE_STATE_DIR:-$state}"
runner="$state/runner.sh"
if [ ! -f "$runtime" ] || [ ! -f "$unit" ] || [ ! -f "$tunnel_unit" ] || [ ! -f "$runner" ]; then
  printf 'FATAL: existing runtime, service units, and stable runner are required before update.\n' >&2
  exit 1
fi
if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'FATAL: updater must run from a Git checkout.\n' >&2
  exit 1
fi

mkdir -p "$state/update-sources" "$state/update-backups"
chmod 700 "$state/update-sources" "$state/update-backups"
exec 9>"$state/update.lock"
if ! flock -n 9; then
  printf 'FATAL: another Agent Console update is already running.\n' >&2
  exit 1
fi

git -C "$root" fetch --quiet origin main
approved_sha="$(git -C "$root" rev-parse origin/main)"
if [ "$requested_sha" != "$approved_sha" ]; then
  printf 'FATAL: requested SHA %s is not current protected origin/main %s.\n' \
    "$requested_sha" "$approved_sha" >&2
  exit 1
fi

checkout="$state/update-sources/$requested_sha"
if [ -e "$checkout" ]; then
  existing_sha="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  if [ "$existing_sha" != "$requested_sha" ]; then
    printf 'FATAL: existing update checkout has unexpected revision: %s.\n' "$checkout" >&2
    exit 1
  fi
else
  git -C "$root" worktree add --detach "$checkout" "$requested_sha"
fi
if [ -n "$(git -C "$checkout" status --porcelain --untracked-files=all)" ]; then
  printf 'FATAL: update checkout is not clean: %s.\n' "$checkout" >&2
  exit 1
fi

# Read the existing trusted runtime before choosing the database to back up.
set -a
source "$runtime"
set +a
database_path="${AGENT_CONSOLE_DB:-$state/agent-console.sqlite3}"
timestamp="$(date +%Y%m%d_%H%M%S)"
backup="$state/update-backups/${timestamp}-${requested_sha:0:12}"
mkdir -p "$backup"
chmod 700 "$backup"
cp -a "$runtime" "$unit" "$tunnel_unit" "$runner" "$backup/"
mkdir -p "$backup/releases"
current_link="$state/releases/current"
if [ -L "$current_link" ]; then
  current_target="$(readlink -f "$current_link")"
  case "$current_target" in
    "$state/releases"/release-*) cp -a "$current_link" "$backup/releases/" ;;
    *)
      printf 'FATAL: current release link escapes the releases directory: %s.\n' "$current_target" >&2
      exit 1
      ;;
  esac
elif [ -e "$current_link" ]; then
  printf 'FATAL: current release path is not a symlink: %s.\n' "$current_link" >&2
  exit 1
fi
mkdir -p "$backup/bin" "$backup/local-bin"
for name in agentctl agent-selector agent-console-status agent-console-logs; do
  if [ -L "$HOME/bin/$name" ]; then cp -a "$HOME/bin/$name" "$backup/bin/"; fi
  if [ -L "$HOME/.local/bin/$name" ]; then cp -a "$HOME/.local/bin/$name" "$backup/local-bin/"; fi
done
if [ -d "$state/launchers" ]; then
  cp -a "$state/launchers" "$backup/"
fi
# Consistent closed snapshots avoid live WAL-sidecar creation in the guarded
# inventory reader. This is maintenance backup, never an inspection fallback.
"$state/venv/bin/python" -B "$checkout/scripts/snapshot-database.py" \
  "$database_path" "$backup/agent-console.sqlite3"
PYTHONPATH="$checkout" AGENT_CONSOLE_DB="$backup/agent-console.sqlite3" \
  "$state/venv/bin/python" -B -m agent_console.cli session list > "$backup/sessions-before.json"

set -a
source "$runtime"
set +a
health_host="${AGENT_CONSOLE_BIND_HOST:-127.0.0.1}"
case "$health_host" in
  0.0.0.0|::) health_host=127.0.0.1 ;;
esac

wait_for_health() {
  local attempts="${1:-30}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error \
      "http://$health_host:${AGENT_CONSOLE_PORT:-3210}/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

rollback() {
  # Gate BEFORE restoring old runner/CLI/release files: an old writer must never
  # silently relabel a database containing private request data.
  if [ -L "$backup/releases/current" ]; then
    rollback_release="$current_target"
  else
    rollback_release="$root"
  fi
  if ! "$state/venv/bin/python" -B "$checkout/scripts/select-release.py" \
    --database "$database_path" --config "$config_dir" --state "$state" \
    --releases "$state/releases" --release "$rollback_release"; then
    printf 'FATAL: schema-incompatible rollback refused; current source and all database state retained. Use reviewed forward recovery.\n' >&2
    return 1
  fi
  cp -a "$backup/runtime.env" "$runtime"
  cp -a "$backup/agent-console-web.service" "$unit"
  cp -a "$backup/agent-console-tailscale-tunnel.service" "$tunnel_unit"
  cp -a "$backup/runner.sh" "$runner"
  # Historical aliases are evidence only; never restore a schema10 writer path.
  "$state/venv/bin/python" -B "$checkout/scripts/install-entrypoints.py" \
    --home "$HOME" --state "$state" --releases "$state/releases"

  systemctl --user daemon-reload
  systemctl --user restart agent-console-web.service
  if ! wait_for_health 30; then
    printf 'WARNING: restored service did not become healthy within 30 seconds.\n' >&2
  fi
}

export AGENT_CONSOLE_SOURCE_ROOT="$checkout"
export AGENT_CONSOLE_PROFILE_DIR="$checkout/agent-profiles"

if ! "$checkout/scripts/install.sh"; then
  rollback
  printf 'FATAL: installer failed; previous unit/runtime/runner restored from %s.\n' "$backup" >&2
  exit 1
fi

if ! release_name="$(PYTHONPATH="$checkout" "$state/venv/bin/python" - \
  "$state/releases" "$checkout" "$requested_sha" "$database_path" "$config_dir" "$state" <<'PY'
import sys
import time
from pathlib import Path

from agent_console.deployer import Deployer

releases_root, source, candidate_sha, database, config, state = map(Path, sys.argv[1:])
deployer = Deployer(releases_root, object(), source_tracker="git", database_path=database,
                    config_dir=config, state_dir=state)
try:
    release = deployer.create_release(source, candidate_sha=str(candidate_sha))
except FileExistsError:
    time.sleep(1.1)
    release = deployer.create_release(source, candidate_sha=str(candidate_sha))
deployer.select_release(release["release_name"])
print(release["release_name"])
PY
)"; then
  rollback
  printf 'FATAL: exact-SHA release creation failed; previous release restored from %s.\n' "$backup" >&2
  exit 1
fi

if ! "$state/venv/bin/python" -B "$checkout/scripts/install-entrypoints.py" \
  --home "$HOME" --state "$state" --releases "$state/releases"; then
  rollback
  printf 'FATAL: selected-release entrypoints unavailable.\n' >&2
  exit 1
fi

if ! systemctl --user restart agent-console-web.service; then
  rollback
  printf 'FATAL: exact-SHA release restart failed; previous release restored from %s.\n' "$backup" >&2
  exit 1
fi

if ! wait_for_health 30; then
  rollback
  printf 'FATAL: updated service failed health check; previous unit/runtime/runner restored from %s.\n' "$backup" >&2
  exit 1
fi

"$state/venv/bin/python" -B "$checkout/scripts/snapshot-database.py" \
  "$database_path" "$backup/agent-console-after.sqlite3"
PYTHONPATH="$checkout" AGENT_CONSOLE_DB="$backup/agent-console-after.sqlite3" \
  "$state/venv/bin/python" -B -m agent_console.cli session list > "$backup/sessions-after.json"
if ! python3 - "$backup/sessions-before.json" "$backup/sessions-after.json" <<'PY'
import json
import sys

fields = ("id", "tmux_name", "created_at", "managed", "socket_scope", "launcher_path", "running")

def inventory(path):
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)
    return sorted(tuple(row.get(field) for field in fields) for row in rows)

if inventory(sys.argv[1]) != inventory(sys.argv[2]):
    raise SystemExit(1)
PY
then
  rollback
  printf 'FATAL: session inventory changed; previous unit/runtime/runner restored from %s.\n' "$backup" >&2
  exit 1
fi

printf 'Agent Console updated to %s as %s; rollback snapshot: %s\n' \
  "$requested_sha" "$release_name" "$backup"
