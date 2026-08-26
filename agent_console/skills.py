from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from .database import Database, utc_now
from .validation import PROFILES

log = logging.getLogger(__name__)

SUPPORTED_TOOLS = frozenset({"codex", "codex-pro", "claude", "hermes"})
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


def _build_catalog_entry(
    name: str,
    source: Path,
    root: Path,
) -> dict[str, Any]:
    frontmatter = _parse_frontmatter(source) if source.is_file() else {}
    kind = frontmatter.get("kind", "standard")
    if kind not in SKILL_KINDS:
        kind = "standard"
    description = frontmatter.get("description", "")
    tools_raw = frontmatter.get("tools", "")
    tools = [t.strip() for t in tools_raw.split(",") if t.strip() in SUPPORTED_TOOLS] if tools_raw else sorted(SUPPORTED_TOOLS)
    allowed_raw = frontmatter.get("allowed_profiles", "")
    allowed = [a.strip() for a in allowed_raw.split(",") if a.strip() in PROFILES] if allowed_raw else None
    requires_approval = frontmatter.get("requires_approval", "").lower() in ("true", "yes", "1")
    return {
        "name": name,
        "description": description,
        "tools": tools,
        "kind": kind,
        "source_path": str(root / name),
        "allowed_profiles": frozenset(allowed) if allowed else None,
        "requires_approval": requires_approval,
    }


def _discover_skills(canonical_root: Path) -> list[dict[str, Any]]:
    if not canonical_root.is_dir():
        return list(SKILL_CATALOG)
    entries = []
    seen = set()
    for entry in SKILL_CATALOG:
        name = entry["name"]
        source = canonical_root / name / "SKILL.md"
        meta = _build_catalog_entry(name, source, canonical_root)
        entries.append(meta)
        seen.add(name)
    for item in canonical_root.iterdir():
        if item.is_dir() and item.name not in seen:
            source = item / "SKILL.md"
            if source.is_file():
                meta = _build_catalog_entry(item.name, source, canonical_root)
                entries.append(meta)
    return entries


def validate_catalog(
    canonical_root: Path | None = None,
) -> list[str]:
    root = canonical_root or _resolve_canonical_root()
    errors: list[str] = []
    entries = _discover_skills(root)
    for entry in entries:
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
    entries = _discover_skills(root)
    result = []
    for entry in entries:
        name = entry["name"]
        source = root / name / "SKILL.md"
        synced = []
        for tool in entry["tools"]:
            link = _tool_root(tool) / name
            synced.append({
                "tool": tool,
                "linked": link.is_symlink() and (link / "SKILL.md").is_file(),
            })
        result.append({
            "name": name,
            "description": entry["description"],
            "kind": entry["kind"],
            "tools": entry["tools"],
            "allowed_profiles": sorted(entry.get("allowed_profiles") or []) if entry.get("allowed_profiles") else None,
            "requires_approval": entry.get("requires_approval", False),
            "source_present": source.is_file(),
            "synced": synced,
        })
    return {"entries": result, "errors": errors}


def _tool_root(tool: str, home: Path | None = None) -> Path:
    h = home or Path.home()
    roots = {
        "codex": h / ".codex" / "skills",
        "codex-pro": h / ".codex" / "skills",
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


def _live_entries(canonical_root: Path) -> list[dict[str, Any]]:
    return _discover_skills(canonical_root) if canonical_root.is_dir() else list(SKILL_CATALOG)


def sync_skills(
    *,
    canonical_root: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    h = home or Path.home()
    entries = _live_entries(root)
    names = [e["name"] for e in entries]
    missing = [name for name in names if not (root / name / "SKILL.md").is_file()]
    if missing:
        raise FileNotFoundError(f"canonical skills missing: {', '.join(missing)}")

    tool_set = {tool for e in entries for tool in e["tools"]}
    roots = {tool: _tool_root(tool, h) for tool in tool_set}
    for r in roots.values():
        _prepare_root(r, root)

    for tool, r in roots.items():
        catalog_names = {e["name"] for e in entries if tool in e["tools"]}
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
        "skills": len(entries),
        "roots": {name: str(p) for name, p in roots.items()},
    }


def doctor_skills(
    *,
    canonical_root: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    h = home or Path.home()
    entries = _live_entries(root)
    problems: list[str] = []
    for entry in entries:
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
        "skills": len(entries),
        "problems": problems,
    }


def check_superpower_approval(
    profile: str,
    skill_name: str,
    *,
    approved: bool = False,
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    entries = _live_entries(root)
    entry = next((e for e in entries if e["name"] == skill_name), None)
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


def assign_skill(
    db: Database,
    profile: str,
    skill_name: str,
    *,
    actor: str = "system",
    surface: str = "CLI",
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile!r}")
    root = canonical_root or _resolve_canonical_root()
    entries = _live_entries(root)
    entry = next((e for e in entries if e["name"] == skill_name), None)
    if entry is None:
        raise ValueError(f"unknown skill in catalog: {skill_name!r}")
    allowed = entry.get("allowed_profiles")
    if allowed is not None and profile not in allowed:
        raise ValueError(
            f"profile {profile!r} is not in skill {skill_name!r} allowed_profiles: {sorted(allowed)}"
        )
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT assigned_at FROM skill_assignments WHERE profile=? AND skill_name=?",
            (profile, skill_name),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"skill {skill_name!r} is already assigned to profile {profile!r} "
                f"(assigned at {existing['assigned_at']})"
            )
        conn.execute(
            "INSERT INTO skill_assignments(profile, skill_name, assigned_by, assigned_surface, assigned_at) "
            "VALUES(?, ?, ?, ?, ?)",
                (profile, skill_name, actor, surface, utc_now()),
        )
    db.audit(
        "skill.assigned",
        f"{profile}/{skill_name}",
        "success",
        actor=actor,
        surface=surface,
        details={"profile": profile, "skill_name": skill_name, "kind": entry["kind"]},
    )
    return {
        "profile": profile,
        "skill_name": skill_name,
        "kind": entry["kind"],
        "requires_approval": entry.get("requires_approval", False),
    }


def unassign_skill(
    db: Database,
    profile: str,
    skill_name: str,
    *,
    actor: str = "system",
    surface: str = "CLI",
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile!r}")
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT assigned_at FROM skill_assignments WHERE profile=? AND skill_name=?",
            (profile, skill_name),
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"skill {skill_name!r} is not assigned to profile {profile!r}"
            )
        conn.execute(
            "DELETE FROM skill_assignments WHERE profile=? AND skill_name=?",
            (profile, skill_name),
        )
    db.audit(
        "skill.unassigned",
        f"{profile}/{skill_name}",
        "success",
        actor=actor,
        surface=surface,
        details={"profile": profile, "skill_name": skill_name},
    )
    return {"profile": profile, "skill_name": skill_name}


def list_assignments(db: Database) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT profile, skill_name, assigned_by, assigned_surface, assigned_at "
            "FROM skill_assignments ORDER BY assigned_at"
        ).fetchall()
    return [dict(row) for row in rows]


def get_profile_assignments(db: Database, profile: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT skill_name, assigned_by, assigned_surface, assigned_at "
            "FROM skill_assignments WHERE profile=? ORDER BY skill_name",
            (profile,),
        ).fetchall()
    return [dict(row) for row in rows]


def is_superpower_approved(db: Database, profile: str, skill_name: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT revoked_at FROM superpower_approvals WHERE profile=? AND skill_name=?",
            (profile, skill_name),
        ).fetchone()
    if row is None:
        return False
    return row["revoked_at"] is None


def approve_superpower(
    db: Database,
    profile: str,
    skill_name: str,
    *,
    actor: str = "system",
    surface: str = "CLI",
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile!r}")
    root = canonical_root or _resolve_canonical_root()
    entries = _live_entries(root)
    entry = next((e for e in entries if e["name"] == skill_name), None)
    if entry is None:
        raise ValueError(f"unknown skill in catalog: {skill_name!r}")
    if entry["kind"] != "superpower":
        raise ValueError(
            f"skill {skill_name!r} is kind {entry['kind']!r}, not superpower; "
            f"only superpowers require explicit approval"
        )
    allowed = entry.get("allowed_profiles")
    if allowed is not None and profile not in allowed:
        raise ValueError(
            f"profile {profile!r} is not allowed to use superpower "
            f"{skill_name!r} (allowed: {sorted(allowed)})"
        )
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT revoked_at FROM superpower_approvals WHERE profile=? AND skill_name=?",
            (profile, skill_name),
        ).fetchone()
        if existing is not None and existing["revoked_at"] is None:
            raise ValueError(
                f"superpower {skill_name!r} is already approved for profile {profile!r}"
            )
        conn.execute(
            "INSERT INTO superpower_approvals(profile, skill_name, approved_by, approved_surface, approved_at) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(profile, skill_name) DO UPDATE SET "
            "approved_by=excluded.approved_by, approved_surface=excluded.approved_surface, "
            "approved_at=excluded.approved_at, revoked_at=NULL",
            (profile, skill_name, actor, surface, utc_now()),
        )
    db.audit(
        "superpower.approved",
        f"{profile}/{skill_name}",
        "success",
        actor=actor,
        surface=surface,
        details={"profile": profile, "skill_name": skill_name},
    )
    return {"profile": profile, "skill_name": skill_name, "approved": True}


def revoke_superpower(
    db: Database,
    profile: str,
    skill_name: str,
    *,
    actor: str = "system",
    surface: str = "CLI",
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile!r}")
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT revoked_at FROM superpower_approvals WHERE profile=? AND skill_name=?",
            (profile, skill_name),
        ).fetchone()
        if existing is None or existing["revoked_at"] is not None:
            raise ValueError(
                f"superpower {skill_name!r} is not currently approved for profile {profile!r}"
            )
        conn.execute(
            "UPDATE superpower_approvals SET revoked_at=? WHERE profile=? AND skill_name=?",
            (utc_now(), profile, skill_name),
        )
    db.audit(
        "superpower.revoked",
        f"{profile}/{skill_name}",
        "success",
        actor=actor,
        surface=surface,
        details={"profile": profile, "skill_name": skill_name},
    )
    return {"profile": profile, "skill_name": skill_name, "revoked": True}


def list_superpower_approvals(db: Database) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT profile, skill_name, approved_by, approved_surface, approved_at, revoked_at "
            "FROM superpower_approvals ORDER BY approved_at"
        ).fetchall()
    return [dict(row) for row in rows]


def isolate_skills(
    isolated_root: Path,
    canonical_root: Path,
    effective_skills: list[dict[str, Any]],
    *,
    cleanup_first: bool = True,
) -> Path:
    if cleanup_first and isolated_root.exists():
        shutil.rmtree(isolated_root)
    isolated_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for skill in effective_skills:
        name = skill["name"]
        target = canonical_root / name
        link = isolated_root / name
        if link.exists():
            if link.is_symlink():
                link.unlink()
            else:
                log.warning("isolated path exists and is not a symlink, skipping: %s", link)
                continue
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            log.warning("failed to create isolated symlink for %s", name)
    return isolated_root


def cleanup_isolated_skills(isolated_root: Path) -> None:
    if isolated_root.exists():
        shutil.rmtree(isolated_root)


def get_effective_skills(
    db: Database,
    profile: str,
    *,
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile!r}")
    root = canonical_root or _resolve_canonical_root()
    entries = _live_entries(root)
    assignments = get_profile_assignments(db, profile)
    effective: list[dict[str, Any]] = []
    issues: list[str] = []
    for a in assignments:
        skill_name = a["skill_name"]
        entry = next((e for e in entries if e["name"] == skill_name), None)
        skill_dir = root / skill_name
        if entry is None:
            if skill_dir.is_dir():
                issues.append(
                    f"assigned skill {skill_name!r} has stale source: "
                    f"SKILL.md not found in {skill_dir}"
                )
            else:
                issues.append(
                    f"assigned skill {skill_name!r} is missing from catalog "
                    "(was removed or skill path changed)"
                )
            continue
        source = skill_dir / "SKILL.md"
        if not source.is_file():
            issues.append(
                f"assigned skill {skill_name!r} has stale source: "
                f"SKILL.md not found at {source}"
            )
            continue
        allowed = entry.get("allowed_profiles")
        if allowed is not None and profile not in allowed:
            issues.append(
                f"profile {profile!r} is disallowed from assigned skill "
                f"{skill_name!r} (allowed: {sorted(allowed)})"
            )
            continue
        if entry["kind"] == "superpower":
            already_approved = is_superpower_approved(db, profile, skill_name)
            ap_result = check_superpower_approval(
                profile, skill_name, approved=already_approved, canonical_root=root
            )
            if not ap_result["allowed"]:
                if ap_result["enforcement"] == "enforced":
                    issues.append(ap_result["reason"])
                    continue
                issues.append(ap_result["reason"])
        effective.append({
            "name": skill_name,
            "kind": entry["kind"],
            "description": entry.get("description", ""),
            "tools": entry["tools"],
            "allowed_profiles": sorted(allowed) if allowed else None,
            "requires_approval": entry.get("requires_approval", False),
            "assigned_at": a["assigned_at"],
            "assigned_by": a["assigned_by"],
        })
    return {
        "profile": profile,
        "total": len(assignments),
        "effective": effective,
        "issues": issues,
    }


def validate_profile_skills(
    db: Database,
    profile: str,
    *,
    canonical_root: Path | None = None,
) -> dict[str, Any]:
    result = get_effective_skills(db, profile, canonical_root=canonical_root)
    return {
        "valid": len(result["issues"]) == 0,
        "effective": result["effective"],
        "issues": result["issues"],
    }


def enrich_catalog_with_assignments(
    catalog: dict[str, Any],
    assignments: list[dict[str, Any]],
) -> dict[str, Any]:
    assigned_map: dict[str, list[dict[str, Any]]] = {}
    for a in assignments:
        skill = a["skill_name"]
        assigned_map.setdefault(skill, []).append({
            "profile": a["profile"],
            "assigned_at": a["assigned_at"],
            "assigned_by": a["assigned_by"],
        })
    enriched_entries = []
    for entry in catalog.get("entries", []):
        entry = dict(entry)
        name = entry["name"]
        entry["assigned_to"] = assigned_map.get(name, [])
        enriched_entries.append(entry)
    result = dict(catalog)
    result["entries"] = enriched_entries
    return result
