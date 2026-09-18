"""Read-only view of the Jev ghost probe history.

The ghost probe is a bounded, low-cost synthetic check that keeps the shared
Jev (TypeSafe) credential and typed-answer path under continuous test. This
module only *reads* its artifacts; it never calls the API and never exposes a
credential value.

Artifacts live in a state directory (default ``<state_dir>/jev-ghost``,
override with ``AGENT_CONSOLE_JEV_GHOST_DIR``):

- ``runs.jsonl``: one sanitized run record per line
- ``latest.json``: the most recent run
- ``summary.json``: rolling aggregate

Every read is defensive: a missing or malformed artifact degrades to an empty
result rather than raising, so the review view can always render.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import Depends

MAX_RUNS = 200
_RUN_FIELDS = ("at", "cycle", "scenario", "status", "http_status", "model",
               "checks_passed", "checks_total", "failed_checks", "duration_ms",
               "error", "usage", "typed", "skill_paths", "key_present")


def default_dir() -> Path:
    """Resolve the probe state directory (absolute, environment-overridable)."""
    raw = os.environ.get("AGENT_CONSOLE_JEV_GHOST_DIR")
    if raw:
        return Path(raw).expanduser()
    state = os.environ.get("AGENT_CONSOLE_STATE_DIR")
    base = Path(state).expanduser() if state else Path.home() / ".local" / "share" / "agent-console"
    return base / "jev-ghost"


def _read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _tail_lines(path: Path, limit: int) -> list[str]:
    """Read at most the last ``limit`` lines without loading the whole file."""
    if limit <= 0 or not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            block = 65536
            data = b""
            while end > 0 and data.count(b"\n") <= limit:
                step = min(block, end)
                end -= step
                fh.seek(end)
                data = fh.read(step) + data
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-limit:]


def _clean_run(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {key: raw[key] for key in _RUN_FIELDS if key in raw}


def load_runs(directory: Path, limit: int = MAX_RUNS) -> list[dict[str, Any]]:
    """Return probe runs, newest first, from ``directory/runs.jsonl``."""
    runs: list[dict[str, Any]] = []
    for line in _tail_lines(directory / "runs.jsonl", limit):
        line = line.strip()
        if not line:
            continue
        try:
            cleaned = _clean_run(json.loads(line))
        except ValueError:
            continue
        if cleaned is not None:
            runs.append(cleaned)
    runs.reverse()
    return runs


def load_summary(directory: Path) -> dict[str, Any] | None:
    summary = _read_json(directory / "summary.json")
    return summary if isinstance(summary, dict) else None


def _shared_skills() -> list[str]:
    raw = os.environ.get("AGCONSOLE_SHARED_SKILLS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def snapshot(directory: Path | None = None, limit: int = MAX_RUNS) -> dict[str, Any]:
    """Build the read-only payload for the review view."""
    directory = Path(directory) if directory is not None else default_dir()
    limit = max(1, min(int(limit), MAX_RUNS))
    runs = load_runs(directory, limit)
    summary = load_summary(directory)
    available = (directory / "runs.jsonl").is_file()
    latest = runs[0] if runs else _read_json(directory / "latest.json")
    if not isinstance(latest, dict):
        latest = None
    return {
        "available": available,
        "state_dir": str(directory),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shared_skills": _shared_skills(),
        "summary": summary,
        "latest": latest,
        "runs": runs,
    }


def install(app, require_identity, *, directory: Path | None = None) -> None:
    """Register the read-only ``GET /api/jev-ghost`` route."""
    resolved = Path(directory) if directory is not None else default_dir()

    @app.get("/api/jev-ghost")
    async def jev_ghost(limit: int = 50, _=Depends(require_identity)) -> dict[str, Any]:
        return snapshot(resolved, limit=limit)
