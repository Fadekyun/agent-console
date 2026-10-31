"""Bounded storage reclaim for Console state.

The launch preflight in :mod:`agent_console.storage` refuses to start a session
on an exhausted filesystem. This module is the other half: the periodic guard
that keeps the filesystem from getting there, and that runs the same reclaim
when an operator asks for it.

Two rules make it safe to run unattended:

* Nothing belonging to a live session is selected. A session counts as live when
  it is ``reserved``/``attached``/``detached`` in the database, or when anything
  under its state was touched inside the protection window.
* Age-based deletion of provider state is opt-in. ``tool-overlays/<session>`` and
  ``provider-state/<session>`` may hold the only copy of a session's native
  conversation history, so they are only reclaimed when ``tier2`` is enabled.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .storage import MIB, level_for

LIVE_STATUSES = ('reserved', 'attached', 'detached')
PROTECT_SECONDS = 3600
DAY = 86400

RELEASES_KEEP = 3            # current + canary + one rollback target
BACKUP_MAX_AGE_DAYS = 14
CONTEXT_MAX_AGE_DAYS = 7
LOG_MAX_AGE_DAYS = 30
OVERLAY_MAX_AGE_DAYS = 14    # tier 2
PROVIDER_MAX_AGE_DAYS = 7    # tier 2
TMP_MAX_AGE_DAYS = 2

# /tmp entries that are mounts, sockets or live tooling state, never reclaimed.
TMP_PROTECTED_PREFIXES = ('.', 'tmux-', 'systemd-private-')


@dataclass(frozen=True)
class Action:
    path: Path
    tier: int
    reason: str
    bytes: int

    def as_dict(self) -> dict[str, object]:
        return {'path': str(self.path), 'tier': self.tier,
                'reason': self.reason, 'bytes': self.bytes}


@dataclass
class Report:
    level: str
    free_bytes: int
    actions: list[Action] = field(default_factory=list)
    removed: int = 0
    freed_bytes: int = 0
    tier2_available_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            'level': self.level,
            'free_bytes': self.free_bytes,
            'free_gib': round(self.free_bytes / (1024 * MIB), 2),
            'planned': [action.as_dict() for action in self.actions],
            'planned_bytes': sum(action.bytes for action in self.actions),
            'removed': self.removed,
            'freed_bytes': self.freed_bytes,
            'tier2_available_bytes': self.tier2_available_bytes,
            'tier2_available_gib': round(self.tier2_available_bytes / (1024 * MIB), 2),
            'freed_gib': round(self.freed_bytes / (1024 * MIB), 2),
            'errors': self.errors,
        }


def size_of(path: Path) -> int:
    """Total apparent size; never follows symlinks."""
    if path.is_symlink():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
        # Directory symlinks are links, not storage; do not descend.
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
    return total


def live_sessions(db_path: str | Path) -> tuple[set[str], set[str]]:
    """Read live session names and ids; unavailable state is treated as none."""
    names: set[str] = set()
    ids: set[str] = set()
    path = Path(db_path)
    if not path.exists():
        return names, ids
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as conn:
            placeholders = ','.join('?' * len(LIVE_STATUSES))
            rows = conn.execute(
                f'SELECT id, tmux_name FROM sessions WHERE status IN ({placeholders})',
                LIVE_STATUSES,
            ).fetchall()
    except sqlite3.Error:
        return names, ids
    for session_id, name in rows:
        if session_id:
            ids.add(str(session_id))
        if name:
            names.add(str(name))
    return names, ids


def _protected(name: str, live_names: set[str]) -> bool:
    if name in live_names:
        return True
    # Contexts and overlays are keyed by session name, with a harness suffix.
    return any(name == live or name.startswith(live + '-') for live in live_names)


def _age_days(path: Path, now: float) -> float:
    try:
        return (now - path.stat().st_mtime) / DAY
    except OSError:
        return 0.0


def _stale(path: Path, max_age_days: float, now: float) -> bool:
    return _age_days(path, now) > max_age_days


def _entries(root: Path) -> list[Path]:
    """Deletable entries only: no symlinks, no other owners' files."""
    if not root.is_dir():
        return []
    try:
        owner = os.geteuid()
    except AttributeError:  # non-POSIX
        owner = None
    entries = []
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            continue
        if owner is not None:
            try:
                if child.stat().st_uid != owner:
                    continue
            except OSError:
                continue
        entries.append(child)
    return entries


def plan_releases(state_dir: Path, now: float, live: set[str]) -> list[Action]:
    root = state_dir / 'releases'
    current = {os.path.realpath(root / name) for name in ('current', 'canary')
               if (root / name).exists()}
    releases = [child for child in _entries(root) if child.is_dir()
                and child.name.startswith('release-')]
    releases.sort(key=lambda child: child.name, reverse=True)
    # Keep the newest releases plus whatever current/canary still point at.
    keep = {str(child) for child in releases[:RELEASES_KEEP]}
    keep |= {str(Path(path)) for path in current}
    actions = []
    for child in releases:
        if str(child) in keep or _protected(child.name, live):
            continue
        if not _stale(child, 1, now):
            continue
        actions.append(Action(child, 1, 'obsolete release', size_of(child)))
    return actions


def plan_update_backups(state_dir: Path, now: float) -> list[Action]:
    root = state_dir / 'update-backups'
    actions = []
    for child in _entries(root):
        if not child.is_dir() or not _stale(child, BACKUP_MAX_AGE_DAYS, now):
            continue
        actions.append(Action(child, 1, f'update backup older than {BACKUP_MAX_AGE_DAYS}d',
                              size_of(child)))
    return actions


def plan_logs(state_dir: Path, now: float) -> list[Action]:
    root = state_dir / 'logs'
    actions = []
    for child in _entries(root):
        if not child.is_file() or not _stale(child, LOG_MAX_AGE_DAYS, now):
            continue
        actions.append(Action(child, 1, f'log older than {LOG_MAX_AGE_DAYS}d', size_of(child)))
    return actions


def plan_contexts(state_dir: Path, now: float, live: set[str]) -> list[Action]:
    root = state_dir / 'contexts'
    actions = []
    for child in _entries(root):
        if not child.is_dir() or _protected(child.name, live):
            continue
        if not _stale(child, CONTEXT_MAX_AGE_DAYS, now):
            continue
        actions.append(Action(child, 1, 'session context for an inactive session', size_of(child)))
    return actions


def plan_provider_state(state_dir: Path, now: float, live_names: set[str],
                        live_ids: set[str]) -> list[Action]:
    """Tier 2: native provider history for sessions that are no longer live."""
    actions = []
    overlays = state_dir / 'tool-overlays'
    for child in _entries(overlays):
        if not child.is_dir() or _protected(child.name, live_names):
            continue
        if not _stale(child, OVERLAY_MAX_AGE_DAYS, now):
            continue
        actions.append(Action(child, 2, f'tool overlay older than {OVERLAY_MAX_AGE_DAYS}d',
                              size_of(child)))
    provider = state_dir / 'provider-state'
    for child in _entries(provider):
        if not child.is_dir() or child.name in live_ids:
            continue
        if not _stale(child, PROVIDER_MAX_AGE_DAYS, now):
            continue
        actions.append(Action(child, 2, 'provider state for an inactive session', size_of(child)))
    return actions


def plan_tmp(tmp_dir: Path, now: float, live: set[str]) -> list[Action]:
    actions = []
    for child in _entries(tmp_dir):
        if child.name.startswith(TMP_PROTECTED_PREFIXES) or _protected(child.name, live):
            continue
        if not _stale(child, TMP_MAX_AGE_DAYS, now):
            continue
        actions.append(Action(child, 1, f'temporary entry older than {TMP_MAX_AGE_DAYS}d',
                              size_of(child)))
    return actions


def plan(state_dir: Path, *, now: float | None = None, live_names: set[str] | None = None,
         live_ids: set[str] | None = None, tmp_dir: Path | None = None,
         tier2: bool = False) -> list[Action]:
    now = time.time() if now is None else now
    live_names = set(live_names or ())
    live_ids = set(live_ids or ())
    actions = [
        *plan_releases(state_dir, now, live_names),
        *plan_update_backups(state_dir, now),
        *plan_logs(state_dir, now),
        *plan_contexts(state_dir, now, live_names),
    ]
    if tmp_dir is not None and tmp_dir.is_dir():
        actions.extend(plan_tmp(tmp_dir, now, live_names))
    if tier2:
        actions.extend(plan_provider_state(state_dir, now, live_names, live_ids))
    return actions


def apply(actions: list[Action], report: Report) -> Report:
    for action in actions:
        try:
            if action.path.is_dir() and not action.path.is_symlink():
                shutil.rmtree(action.path)
            else:
                action.path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            report.errors.append(f'{action.path}: {exc}')
            continue
        report.removed += 1
        report.freed_bytes += action.bytes
    return report


def run(state_dir: str | Path, *, db_path: str | Path | None = None,
        tmp_dir: str | Path | None = '/tmp', tier2: bool | None = None,
        now: float | None = None, apply_actions: bool = True) -> Report:
    """Plan (and optionally apply) reclaim for the current free-space level."""
    from .storage import free_bytes

    root = Path(state_dir)
    now = time.time() if now is None else now
    if tier2 is None:
        tier2 = os.getenv('AGENT_CONSOLE_STORAGE_GUARD_TIER2') == '1'
    free = free_bytes(root)
    report = Report(level=level_for(free), free_bytes=free)
    if report.level == 'ok':
        return report
    names, ids = live_sessions(db_path or (root / 'agent-console.sqlite3'))
    report.actions = plan(root, now=now, live_names=names, live_ids=ids,
                          tmp_dir=Path(tmp_dir) if tmp_dir else None, tier2=tier2)
    if not tier2:
        # Show an operator what the opt-in tier would add, without touching it.
        report.tier2_available_bytes = sum(
            action.bytes for action in plan_provider_state(root, now, names, ids))
    if apply_actions:
        apply(report.actions, report)
        report.free_bytes = free_bytes(root)
        report.level = level_for(report.free_bytes)
    return report


def write_status(state_dir: str | Path, report: Report) -> Path:
    """Persist the last guard result atomically for the UI and handoffs."""
    target = Path(state_dir) / 'storage-status.json'
    temporary = target.with_name(target.name + '.tmp')
    payload = json.dumps({**report.as_dict(), 'checked_at': int(time.time())}, indent=1)
    temporary.write_text(payload, encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target
