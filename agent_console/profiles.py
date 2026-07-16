from __future__ import annotations

from pathlib import Path

from .validation import PROFILES, validate_profile


READ_ONLY_PROFILES = frozenset({"planner", "scout", "reviewer", "researcher"})
WRITE_PROFILES = frozenset({"coder", "bugfix"})


def profile_text(profile_dir: Path, profile: str) -> str:
    validate_profile(profile)
    path = profile_dir / f"{profile}.md"
    if not path.is_file():
        raise FileNotFoundError(f"profile is not installed: {path}")
    return path.read_text(encoding="utf-8")


def installed_profiles(profile_dir: Path) -> list[dict[str, object]]:
    result = []
    for name in sorted(PROFILES):
        path = profile_dir / f"{name}.md"
        result.append(
            {
                "name": name,
                "installed": path.is_file(),
                "read_only": name in READ_ONLY_PROFILES,
                "prefers_worktree": name in WRITE_PROFILES,
                "path": str(path),
            }
        )
    return result
