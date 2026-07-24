from __future__ import annotations

import re
from pathlib import Path


SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PLAN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TOOLS = frozenset({"codex", "claude", "opencode", "hermes", "shell"})
AGENT_MODES = frozenset({"plan", "build"})
PROFILES = frozenset(
    {
        "general",
        "coder",
        "planner",
        "scout",
        "reviewer",
        "verifier",
        "bugfix",
        "researcher",
        "release",
        "orchestrator",
    }
)


def validate_session_name(value: str) -> str:
    if not SESSION_RE.fullmatch(value):
        raise ValueError(
            "session name must be 1-80 characters using letters, digits, dots, underscores, or hyphens"
        )
    return value


def validate_plan_id(value: str) -> str:
    if not PLAN_RE.fullmatch(value):
        raise ValueError("invalid plan ID")
    return value


def validate_tool(value: str) -> str:
    if value not in TOOLS:
        raise ValueError(f"unsupported tool: {value}")
    return value


def validate_profile(value: str) -> str:
    if value not in PROFILES:
        raise ValueError(f"unsupported profile: {value}")
    return value


def contained_path(candidate: Path, root: Path, *, must_exist: bool = True) -> Path:
    resolved_root = root.expanduser().resolve(strict=True)
    resolved = candidate.expanduser().resolve(strict=must_exist)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path must remain beneath {resolved_root}: {candidate}") from exc
    return resolved
