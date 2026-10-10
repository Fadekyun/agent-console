#!/bin/sh
set -eu

profile_source=/app/agent-profiles
profile_target="${AGENT_CONSOLE_PROFILE_DIR:-/workspace/agent-profiles}"

mkdir -p "$profile_target"

if [ -d "$profile_source" ]; then
    for source in "$profile_source"/*.md; do
        [ -f "$source" ] || continue
        target="$profile_target/$(basename "$source")"
        if [ ! -e "$target" ]; then
            cp "$source" "$target"
        fi
    done
fi

exec "$@"
