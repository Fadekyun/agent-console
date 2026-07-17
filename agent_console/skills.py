from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .validation import PROFILES

SUPPORTED_TOOLS = frozenset({"codex", "claude", "hermes"})
SKILL_KINDS = frozenset({"standard", "superpower"})


def _resolve_canonical_root() -> Path:
    return Path(os.getenv("AGCONSOLE_SKILLS_ROOT", str(Path.home() / "codex" / "skills")))


def _load_retained_env() -> tuple[str, ...]:
    raw = os.getenv("AGCONSOLE_RETAINED_SKILLS", "")
    if not raw:
        return ()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


RETAINED_SKILLS: tuple[str, ...] = _load_retained_env()

SKILL_CATALOG: list[dict[str, Any]] = [
    {
        "name": name,
        "description": "",
        "tools": sorted(SUPPORTED_TOOLS),
        "kind": "standard",
        "source_path": str(_resolve_canonical_root() / name),
        "allowed_profiles": None,
        "requires_approval": False,
    }
    for name in RETAINED_SKILLS
]


def _parse_frontmatter(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        key, _, value = line.partition(":")
                        result[key.strip()] = value.strip()
    except OSError:
        pass
    return result


def validate_catalog(
    canonical_root: Path | None = None,
) -> list[str]:
    root = canonical_root or _resolve_canonical_root()
    errors: list[str] = []
    for entry in SKILL_CATALOG:
        name = entry["name"]
        kind = entry["kind"]
        if kind not in SKILL_KINDS:
            errors.append(f"skill {name!r}: unknown kind {kind!r}")
            continue
        source = root / name / "SKILL.md"
        if not source.is_file():
            errors.append(f"skill {name!r}: missing SKILL.md at {source}")
        for tool in entry["tools"]:
            if tool not in SUPPORTED_TOOLS:
                errors.append(f"skill {name!r}: unsupported tool {tool!r}")
        allowed = entry.get("allowed_profiles")
        if allowed is not None:
            for profile in allowed:
                if profile not in PROFILES:
                    errors.append(f"skill {name!r}: unknown profile {profile!r}")
    return errors


def skill_catalog(
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    errors = validate_catalog(root)
    entries = []
    for entry in SKILL_CATALOG:
        name = entry["name"]
        source = root / name / "SKILL.md"
        frontmatter = _parse_frontmatter(source) if source.is_file() else {}
        description = frontmatter.get("description", entry["description"])
        synced = []
        for tool in entry["tools"]:
            link = _tool_root(tool) / name
            synced.append({
                "tool": tool,
                "linked": link.is_symlink() and (link / "SKILL.md").is_file(),
            })
        entries.append({
            "name": name,
            "description": description,
            "kind": entry["kind"],
            "tools": entry["tools"],
            "allowed_profiles": entry.get("allowed_profiles"),
            "requires_approval": entry.get("requires_approval", False),
            "source_present": source.is_file(),
            "synced": synced,
        })
    return {"entries": entries, "errors": errors}


def _tool_root(tool: str, home: Path | None = None) -> Path:
    h = home or Path.home()
    roots = {
        "codex": h / ".codex" / "skills",
        "claude": h / ".claude" / "skills",
        "hermes": h / ".hermes" / "skills" / "homelab",
    }
    return roots[tool]


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
    canonical_root: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    h = home or Path.home()
    names = [entry["name"] for entry in SKILL_CATALOG]
    missing = [name for name in names if not (root / name / "SKILL.md").is_file()]
    if missing:
        raise FileNotFoundError(f"canonical skills missing: {', '.join(missing)}")

    tool_set = {tool for entry in SKILL_CATALOG for tool in entry["tools"]}
    roots = {tool: _tool_root(tool, h) for tool in tool_set}
    for r in roots.values():
        _prepare_root(r, root)

    for tool, r in roots.items():
        catalog_names = {e["name"] for e in SKILL_CATALOG if tool in e["tools"]}
        for name in catalog_names:
            _replace_link(r / name, root / name)
        for path in r.iterdir():
            if (
                path.is_symlink()
                and root.resolve() in path.resolve().parents
                and path.name not in catalog_names
            ):
                path.unlink()

    return {
        "ok": True,
        "canonical_root": str(root),
        "skills": len(SKILL_CATALOG),
        "roots": {name: str(p) for name, p in roots.items()},
    }


def doctor_skills(
    *,
    canonical_root: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    h = home or Path.home()
    problems: list[str] = []
    for entry in SKILL_CATALOG:
        name = entry["name"]
        source = root / name / "SKILL.md"
        if not source.is_file():
            problems.append(f"missing canonical skill: {name}")
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            problems.append(f"missing frontmatter: {name}")
        for tool in entry["tools"]:
            link = _tool_root(tool, h) / name
            if not link.is_symlink() or not (link / "SKILL.md").is_file():
                problems.append(f"{tool} link missing: {name}")
            elif link.resolve() != (root / name).resolve():
                problems.append(f"{tool} link has wrong target: {name}")
    return {
        "ok": not problems,
        "skills": len(SKILL_CATALOG),
        "problems": problems,
    }


def check_superpower_approval(
    profile: str,
    skill_name: str,
    *,
    approved: bool = False,
) -> dict[str, Any]:
    entry = next((e for e in SKILL_CATALOG if e["name"] == skill_name), None)
    if entry is None:
        return {"allowed": False, "reason": f"unknown skill: {skill_name}", "enforcement": "enforced"}
    if entry["kind"] != "superpower":
        return {"allowed": True, "reason": None, "enforcement": "enforced"}
    allowed = entry.get("allowed_profiles")
    if allowed is not None and profile not in allowed:
        return {
            "allowed": False,
            "reason": f"profile {profile!r} is not allowed to use superpower {skill_name!r}",
            "enforcement": "enforced",
        }
    if entry.get("requires_approval", False) and not approved:
        return {
            "allowed": False,
            "reason": f"superpower {skill_name!r} requires explicit human approval for profile {profile!r}",
            "enforcement": "pending_approval",
        }
    return {"allowed": True, "reason": None, "enforcement": "enforced"}
