from __future__ import annotations

from pathlib import Path
from typing import Any

from .validation import PROFILES, validate_profile


_ALL_PROFILES: frozenset[str] = frozenset(PROFILES)

PROFILE_SCHEMA: dict[str, dict[str, Any]] = {
    "general": {
        "name": "general",
        "display_name": "General",
        "description": "Operate as a normal interactive agent. Read the workspace AGENTS.md and the applicable repository AGENTS.md or CLAUDE.md before acting. Do not assume a plan exists; determine the task from the user.",
        "read_write_capability": "write",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset(),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset(),
        "requires_human_approval": False,
        "manages_session_links": False,
        "status": "active",
    },
    "coder": {
        "name": "coder",
        "display_name": "Coder",
        "description": "Implement only the approved plan, sprint item, or explicit coding task. Read repository instructions first, prefer an isolated worktree, keep changes bounded, and run relevant tests. Do not push, merge, deploy, or release without explicit approval. Report changed files, tests, and remaining risks.",
        "read_write_capability": "write",
        "worktree_requirement": "preferred",
        "delegation_permissions": frozenset(),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset(),
        "requires_human_approval": False,
        "manages_session_links": True,
        "status": "active",
    },
    "planner": {
        "name": "planner",
        "display_name": "Planner",
        "description": "Inspect the task and produce a decision-complete implementation plan. Never edit or create repository files, commit, deploy, or implement. Separate verified facts, assumptions, and recommendations. Include acceptance criteria and validation steps, and finish with a durable plan artifact.",
        "read_write_capability": "read_only",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset({"read_only"}),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset({"plan"}),
        "requires_human_approval": False,
        "manages_session_links": True,
        "status": "active",
    },
    "scout": {
        "name": "scout",
        "display_name": "Scout",
        "description": "Read repository files and history to locate relevant components and explain existing behavior. Support findings with paths, code, tests, or history. Do not edit, implement, commit, or expand the requested investigation.",
        "read_write_capability": "read_only",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset({"read_only"}),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset({"plan"}),
        "requires_human_approval": False,
        "manages_session_links": False,
        "status": "active",
    },
    "reviewer": {
        "name": "reviewer",
        "display_name": "Reviewer",
        "description": "Review the specified diff, branch, commit, or worktree for correctness, regressions, security issues, and missing tests. Rank findings by severity and cite locations. Do not modify the reviewed work and do not approve solely because tests pass.",
        "read_write_capability": "read_only",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset({"read_only"}),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset({"plan"}),
        "requires_human_approval": False,
        "manages_session_links": False,
        "status": "active",
    },
    "researcher": {
        "name": "researcher",
        "display_name": "Researcher",
        "description": "Research external documentation, APIs, standards, and current behavior without modifying repository code. Prefer primary sources, provide precise citations, and distinguish current documentation from historical behavior.",
        "read_write_capability": "read_only",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset({"read_only"}),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset({"plan"}),
        "requires_human_approval": False,
        "manages_session_links": False,
        "status": "active",
    },
    "verifier": {
        "name": "verifier",
        "display_name": "Verifier",
        "description": "Validate the stated acceptance criteria. Run tests, builds, linters, and targeted reproductions without modifying production code. Record exact commands and classify each result as pass, fail, blocked, or not tested. Temporary output may be created outside the repository when necessary.",
        "read_write_capability": "read_only",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset({"read_only"}),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset({"plan"}),
        "requires_human_approval": False,
        "manages_session_links": False,
        "status": "active",
    },
    "bugfix": {
        "name": "bugfix",
        "display_name": "Bugfix",
        "description": "Reproduce the reported problem before editing, identify the root cause, and use an isolated worktree. Make the smallest reasonable correction and add or update a regression test. Avoid unrelated refactoring and do not push or merge.",
        "read_write_capability": "write",
        "worktree_requirement": "preferred",
        "delegation_permissions": frozenset(),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset(),
        "requires_human_approval": False,
        "manages_session_links": False,
        "status": "active",
    },
    "release": {
        "name": "release",
        "display_name": "Release",
        "description": "Operate only after explicit human approval. Review the approved diff and test evidence, and stage, commit, push, or open a pull request only within the approved scope. Do not introduce implementation changes. Stop if the tree differs from the approved state, and never merge without separate authorization.",
        "read_write_capability": "write",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset(),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset(),
        "requires_human_approval": True,
        "manages_session_links": True,
        "status": "active",
    },
    "operator": {
        "name": "operator",
        "display_name": "Operator",
        "description": "Manage services, deployments, logs, and server configuration within the approved task. Show potentially destructive commands before running them. Require explicit confirmation for deletion, data migration, firewall changes, credential changes, and service replacement. Prefer user services and always provide rollback instructions.",
        "read_write_capability": "write",
        "worktree_requirement": "none",
        "delegation_permissions": frozenset(),
        "allowed_delegation_profiles": frozenset({"planner", "researcher", "reviewer", "scout"}),
        "allowed_collaboration_profiles": _ALL_PROFILES,
        "legacy_aliases": frozenset(),
        "replacement_profile": None,
        "provider_mode_constraints": frozenset(),
        "requires_human_approval": True,
        "manages_session_links": True,
        "status": "active",
    },
}

READ_ONLY_PROFILES: frozenset[str] = frozenset(
    name for name, meta in PROFILE_SCHEMA.items()
    if meta["read_write_capability"] == "read_only"
)

WRITE_PROFILES: frozenset[str] = frozenset(
    name for name, meta in PROFILE_SCHEMA.items()
    if meta["read_write_capability"] == "write"
)


def _serialize_metadata(name: str, meta: dict[str, Any], profile_dir: Path) -> dict[str, Any]:
    path = profile_dir / f"{name}.md"
    result = dict(meta)
    result["installed"] = path.is_file()
    result["path"] = str(path)
    for key in ("delegation_permissions", "allowed_delegation_profiles",
                 "allowed_collaboration_profiles", "legacy_aliases",
                 "provider_mode_constraints"):
        if isinstance(result.get(key), frozenset):
            result[key] = sorted(result[key])
    return result


def profile_text(profile_dir: Path, profile: str) -> str:
    validate_profile(profile)
    path = profile_dir / f"{profile}.md"
    if not path.is_file():
        raise FileNotFoundError(f"profile is not installed: {path}")
    return path.read_text(encoding="utf-8")


def installed_profiles(profile_dir: Path) -> list[dict[str, Any]]:
    result = []
    for name in PROFILE_SCHEMA:
        meta = PROFILE_SCHEMA[name]
        entry = _serialize_metadata(name, meta, profile_dir)
        result.append(entry)
    result.sort(key=lambda e: e["name"])
    return result


def profile_summaries() -> list[dict[str, Any]]:
    names = sorted(name for name in PROFILE_SCHEMA if name != "general")
    ordered = ["general"] + names
    summaries = []
    for name in ordered:
        meta = PROFILE_SCHEMA[name]
        summaries.append({
            "name": name,
            "display_name": meta["display_name"],
            "read_write_capability": meta["read_write_capability"],
            "worktree_requirement": meta["worktree_requirement"],
            "requires_human_approval": meta["requires_human_approval"],
            "status": meta["status"],
        })
    return summaries


def validate_profile_schema() -> None:
    missing = PROFILES - PROFILE_SCHEMA.keys()
    if missing:
        raise RuntimeError(
            f"profiles missing from PROFILE_SCHEMA: {sorted(missing)}"
        )
    extra = PROFILE_SCHEMA.keys() - PROFILES
    if extra:
        raise RuntimeError(
            f"profiles in PROFILE_SCHEMA not in validation.PROFILES: {sorted(extra)}"
        )
    for name, meta in PROFILE_SCHEMA.items():
        expected = {
            "name", "display_name", "description", "read_write_capability",
            "worktree_requirement", "delegation_permissions",
            "allowed_delegation_profiles", "allowed_collaboration_profiles",
            "legacy_aliases", "replacement_profile", "provider_mode_constraints",
            "requires_human_approval", "manages_session_links", "status",
        }
        actual = set(meta.keys())
        missing_fields = expected - actual
        if missing_fields:
            raise RuntimeError(f"profile {name!r} missing fields: {sorted(missing_fields)}")
        extra_fields = actual - expected
        if extra_fields:
            raise RuntimeError(f"profile {name!r} has unknown fields: {sorted(extra_fields)}")
        if meta["name"] != name:
            raise RuntimeError(
                f"profile schema key {name!r} does not match meta.name {meta['name']!r}"
            )
        if meta["read_write_capability"] not in ("read_only", "write"):
            raise RuntimeError(
                f"profile {name!r} has invalid read_write_capability: {meta['read_write_capability']!r}"
            )
        if meta["worktree_requirement"] not in ("none", "preferred", "required"):
            raise RuntimeError(
                f"profile {name!r} has invalid worktree_requirement: {meta['worktree_requirement']!r}"
            )
        if meta["status"] not in ("active", "deprecated", "legacy"):
            raise RuntimeError(
                f"profile {name!r} has invalid status: {meta['status']!r}"
            )
        if meta["replacement_profile"] is not None and meta["replacement_profile"] not in PROFILES:
            raise RuntimeError(
                f"profile {name!r} replacement_profile {meta['replacement_profile']!r} "
                f"is not a known profile"
            )
