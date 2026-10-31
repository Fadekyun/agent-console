"""Storage headroom policy for session launches.

A launch writes a context file, launcher, environment snapshot, tool overlay and
tmux state before the first prompt is typed. When the state filesystem is
exhausted those writes fail one by one, which leaves a half-created session and
surfaces later as unrelated ``disk I/O error`` failures inside the terminal.

The check below runs once, before any of those writes. An exhausted filesystem
therefore fails as a single refusal that names the shortfall, and the operator
sees storage pressure instead of a cryptic mid-session failure.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

MIB = 1024 * 1024

# Policy levels, in MiB of free space on the state filesystem. Ordered from
# healthy to exhausted; the first threshold a value falls below wins.
LEVEL_THRESHOLDS_MIB: tuple[tuple[str, int], ...] = (
    ('ok', 10 * 1024),
    ('warn', 4 * 1024),
    ('critical', 1024),
    ('blocked', 0),
)

# A launch is refused outright below this reserve: 2 GiB is roughly the largest
# single tool overlay plus its worktree and logs.
DEFAULT_MIN_FREE_MIB = 2048


class StoragePressure(RuntimeError):
    """Raised when the state filesystem cannot host another session.

    Subclasses RuntimeError so existing operation-error handling and CLI
    reporting keep working; ``status`` is honoured by the web error mapper.
    """

    status = 503


def minimum_free_mib(raw: str | None = None) -> int:
    """Parse the configured launch reserve, ignoring unusable values."""
    value = os.getenv('AGENT_CONSOLE_MIN_FREE_MIB') if raw is None else raw
    if not value:
        return DEFAULT_MIN_FREE_MIB
    try:
        parsed = int(value.strip())
    except (AttributeError, ValueError):
        return DEFAULT_MIN_FREE_MIB
    return parsed if parsed > 0 else DEFAULT_MIN_FREE_MIB


def level_for(free_bytes: int) -> str:
    """Map free bytes onto ok/warn/critical/blocked."""
    free_mib = free_bytes / MIB
    for name, threshold in LEVEL_THRESHOLDS_MIB:
        if free_mib >= threshold:
            return name
    return 'blocked'


def free_bytes(path: str | Path) -> int:
    return shutil.disk_usage(str(path)).free


def status(path: str | Path, *, minimum: int | None = None) -> dict[str, object]:
    """Report headroom without mutating anything."""
    reserve_mib = minimum_free_mib() if minimum is None else minimum
    free = free_bytes(path)
    return {
        'path': str(path),
        'free_bytes': free,
        'free_gib': round(free / (1024 * MIB), 2),
        'level': level_for(free),
        'minimum_free_bytes': reserve_mib * MIB,
        'minimum_free_gib': round(reserve_mib / 1024, 2),
        'headroom_ok': free >= reserve_mib * MIB,
    }


def require_headroom(path: str | Path, *, minimum: int | None = None) -> dict[str, object]:
    """Return the headroom report, or raise StoragePressure naming the shortfall."""
    report = status(path, minimum=minimum)
    if report['headroom_ok']:
        return report
    raise StoragePressure(
        f"Not enough free space for a new session: {report['free_gib']} GiB free at "
        f"{report['path']}, {report['minimum_free_gib']} GiB required. Free space or run "
        "the storage guard, then retry. No session state was created."
    )
