from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Mapping

from .database import Database, utc_now
from .skill_capabilities import SKILL_TOOL_CAPABILITIES, SUPPORTED_TOOLS, SkillToolCapability
from .validation import PROFILES

log = logging.getLogger(__name__)

SKILL_KINDS = frozenset({"standard", "superpower"})
MAX_DISCOVERY_ENTRIES = 512
VersionProbe = Callable[[str, Path], str | None]


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
    source_diagnostic = _source_diagnostic(root, name)
    frontmatter = _parse_frontmatter(source) if source_diagnostic["state"] == "present" else {}
    kind = frontmatter.get("kind", "standard")
    if kind not in SKILL_KINDS:
        kind = "standard"
    description = frontmatter.get("description", "")
    tools_raw = frontmatter.get("tools", "")
    declared_tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    tools = (
        [tool for tool in declared_tools if tool in SUPPORTED_TOOLS]
        if tools_raw
        else sorted(SUPPORTED_TOOLS)
    )
    allowed_raw = frontmatter.get("allowed_profiles", "")
    allowed = [a.strip() for a in allowed_raw.split(",") if a.strip() in PROFILES] if allowed_raw else None
    requires_approval = frontmatter.get("requires_approval", "").lower() in ("true", "yes", "1")
    return {
        "name": name,
        "native_id": frontmatter.get("name") or name,
        "description": description,
        "tools": tools,
        "kind": kind,
        "source_path": str(root / name),
        "allowed_profiles": frozenset(allowed) if allowed else None,
        "requires_approval": requires_approval,
        "unsupported_tools": [tool for tool in declared_tools if tool not in SUPPORTED_TOOLS],
        "source_diagnostic": source_diagnostic,
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _lexists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _source_diagnostic(root: Path, name: str) -> dict[str, Any]:
    skill_dir = root / name
    skill_file = skill_dir / "SKILL.md"
    result = {
        "expected_source": str(skill_dir),
        "skill_file": str(skill_file),
        "state": "missing",
        "resolved_source": None,
        "resolved_skill_file": None,
    }
    if not skill_dir.is_dir() or not skill_file.is_file():
        return result
    try:
        root_real = root.resolve(strict=True)
        source_real = skill_dir.resolve(strict=True)
        skill_real = skill_file.resolve(strict=True)
    except (OSError, RuntimeError):
        result["state"] = "invalid"
        return result
    result["resolved_source"] = str(source_real)
    result["resolved_skill_file"] = str(skill_real)
    if not _is_within(source_real, root_real):
        result["state"] = "source-escape"
    elif not _is_within(skill_real, source_real) or not _is_within(skill_real, root_real):
        result["state"] = "skill-file-escape"
    else:
        result["state"] = "present"
    return result


def _discover_skills(canonical_root: Path) -> list[dict[str, Any]]:
    if not canonical_root.is_dir():
        return [
            _build_catalog_entry(
                entry["name"], canonical_root / entry["name"] / "SKILL.md", canonical_root
            )
            for entry in SKILL_CATALOG
        ]
    entries = []
    seen = set()
    for entry in SKILL_CATALOG:
        name = entry["name"]
        source = canonical_root / name / "SKILL.md"
        meta = _build_catalog_entry(name, source, canonical_root)
        entries.append(meta)
        seen.add(name)
    for item in islice(canonical_root.iterdir(), MAX_DISCOVERY_ENTRIES):
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
        source_state = entry["source_diagnostic"]["state"]
        if source_state != "present":
            errors.append(f"skill {name!r}: source is {source_state} at {source}")
        for tool in entry.get("unsupported_tools", []):
            errors.append(f"skill {name!r}: unsupported tool {tool!r}")
        for tool in entry["tools"]:
            if tool not in SUPPORTED_TOOLS:
                errors.append(f"skill {name!r}: unsupported tool {tool!r}")
        allowed = entry.get("allowed_profiles")
        if allowed is not None:
            for profile in allowed:
                if profile not in PROFILES:
                    errors.append(f"skill {name!r}: unknown profile {profile!r}")
    return errors


def _tool_root(
    tool: str,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    return SKILL_TOOL_CAPABILITIES[tool].resolve_native_root(home, environ)[0]


def _resolved_binary(tool: str) -> Path:
    from .providers import TOOL_BINARIES

    configured = TOOL_BINARIES[tool]
    if configured.is_absolute():
        return configured
    found = shutil.which(str(configured))
    return Path(found) if found else configured


def _default_version_probe(tool: str, binary: Path) -> str | None:
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return None
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""


def _version_diagnostics(
    tools: set[str],
    version_probe: VersionProbe | None,
) -> dict[str, dict[str, Any]]:
    probe = version_probe or _default_version_probe
    command_cache: dict[str, str | None] = {}
    result: dict[str, dict[str, Any]] = {}
    for tool in sorted(tools):
        capability = SKILL_TOOL_CAPABILITIES[tool]
        binary = _resolved_binary(tool)
        cache_key = str(binary.resolve(strict=False))
        if cache_key not in command_cache:
            command_cache[cache_key] = probe(tool, binary)
        output = command_cache[cache_key]
        version_match = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b", output or "")
        version = version_match.group(1) if version_match else None
        binary_present = binary.is_file() and os.access(binary, os.X_OK)
        if not capability.supported:
            version_state = "unsupported"
        elif output is None and not binary_present:
            version_state = "missing-binary"
        elif capability.verification == "exact-version":
            version_state = "verified" if version in capability.verified_versions else "unverified-version"
        elif version is None:
            version_state = "unverified-version"
        else:
            version_state = "legacy-compatible"
        mutation_allowed = capability.supported and not (
            capability.unverified_version_policy == "skip"
            and version_state != "verified"
        )
        result[tool] = {
            "binary": str(binary),
            "binary_present": binary_present,
            "installed_version": version,
            "version_state": version_state,
            "mutation_allowed": mutation_allowed,
        }
    return result


def _validate_target_root(path: Path, anchor: Path, *, create: bool) -> None:
    try:
        relative = path.relative_to(anchor)
    except ValueError as exc:
        raise ValueError(f"skill root {path} escapes containment anchor {anchor}") from exc
    existing_components = [anchor, *anchor.parents]
    for component in existing_components:
        if component == Path(component.anchor):
            continue
        if component.is_symlink():
            raise ValueError(f"skill containment path contains a symlink: {component}")
    if create:
        anchor.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not anchor.is_dir():
        raise ValueError(f"skill root containment anchor is not a directory: {anchor}")
    anchor_real = anchor.resolve(strict=True)
    current = anchor
    for part in relative.parts:
        current = current / part
        if _lexists(current):
            if current.is_symlink():
                raise ValueError(f"skill target parent is a symlink: {current}")
            if not current.is_dir():
                raise ValueError(f"skill target parent is not a directory: {current}")
        elif create:
            current.mkdir(mode=0o700)
        else:
            continue
        if not _is_within(current.resolve(strict=True), anchor_real):
            raise ValueError(f"skill target parent escapes containment anchor: {current}")


def _target_diagnostic(path: Path, source: Path) -> dict[str, Any]:
    target: str | None = None
    if not _lexists(path):
        state = "missing"
    elif not path.is_symlink():
        state = "collision"
    else:
        try:
            target_path = path.resolve(strict=True)
            target = str(target_path)
            source_path = source.resolve(strict=True)
            linked_skill = (path / "SKILL.md").resolve(strict=True)
            source_skill = (source / "SKILL.md").resolve(strict=True)
            state = "present" if target_path == source_path and linked_skill == source_skill else "wrong-target"
        except (OSError, RuntimeError):
            state = "wrong-target"
    return {
        "state": state,
        "present": state == "present",
        "linked": state == "present",
        "target": target,
        "collision": state in {"collision", "wrong-target"},
    }


def _discovery_roots(
    capability: SkillToolCapability,
    native_root: Path,
    home: Path,
    project_root: Path | None,
) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    xdg_config = native_root.parents[1] if capability.native_root == "xdg-config/opencode/skills" else None
    for source in capability.discovery_sources:
        if source.base == "home":
            path = home / source.relative_path
        elif source.base == "xdg-config" and xdg_config is not None:
            path = xdg_config / source.relative_path
        elif source.base == "project" and project_root is not None:
            path = project_root / source.relative_path
        else:
            continue
        roots.append({
            **source.as_dict(),
            "path": path,
        })
    return roots


def _scan_discovery_root(item: dict[str, Any]) -> tuple[list[dict[str, Any]], int, bool]:
    root = item["path"]
    if not root.is_dir():
        return [], 0, False
    occurrences: list[dict[str, Any]] = []
    entries_seen = 0
    truncated = False
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth:
            skill_file = directory / "SKILL.md"
            if skill_file.is_file():
                metadata = _parse_frontmatter(skill_file)
                occurrences.append({
                    "name": metadata.get("name") or directory.name,
                    "kind": item["kind"],
                    "path": str(directory),
                    "skill_file": str(skill_file),
                    "resolved_source": str(directory.resolve(strict=False)),
                    "precedence": item["precedence"],
                    "precedence_verified": item["precedence_verified"],
                })
                continue
        if depth and not item["recursive"]:
            continue
        try:
            children = directory.iterdir()
        except OSError:
            continue
        for child in children:
            entries_seen += 1
            if entries_seen > MAX_DISCOVERY_ENTRIES:
                truncated = True
                break
            try:
                is_directory = child.is_dir()
            except OSError:
                is_directory = False
            if not is_directory:
                continue
            if child.is_symlink():
                skill_file = child / "SKILL.md"
                if skill_file.is_file():
                    metadata = _parse_frontmatter(skill_file)
                    occurrences.append({
                        "name": metadata.get("name") or child.name,
                        "kind": item["kind"],
                        "path": str(child),
                        "skill_file": str(skill_file),
                        "resolved_source": str(child.resolve(strict=False)),
                        "precedence": item["precedence"],
                        "precedence_verified": item["precedence_verified"],
                    })
            else:
                stack.append((child, depth + 1))
        if truncated:
            break
    if stack:
        truncated = True
    return occurrences, entries_seen, truncated


def _scan_discovery(
    capability: SkillToolCapability,
    native_root: Path,
    home: Path,
    project_root: Path | None,
    version_state: str,
) -> dict[str, Any]:
    roots = _discovery_roots(capability, native_root, home, project_root)
    occurrences: dict[str, list[dict[str, Any]]] = {}
    truncated = False
    root_results: list[dict[str, Any]] = []
    for item in roots:
        path = item["path"]
        found, entries_seen, source_truncated = _scan_discovery_root(item)
        truncated = truncated or source_truncated
        for occurrence in found:
            occurrences.setdefault(occurrence["name"], []).append(occurrence)
        root_results.append({
            "kind": item["kind"],
            "path": str(path),
            "recursive": item["recursive"],
            "precedence": item["precedence"],
            "precedence_verified": item["precedence_verified"],
            "skills_found": len(found),
            "entries_scanned": entries_seen,
            "truncated": source_truncated,
        })
    discovered_skills = []
    duplicates = []
    for name, paths in sorted(occurrences.items()):
        ordering_verified = len(paths) == 1 or all(
            path["precedence_verified"] and path["precedence"] is not None for path in paths
        )
        ordered = sorted(paths, key=lambda path: path["precedence"] or -1)
        winner = ordered[-1] if ordering_verified else None
        item = {
            "name": name,
            "paths": [path["path"] for path in paths],
            "winner": winner["path"] if winner else None,
            "shadowed": (
                [path["path"] for path in ordered[:-1]] if winner else []
            ),
            "ordering_state": "verified" if winner else "uncertain",
            "occurrences": paths,
        }
        discovered_skills.append(item)
        if len(paths) > 1:
            duplicates.append(item)
    uncertainty_reasons: list[str] = []
    if truncated:
        uncertainty_reasons.append("discovery scan reached its entry bound")
    if capability.verification == "exact-version" and version_state != "verified":
        uncertainty_reasons.append("installed version is not verified")
    if not capability.configured_sources_inspected:
        uncertainty_reasons.append("configured discovery sources are not inspected")
    if any(item["ordering_state"] == "uncertain" for item in duplicates):
        uncertainty_reasons.append("duplicate ordering includes an unverified source")
    return {
        "status": "uncertain" if uncertainty_reasons else "complete",
        "uncertainty_reasons": uncertainty_reasons,
        "bounded_entries_per_root": MAX_DISCOVERY_ENTRIES,
        "roots": root_results,
        "skills": discovered_skills,
        "duplicates": duplicates,
    }


def _collect_catalog_state(
    root: Path,
    *,
    home: Path | None,
    project_root: Path | None,
    version_probe: VersionProbe | None,
    version_info: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = _discover_skills(root)
    tools = {tool for entry in entries for tool in entry["tools"]}
    revoked_links = _managed_revoked_links(entries, home)
    tools.update(link["tool"] for link in revoked_links)
    versions = version_info or _version_diagnostics(tools, version_probe)
    actual_home = Path.home() if home is None else Path(home)
    providers: list[dict[str, Any]] = []
    discovery_by_tool: dict[str, dict[str, Any]] = {}
    for tool in sorted(tools):
        capability = SKILL_TOOL_CAPABILITIES[tool]
        native_root, anchor = capability.resolve_native_root(home)
        discovery = _scan_discovery(
            capability, native_root, actual_home, project_root, versions[tool]["version_state"]
        )
        discovery_by_tool[tool] = discovery
        providers.append({
            **capability.as_dict(),
            **versions[tool],
            "native_root": str(native_root),
            "containment_anchor": str(anchor),
            "discovery": discovery,
        })

    diagnostics: list[dict[str, Any]] = []
    for entry in entries:
        name = entry["name"]
        native_id = entry["native_id"]
        source = root / name
        for tool in entry["tools"]:
            native_root = _tool_root(tool, home)
            path = native_root / name
            target = _target_diagnostic(path, source)
            discovered = next(
                (
                    item for item in discovery_by_tool[tool]["skills"]
                    if item["name"] == native_id
                ),
                None,
            )
            shadowed_by = []
            try:
                expected_real = str(source.resolve(strict=True))
            except (OSError, RuntimeError):
                expected_real = None
            conflicting_paths = (
                [
                    occurrence["path"]
                    for occurrence in discovered["occurrences"]
                    if expected_real is None or occurrence["resolved_source"] != expected_real
                ]
                if discovered else []
            )
            if discovered is not None:
                winner_occurrence = next(
                    (
                        occurrence for occurrence in discovered["occurrences"]
                        if occurrence["path"] == discovered["winner"]
                    ),
                    None,
                )
                if (
                    winner_occurrence is not None
                    and winner_occurrence["path"] != str(path)
                    and winner_occurrence["resolved_source"] != expected_real
                ):
                    shadowed_by = [discovered["winner"]]
                elif not discovered["winner"] and str(path) not in discovered["paths"]:
                    shadowed_by = list(conflicting_paths)
            diagnostics.append({
                "skill": name,
                "native_id": native_id,
                "tool": tool,
                "expected_source": str(source),
                "source_skill_file": str(source / "SKILL.md"),
                "source_state": entry["source_diagnostic"]["state"],
                "native_path": str(path),
                "materialized_path": str(path),
                **target,
                "collision": target["collision"] or bool(conflicting_paths),
                "shadowed": bool(shadowed_by),
                "shadowed_by": shadowed_by,
                "discovered_paths": discovered["paths"] if discovered else [],
                "installed_version": versions[tool]["installed_version"],
                "version_state": versions[tool]["version_state"],
                "mutation_allowed": versions[tool]["mutation_allowed"],
            })
    for revoked in revoked_links:
        tool = revoked["tool"]
        diagnostics.append({
            "skill": revoked["skill"],
            "native_id": revoked["native_id"],
            "tool": tool,
            "expected_source": str(revoked["source"]),
            "source_skill_file": str(revoked["source"] / "SKILL.md"),
            "source_state": "present",
            "native_path": str(revoked["path"]),
            "materialized_path": str(revoked["path"]),
            "state": "revoked-managed-link",
            "present": True,
            "linked": True,
            "target": str(revoked["source"].resolve(strict=True)),
            "collision": False,
            "shadowed": False,
            "shadowed_by": [],
            "discovered_paths": [str(revoked["path"])],
            "installed_version": versions[tool]["installed_version"],
            "version_state": versions[tool]["version_state"],
            "mutation_allowed": versions[tool]["mutation_allowed"],
        })
    return {
        "entries": entries,
        "providers": providers,
        "diagnostics": diagnostics,
        "versions": versions,
    }


def _managed_revoked_links(
    entries: list[dict[str, Any]],
    home: Path | None,
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[tuple[Path, Path]] = set()
    for entry in entries:
        source = Path(entry["source_path"])
        allowed_roots = {_tool_root(tool, home) for tool in entry["tools"]}
        for tool in sorted(SUPPORTED_TOOLS - set(entry["tools"])):
            native_root = _tool_root(tool, home)
            if native_root in allowed_roots:
                continue
            path = native_root / entry["name"]
            ownership_key = (path, source)
            if (
                ownership_key not in seen
                and _target_diagnostic(path, source)["state"] == "present"
            ):
                seen.add(ownership_key)
                links.append({
                    "tool": tool,
                    "skill": entry["name"],
                    "native_id": entry["native_id"],
                    "source": source,
                    "path": path,
                })
    return links


def skill_catalog(
    canonical_root: Path | None = None,
    *,
    home: Path | None = None,
    project_root: Path | None = None,
    version_probe: VersionProbe | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    state = _collect_catalog_state(
        root, home=home, project_root=project_root, version_probe=version_probe
    )
    by_skill: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in state["diagnostics"]:
        by_skill.setdefault(diagnostic["skill"], []).append(diagnostic)
    result = []
    for entry in state["entries"]:
        result.append({
            "name": entry["name"],
            "native_id": entry["native_id"],
            "description": entry["description"],
            "kind": entry["kind"],
            "tools": entry["tools"],
            "allowed_profiles": sorted(entry.get("allowed_profiles") or []) if entry.get("allowed_profiles") else None,
            "requires_approval": entry.get("requires_approval", False),
            "source_present": entry["source_diagnostic"]["state"] == "present",
            "source": entry["source_diagnostic"],
            "synced": by_skill.get(entry["name"], []),
        })
    return {
        "entries": result,
        "errors": validate_catalog(root),
        "providers": state["providers"],
        "diagnostics": state["diagnostics"],
    }


def _live_entries(canonical_root: Path) -> list[dict[str, Any]]:
    return _discover_skills(canonical_root)


def sync_skills(
    *,
    canonical_root: Path | None = None,
    home: Path | None = None,
    project_root: Path | None = None,
    version_probe: VersionProbe | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    entries = _live_entries(root)
    invalid_sources = [
        (entry["name"], entry["source_diagnostic"]["state"])
        for entry in entries
        if entry["source_diagnostic"]["state"] != "present"
    ]
    if invalid_sources:
        details = ", ".join(f"{name} ({source_state})" for name, source_state in invalid_sources)
        if all(source_state == "missing" for _, source_state in invalid_sources):
            raise FileNotFoundError(f"canonical skills missing: {details}")
        raise ValueError(f"canonical skills are unsafe: {details}")
    unsupported = [
        f"{entry['name']}: {', '.join(entry['unsupported_tools'])}"
        for entry in entries if entry.get("unsupported_tools")
    ]
    if unsupported:
        raise ValueError(f"canonical skills declare unsupported tools: {'; '.join(unsupported)}")
    revoked_links = _managed_revoked_links(entries, home)
    tool_set = {tool for e in entries for tool in e["tools"]}
    tool_set.update(link["tool"] for link in revoked_links)
    versions = _version_diagnostics(tool_set, version_probe)
    roots = {tool: _tool_root(tool, home) for tool in tool_set}
    problems: list[str] = []
    skipped: list[dict[str, str]] = []
    changed: list[dict[str, str]] = []
    blocked_tools: set[str] = set()
    pre_state = _collect_catalog_state(
        root,
        home=home,
        project_root=project_root,
        version_probe=version_probe,
        version_info=versions,
    )
    for diagnostic in pre_state["diagnostics"]:
        if (
            diagnostic["collision"]
            and diagnostic["state"] not in {"wrong-target", "collision", "revoked-managed-link"}
        ):
            problems.append(
                f"{diagnostic['tool']} {diagnostic['skill']}: discovery collision at "
                f"{', '.join(diagnostic['discovered_paths'])}"
            )
            blocked_tools.add(diagnostic["tool"])
    for tool in sorted(tool_set):
        capability = SKILL_TOOL_CAPABILITIES[tool]
        native_root, anchor = capability.resolve_native_root(home)
        if not versions[tool]["mutation_allowed"]:
            skipped.append({
                "tool": tool,
                "reason": f"mutation skipped: {versions[tool]['version_state']}",
            })
            blocked_tools.add(tool)
            continue
        try:
            _validate_target_root(native_root, anchor, create=False)
        except ValueError as exc:
            # A missing anchor/root is safe to create; existing unsafe chains are not.
            if anchor.exists():
                problems.append(f"{tool}: {exc}")
                blocked_tools.add(tool)
                continue
        for entry in entries:
            if tool not in entry["tools"]:
                continue
            target = _target_diagnostic(native_root / entry["name"], root / entry["name"])
            if target["state"] in {"wrong-target", "collision"}:
                problems.append(
                    f"{tool} {entry['name']}: refusing to replace {target['state']} at "
                    f"{native_root / entry['name']}"
                )
                blocked_tools.add(tool)
    for tool in sorted(tool_set - blocked_tools):
        native_root, anchor = SKILL_TOOL_CAPABILITIES[tool].resolve_native_root(home)
        try:
            _validate_target_root(native_root, anchor, create=True)
            for entry in entries:
                if tool not in entry["tools"]:
                    continue
                path = native_root / entry["name"]
                if _target_diagnostic(path, root / entry["name"])["state"] == "present":
                    continue
                path.symlink_to(root / entry["name"], target_is_directory=True)
                changed.append({
                    "action": "linked",
                    "tool": tool,
                    "skill": entry["name"],
                    "path": str(path),
                })
            for revoked in revoked_links:
                if revoked["tool"] != tool:
                    continue
                path = revoked["path"]
                if _target_diagnostic(path, revoked["source"])["state"] != "present":
                    problems.append(
                        f"{tool} {revoked['skill']}: revoked link ownership changed during sync"
                    )
                    blocked_tools.add(tool)
                    continue
                path.unlink()
                changed.append({
                    "action": "unlinked-revoked",
                    "tool": tool,
                    "skill": revoked["skill"],
                    "path": str(path),
                })
        except (OSError, ValueError) as exc:
            problems.append(f"{tool}: sync failed safely: {exc}")
            blocked_tools.add(tool)
    final_state = _collect_catalog_state(
        root,
        home=home,
        project_root=project_root,
        version_probe=version_probe,
        version_info=versions,
    )
    ok = not problems and not skipped
    return {
        "ok": ok,
        "partial": not ok and bool(changed),
        "canonical_root": str(root),
        "skills": len(entries),
        "roots": {name: str(p) for name, p in roots.items()},
        "changed": changed,
        "skipped": skipped,
        "problems": problems,
        "providers": final_state["providers"],
        "diagnostics": final_state["diagnostics"],
    }


def doctor_skills(
    *,
    canonical_root: Path | None = None,
    home: Path | None = None,
    project_root: Path | None = None,
    version_probe: VersionProbe | None = None,
) -> dict[str, Any]:
    root = canonical_root or _resolve_canonical_root()
    state = _collect_catalog_state(
        root, home=home, project_root=project_root, version_probe=version_probe
    )
    entries = state["entries"]
    problems: list[str] = []
    warnings: list[str] = []
    for entry in entries:
        name = entry["name"]
        source = root / name / "SKILL.md"
        source_state = entry["source_diagnostic"]["state"]
        if source_state != "present":
            problems.append(f"canonical skill {name} is {source_state}: {source}")
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            problems.append(f"missing frontmatter: {name}")
    for diagnostic in state["diagnostics"]:
        if diagnostic["state"] != "present":
            problems.append(
                f"{diagnostic['tool']} materialization {diagnostic['state']}: {diagnostic['skill']}"
            )
        if diagnostic["collision"] and diagnostic["state"] == "present":
            problems.append(
                f"{diagnostic['tool']} discovery collision: {diagnostic['skill']}"
            )
        if diagnostic["shadowed"]:
            problems.append(
                f"{diagnostic['tool']} skill is shadowed: {diagnostic['skill']} by "
                f"{', '.join(diagnostic['shadowed_by'])}"
            )
    for provider in state["providers"]:
        if not provider["mutation_allowed"]:
            problems.append(
                f"{provider['tool']} mutation disabled: {provider['version_state']}"
            )
        elif provider["version_state"] in {"missing-binary", "unverified-version"}:
            warnings.append(
                f"{provider['tool']} version status: {provider['version_state']}"
            )
    return {
        "ok": not problems,
        "skills": len(entries),
        "problems": problems,
        "warnings": warnings,
        "providers": state["providers"],
        "diagnostics": state["diagnostics"],
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
