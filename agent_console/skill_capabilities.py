from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class SkillDiscoverySource:
    kind: str
    base: str
    relative_path: str
    recursive: bool
    precedence: int | None
    precedence_verified: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "base": self.base,
            "relative_path": self.relative_path,
            "recursive": self.recursive,
            "precedence": self.precedence,
            "precedence_verified": self.precedence_verified,
        }


@dataclass(frozen=True)
class SkillToolCapability:
    """Static skill behavior for one Agent Console tool integration."""

    tool: str
    supported: bool
    native_root: str
    supports_on_demand: bool
    supports_permissions: bool
    materialization_method: str
    can_isolate_skills: bool
    verification: str
    discovery_sources: tuple[SkillDiscoverySource, ...]
    configured_sources_inspected: bool = False
    verified_versions: frozenset[str] = frozenset()
    unverified_version_policy: str = "allow"

    def resolve_native_root(
        self,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[Path, Path]:
        """Return the native root and its containment anchor.

        Explicit homes are test/fixture homes and deliberately ignore the caller's
        XDG variables. The real home follows XDG_CONFIG_HOME for OpenCode.
        """

        env = os.environ if environ is None else environ
        explicit_home = home is not None
        resolved_home = Path.home() if home is None else Path(home)
        if self.native_root == "xdg-config/opencode/skills":
            configured = Path(env.get("XDG_CONFIG_HOME", resolved_home / ".config"))
            config_home = (
                resolved_home / ".config"
                if explicit_home or not configured.is_absolute()
                else configured
            )
            anchor = resolved_home if explicit_home else config_home
            return config_home / "opencode" / "skills", anchor
        return resolved_home / self.native_root, resolved_home

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "supported": self.supported,
            "native_root": self.native_root,
            "native_root_spec": self.native_root,
            "supports_on_demand": self.supports_on_demand,
            "supports_project_skills": any(source.base == "project" for source in self.discovery_sources),
            "supports_global_skills": any(source.base in {"home", "xdg-config"} for source in self.discovery_sources),
            "isolation_scope": "Console-selected snapshots only; native user, project, admin or plugin sources may remain active.",
            "supports_permissions": self.supports_permissions,
            "materialization_method": self.materialization_method,
            "can_isolate_skills": self.can_isolate_skills,
            "verification": self.verification,
            "discovery_sources": [source.as_dict() for source in self.discovery_sources],
            "configured_sources_inspected": self.configured_sources_inspected,
            "verified_versions": sorted(self.verified_versions),
            "unverified_version_policy": self.unverified_version_policy,
        }


SKILL_TOOL_CAPABILITIES: dict[str, SkillToolCapability] = {
    "codex": SkillToolCapability(
        tool="codex",
        supported=True,
        native_root=".codex/skills",
        supports_on_demand=True,
        supports_permissions=True,
        materialization_method="symlink",
        can_isolate_skills=True,
        verification="recorded-version",
        discovery_sources=(
            SkillDiscoverySource("native-global", "home", ".codex/skills", True, None, False),
            SkillDiscoverySource("global-agents", "home", ".agents/skills", True, None, False),
            SkillDiscoverySource("native-project", "project", ".codex/skills", True, None, False),
            SkillDiscoverySource("project-agents", "project", ".agents/skills", True, None, False),
        ),
        verified_versions=frozenset({"0.159.2"}),
    ),
    "codex-pro": SkillToolCapability(
        tool="codex-pro",
        supported=True,
        native_root=".codex/skills",
        supports_on_demand=True,
        supports_permissions=True,
        materialization_method="symlink",
        can_isolate_skills=True,
        verification="recorded-version",
        discovery_sources=(
            SkillDiscoverySource("native-global", "home", ".codex/skills", True, None, False),
            SkillDiscoverySource("global-agents", "home", ".agents/skills", True, None, False),
            SkillDiscoverySource("native-project", "project", ".codex/skills", True, None, False),
            SkillDiscoverySource("project-agents", "project", ".agents/skills", True, None, False),
        ),
        verified_versions=frozenset({"0.159.2"}),
    ),
    "claude": SkillToolCapability(
        tool="claude",
        supported=True,
        native_root=".claude/skills",
        supports_on_demand=True,
        supports_permissions=True,
        materialization_method="symlink",
        can_isolate_skills=False,
        verification="recorded-version",
        discovery_sources=(
            SkillDiscoverySource("native-global", "home", ".claude/skills", True, None, False),
            SkillDiscoverySource("native-project", "project", ".claude/skills", True, None, False),
        ),
        verified_versions=frozenset({"2.1.287"}),
    ),
    "hermes": SkillToolCapability(
        tool="hermes",
        supported=True,
        native_root=".hermes/skills/homelab",
        supports_on_demand=True,
        supports_permissions=False,
        materialization_method="symlink",
        can_isolate_skills=False,
        verification="recorded-version",
        discovery_sources=(
            SkillDiscoverySource("native-global", "home", ".hermes/skills", True, None, False),
            SkillDiscoverySource("native-project", "project", ".hermes/skills", True, None, False),
            SkillDiscoverySource("project-agents", "project", ".agents/skills", True, None, False),
        ),
        verified_versions=frozenset({"0.21.4"}),
    ),
    "pi": SkillToolCapability(
        tool="pi",
        supported=True,
        native_root=".pi/agent/skills",
        supports_on_demand=True,
        supports_permissions=False,
        materialization_method="symlink",
        can_isolate_skills=True,
        verification="exact-version",
        discovery_sources=(
            SkillDiscoverySource("native-global", "home", ".pi/agent/skills", True, None, False),
            SkillDiscoverySource("global-agents", "home", ".agents/skills", True, None, False),
            SkillDiscoverySource("native-project", "project", ".pi/skills", True, None, False),
            SkillDiscoverySource("project-agents", "project", ".agents/skills", True, None, False),
        ),
        # Package/settings/ancestor sources are also possible. Do not claim the
        # static inventory is exhaustive or that it suppresses native sources.
        configured_sources_inspected=False,
        verified_versions=frozenset({"0.99.2"}),
        unverified_version_policy="skip",
    ),
    "opencode": SkillToolCapability(
        tool="opencode",
        supported=True,
        native_root="xdg-config/opencode/skills",
        supports_on_demand=True,
        supports_permissions=True,
        materialization_method="symlink",
        can_isolate_skills=False,
        verification="exact-version",
        discovery_sources=(
            # Repeated isolated 1.18.30 discovery selected different winners
            # for the same duplicate across these roots. Discovery is verified;
            # deterministic precedence is not.
            SkillDiscoverySource("legacy-global-claude", "home", ".claude/skills", True, None, False),
            SkillDiscoverySource("global-agents", "home", ".agents/skills", True, None, False),
            SkillDiscoverySource("native-global", "xdg-config", "opencode/skills", True, None, False),
            SkillDiscoverySource("project-claude", "project", ".claude/skills", True, None, False),
            SkillDiscoverySource("project-agents", "project", ".agents/skills", True, None, False),
            SkillDiscoverySource("native-project", "project", ".opencode/skills", True, None, False),
        ),
        verified_versions=frozenset({"1.18.30", "1.18.31"}),
        unverified_version_policy="skip",
    ),
}


SUPPORTED_TOOLS = frozenset(
    tool for tool, capability in SKILL_TOOL_CAPABILITIES.items() if capability.supported
)
