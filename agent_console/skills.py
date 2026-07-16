from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_retained_env = os.getenv("AGCONSOLE_RETAINED_SKILLS", "")
RETAINED_SKILLS = tuple(
    name.strip() for name in _retained_env.split(",") if name.strip()
) if _retained_env else ()


def _replace_link(path: Path, target: Path) -> None:
    if path.is_symlink():
        if path.resolve() == target.resolve():
            return
        path.unlink()
    elif path.exists():
        raise FileExistsError(f"refusing to replace non-symlink skill path: {path}")
    path.symlink_to(target, target_is_directory=True)


def _prepare_root(path: Path, canonical_root: Path) -> None:
    if path.is_symlink():
        if path.resolve() != canonical_root.resolve():
            raise ValueError(f"unexpected skill-root symlink: {path}")
        path.unlink()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def sync_skills(
    *,
    canonical_root: Path = Path(os.getenv("AGCONSOLE_SKILLS_ROOT", str(Path.home() / "codex" / "skills"))),
    home: Path | None = None,
) -> dict[str, Any]:
    home = home or Path.home()
    missing = [name for name in RETAINED_SKILLS if not (canonical_root / name / "SKILL.md").is_file()]
    if missing:
        raise FileNotFoundError(f"canonical skills missing: {', '.join(missing)}")

    roots = {
        "codex": home / ".codex" / "skills",
        "claude": home / ".claude" / "skills",
        "hermes": home / ".hermes" / "skills" / "homelab",
    }
    for root in roots.values():
        _prepare_root(root, canonical_root)

    for tool, root in roots.items():
        for name in RETAINED_SKILLS:
            _replace_link(root / name, canonical_root / name)
        for path in root.iterdir():
            if (
                path.is_symlink()
                and canonical_root.resolve() in path.resolve().parents
                and path.name not in RETAINED_SKILLS
            ):
                path.unlink()

    return {
        "ok": True,
        "canonical_root": str(canonical_root),
        "skills": len(RETAINED_SKILLS),
        "roots": {name: str(path) for name, path in roots.items()},
        "opencode_discovery": str(roots["claude"]),
    }


def doctor_skills(
    *,
    canonical_root: Path = Path(os.getenv("AGCONSOLE_SKILLS_ROOT", str(Path.home() / "codex" / "skills"))),
    home: Path | None = None,
) -> dict[str, Any]:
    home = home or Path.home()
    roots = {
        "codex": home / ".codex" / "skills",
        "claude": home / ".claude" / "skills",
        "hermes": home / ".hermes" / "skills" / "homelab",
    }
    problems: list[str] = []
    for name in RETAINED_SKILLS:
        source = canonical_root / name / "SKILL.md"
        if not source.is_file():
            problems.append(f"missing canonical skill: {name}")
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            problems.append(f"missing frontmatter: {name}")
        for tool, root in roots.items():
            link = root / name
            if not link.is_symlink() or not (link / "SKILL.md").is_file():
                problems.append(f"{tool} link missing: {name}")
            elif link.resolve() != (canonical_root / name).resolve():
                problems.append(f"{tool} link has wrong target: {name}")
    return {
        "ok": not problems,
        "skills": len(RETAINED_SKILLS),
        "problems": problems,
    }
