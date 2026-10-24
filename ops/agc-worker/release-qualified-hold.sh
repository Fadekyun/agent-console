#!/bin/bash
# Operator-only release; central owner hold and dynamic gates stay independent.
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
exec /usr/bin/python3 -I /usr/local/lib/agc-worker/qualification_gate.py "$@"
