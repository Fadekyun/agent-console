"""Bounded storage reclaim for Console state.

The launch preflight in :mod:`agent_console.storage` refuses to start a session
on an exhausted filesystem. This module is the other half: the periodic guard
that keeps the filesystem from getting there, and that runs the same retention
when an operator asks for it.

Safety rules, in order of importance:

* **Fail closed.** If live session state cannot be read, nothing that depends on
  it is touched. An unreadable database must never look like "no sessions".
* **Activity, not directory age.** A candidate is only eligible when its newest
  file (not its top-directory mtime) is older than the rule's threshold *and*
  older than :data:`PROTECT_SECONDS`, so a tree that was written to a minute ago
  is never selected because its directory timestamp looks old.
* **Containment.** Every candidate must resolve inside its own root, with no
  symlinked component, and must be owned by the guard's user.
* **Pointers are rechecked at delete time.** Release candidates are re-verified
  against ``current``/``canary`` immediately before removal, so a deploy that
  flips pointers between planning and applying cannot lose a live release.
* **Serialised.** Applying takes an exclusive lock, so a cron run and an
  operator run cannot act on the same plan concurrently.
* **History is opt-in and pressure-gated.** ``tool-overlays/<session>`` and
  ``provider-state/<session>`` may hold the only copy of a session's native
  conversation history; they are reclaimed only when tier 2 is enabled *and* the
  filesystem is at ``warn`` or worse.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .storage import MIB, level_for

LIVE_STATUSES = ('reserved', 'attached', 'detached')
PROTECT_SECONDS = 3600
DAY = 86400

RELEASES_KEEP = 3            # current + canary + one rollback target
ASSESSMENT_KEEP = 2          # newest harness assessments, plus whatever latest.json names
BACKUP_MAX_AGE_DAYS = 14
CONTEXT_MAX_AGE_DAYS = 7
LOG_MAX_AGE_DAYS = 30
OVERLAY_MAX_AGE_DAYS = 14    # tier 2
PROVIDER_MAX_AGE_DAYS = 7    # tier 2
TMP_MAX_AGE_DAYS = 2

# Only names the Console tooling itself creates are eligible under /tmp. Anything
# else there belongs to another job or is a mount/socket we must not touch.
TMP_MANAGED_PREFIXES = (
    'agc-', 'offkai-', 'ac-', 'pr1', 'pr2', 'pi-', 'codex-', 'console-',
    'hermes-', 'pnpmhome', 'node-compile-cache', 'ops-',
)


class LiveStateUnavailable(RuntimeError):
    """Raised when the authoritative session database cannot be read."""


@dataclass(frozen=True)
class LiveState:
    names: set[str]
    ids: set[str]


@dataclass(frozen=True)
class Action:
    path: Path
    tier: int
    reason: str
    bytes: int
    kind: str = 'item'

    def as_dict(self) -> dict[str, object]:
        return {'path': str(self.path), 'tier': self.tier, 'kind': self.kind,
                'reason': self.reason, 'bytes': self.bytes}


@dataclass
class Report:
    level: str
    free_bytes: int
    actions: list[Action] = field(default_factory=list)
    removed: int = 0
    freed_bytes: int = 0
    tier2_available_bytes: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            'level': self.level,
            'free_bytes': self.free_bytes,
            'free_gib': round(self.free_bytes / (1024 * MIB), 2),
            'planned': [action.as_dict() for action in self.actions],
            'planned_bytes': sum(action.bytes for action in self.actions),
            'planned_gib': round(sum(action.bytes for action in self.actions) / (1024 * MIB), 2),
            'removed': self.removed,
            'freed_bytes': self.freed_bytes,
            'freed_gib': round(self.freed_bytes / (1024 * MIB), 2),
            'tier2_available_bytes': self.tier2_available_bytes,
            'tier2_available_gib': round(self.tier2_available_bytes / (1024 * MIB), 2),
            'skipped': self.skipped,
            'errors': self.errors,
        }


def measure(path: Path) -> tuple[int, float]:
    """Return (apparent bytes, newest modification time) without following links."""
    if path.is_symlink():
        return 0, 0.0
    newest = 0.0
    try:
        newest = path.lstat().st_mtime
    except OSError:
        return 0, 0.0
    if path.is_file():
        return path.lstat().st_size, newest
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _: None):
        for name in files:
            try:
                stat = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += stat.st_size
            newest = max(newest, stat.st_mtime)
        # Directory symlinks are links, not storage; do not descend.
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
    return total, newest


def size_of(path: Path) -> int:
    """Total apparent size; never follows symlinks."""
    return measure(path)[0]


def live_sessions(db_path: str | Path) -> LiveState:
    """Return the authoritative live session identities.

    Raises :class:`LiveStateUnavailable` when the database exists but cannot be
    read: treating that as "nothing is live" would authorise deletion during a
    database lock or corruption, which is exactly when the disk is also in trouble.
    """
    path = Path(db_path)
    if not path.exists():
        return LiveState(set(), set())
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as conn:
            placeholders = ','.join('?' * len(LIVE_STATUSES))
            rows = conn.execute(
                f'SELECT id, tmux_name FROM sessions WHERE status IN ({placeholders})',
                LIVE_STATUSES,
            ).fetchall()
    except sqlite3.Error as exc:
        raise LiveStateUnavailable(f'session state unreadable: {exc}') from None
    names: set[str] = set()
    ids: set[str] = set()
    for session_id, name in rows:
        if session_id:
            ids.add(str(session_id))
        if name:
            names.add(str(name))
    return LiveState(names, ids)


def _protected(name: str, live_names: set[str]) -> bool:
    if name in live_names:
        return True
    # Contexts and overlays are keyed by session name, with a harness suffix.
    return any(name == live or name.startswith(live + '-') for live in live_names)


def _contained(path: Path, root: Path) -> bool:
    """True when ``path`` resolves strictly inside ``root`` with no symlink hop."""
    try:
        real = Path(os.path.realpath(path))
        real_root = Path(os.path.realpath(root))
    except OSError:
        return False
    if real == real_root:
        return False
    try:
        real.relative_to(real_root)
    except ValueError:
        return False
    return not path.is_symlink()


def _eligible(path: Path, max_age_days: float, now: float,
              root: Path) -> tuple[bool, int, float]:
    """Return (deletable, bytes, newest_age_days) honouring containment and activity."""
    if not _contained(path, root):
        return False, 0, 0.0
    size, newest = measure(path)
    age_days = (now - newest) / DAY if newest else float('inf')
    recent = newest and (now - newest) < PROTECT_SECONDS
    return (age_days > max_age_days and not recent), size, age_days


def _entries(root: Path) -> list[Path]:
    """Candidate entries: owned by us, not symlinks, contained in ``root``."""
    if not root.is_dir() or root.is_symlink():
        return []
    try:
        owner = os.geteuid()
    except AttributeError:  # non-POSIX
        owner = None
    entries = []
    for child in sorted(root.iterdir()):
        if child.is_symlink() or not _contained(child, root):
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
        ok, size, _ = _eligible(child, 1, now, root)
        if not ok:
            continue
        actions.append(Action(child, 1, 'obsolete release', size, 'release'))
    return actions


def plan_update_backups(state_dir: Path, now: float) -> list[Action]:
    root = state_dir / 'update-backups'
    actions = []
    for child in _entries(root):
        if not child.is_dir():
            continue
        ok, size, _ = _eligible(child, BACKUP_MAX_AGE_DAYS, now, root)
        if not ok:
            continue
        actions.append(Action(child, 1, f'update backup older than {BACKUP_MAX_AGE_DAYS}d',
                              size, 'backup'))
    return actions


def plan_logs(state_dir: Path, now: float) -> list[Action]:
    root = state_dir / 'logs'
    actions = []
    for child in _entries(root):
        if not child.is_file():
            continue
        ok, size, _ = _eligible(child, LOG_MAX_AGE_DAYS, now, root)
        if not ok:
            continue
        actions.append(Action(child, 1, f'log older than {LOG_MAX_AGE_DAYS}d', size, 'log'))
    return actions


def _assessment_keep(root: Path, snapshots: list[Path]) -> set[str]:
    """Snapshots to keep: the newest few plus whatever latest.json still names."""
    keep = {str(child) for child in snapshots[-ASSESSMENT_KEEP:]}
    latest = root / 'latest.json'
    if latest.is_symlink() or not latest.is_file():
        return keep
    try:
        payload = json.loads(latest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        # An unreadable reference must not silently drop the protection it grants.
        return {str(child) for child in snapshots}
    if not isinstance(payload, dict):
        return {str(child) for child in snapshots}
    for key in ('snapshot', 'path', 'directory', 'name'):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        keep.add(str(Path(os.path.realpath(candidate))))
    return keep


def plan_assessments(state_dir: Path, now: float) -> list[Action]:
    """Superseded harness-assessment snapshots.

    Each run writes a complete snapshot (hundreds of MiB) and only the newest is
    referenced by ``latest.json``. They are regenerable, so this is tier 1.
    """
    root = state_dir / 'harness-assessments'
    snapshots = [child for child in _entries(root) if child.is_dir()]
    snapshots.sort(key=lambda child: child.name)
    keep = _assessment_keep(root, snapshots)
    actions = []
    for child in snapshots:
        if str(Path(os.path.realpath(child))) in keep:
            continue
        ok, size, _ = _eligible(child, 0.25, now, root)
        if not ok:
            continue
        actions.append(Action(child, 1, 'superseded harness assessment', size, 'assessment'))
    return actions


def plan_contexts(state_dir: Path, now: float, live: set[str]) -> list[Action]:
    """Session context mirrors: the heavy per-session directories only.

    ``contexts/<name>.md`` instruction files are deliberately kept. They are a few
    kilobytes each (bounded by the session count, not by age), and an operator may
    have edited one; the disk cost of keeping them is irrelevant next to that risk.
    """
    root = state_dir / 'contexts'
    actions = []
    for child in _entries(root):
        name = child.name
        if not child.is_dir():
            continue
        if _protected(name, live):
            continue
        ok, size, _ = _eligible(child, CONTEXT_MAX_AGE_DAYS, now, root)
        if not ok:
            continue
        actions.append(Action(child, 1, 'session context for an inactive session',
                              size, 'context'))
    return actions


def plan_provider_state(state_dir: Path, now: float, live_names: set[str],
                        live_ids: set[str]) -> list[Action]:
    """Tier 2: native provider history for sessions that are no longer live."""
    actions = []
    overlays = state_dir / 'tool-overlays'
    for child in _entries(overlays):
        if not child.is_dir() or _protected(child.name, live_names):
            continue
        ok, size, _ = _eligible(child, OVERLAY_MAX_AGE_DAYS, now, overlays)
        if not ok:
            continue
        actions.append(Action(child, 2, f'tool overlay older than {OVERLAY_MAX_AGE_DAYS}d',
                              size, 'overlay'))
    provider = state_dir / 'provider-state'
    for child in _entries(provider):
        if not child.is_dir() or child.name in live_ids:
            continue
        ok, size, _ = _eligible(child, PROVIDER_MAX_AGE_DAYS, now, provider)
        if not ok:
            continue
        actions.append(Action(child, 2, 'provider state for an inactive session',
                              size, 'provider'))
    return actions


def plan_tmp(tmp_dir: Path, now: float, live: set[str]) -> list[Action]:
    """Managed temporary trees only; unknown names are never reclaimed."""
    root = Path(tmp_dir)
    actions = []
    for child in _entries(root):
        if not child.name.startswith(TMP_MANAGED_PREFIXES):
            continue
        if _protected(child.name, live):
            continue
        ok, size, _ = _eligible(child, TMP_MAX_AGE_DAYS, now, root)
        if not ok:
            continue
        actions.append(Action(child, 1, f'managed temporary entry older than {TMP_MAX_AGE_DAYS}d',
                              size, 'tmp'))
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
        *plan_assessments(state_dir, now),
    ]
    if tmp_dir is not None and Path(tmp_dir).is_dir():
        actions.extend(plan_tmp(Path(tmp_dir), now, live_names))
    if tier2:
        actions.extend(plan_provider_state(state_dir, now, live_names, live_ids))
    return actions


def _pointers_changed(action: Action, state_dir: Path) -> bool:
    """True when a release candidate became a live pointer after planning."""
    root = state_dir / 'releases'
    targets = {os.path.realpath(root / name) for name in ('current', 'canary')
               if (root / name).exists()}
    return os.path.realpath(action.path) in targets


def apply(actions: list[Action], report: Report, *, state_dir: Path | None = None) -> Report:
    for action in actions:
        root = state_dir or action.path.parent
        if not _contained(action.path, root):
            report.skipped.append(f'{action.path}: outside {root}')
            continue
        if action.kind == 'release' and state_dir is not None and _pointers_changed(action, state_dir):
            report.skipped.append(f'{action.path}: became current/canary during apply')
            continue
        try:
            if action.path.is_dir():
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


def _lock(state_dir: Path):
    path = state_dir / 'storage-guard.lock'
    handle = open(path, 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def run(state_dir: str | Path, *, db_path: str | Path | None = None,
        tmp_dir: str | Path | None = '/tmp', tier2: bool | None = None,
        now: float | None = None, apply_actions: bool = True) -> Report:
    """Plan (and optionally apply) retention plus pressure reclaim."""
    from .storage import free_bytes

    root = Path(state_dir)
    now = time.time() if now is None else now
    if tier2 is None:
        tier2 = os.getenv('AGENT_CONSOLE_STORAGE_GUARD_TIER2') == '1'
    free = free_bytes(root)
    report = Report(level=level_for(free), free_bytes=free)

    lock = _lock(root) if apply_actions else None
    if apply_actions and lock is None:
        report.errors.append('another storage-guard run holds the lock')
        return report
    try:
        try:
            live = live_sessions(db_path or (root / 'agent-console.sqlite3'))
        except LiveStateUnavailable as exc:
            # Fail closed: no session-dependent deletion while state is unreadable.
            report.errors.append(f'{exc}; session-dependent cleanup skipped')
            report.actions = [*plan_releases(root, now, set()),
                              *plan_update_backups(root, now),
                              *plan_logs(root, now),
                              *plan_assessments(root, now)]
            if apply_actions:
                apply(report.actions, report, state_dir=root)
            return report

        # Tier 2 removes native history, so it stays behind both the opt-in and
        # actual pressure; a healthy disk never triggers it.
        tier2_active = bool(tier2) and report.level != 'ok'
        report.actions = plan(root, now=now, live_names=live.names, live_ids=live.ids,
                              tmp_dir=Path(tmp_dir) if tmp_dir else None, tier2=tier2_active)
        if not tier2:
            report.tier2_available_bytes = sum(
                action.bytes for action in plan_provider_state(root, now, live.names, live.ids))
        if apply_actions:
            apply(report.actions, report, state_dir=root)
            report.free_bytes = free_bytes(root)
            report.level = level_for(report.free_bytes)
        return report
    finally:
        if lock is not None:
            lock.close()


def write_status(state_dir: str | Path, report: Report) -> Path:
    """Persist the last guard result atomically for the UI and handoffs."""
    root = Path(state_dir)
    target = root / 'storage-status.json'
    payload = json.dumps({**report.as_dict(), 'checked_at': int(time.time())}, indent=1)
    handle, temporary = tempfile.mkstemp(prefix='.storage-status-', dir=root)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target
