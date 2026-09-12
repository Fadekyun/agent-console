from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth import AuthRegistry
from .skill_capabilities import SKILL_TOOL_CAPABILITIES


def _resolve_binary(env_var: str, fallback: str) -> Path:
    env_val = os.getenv(env_var)
    if env_val:
        return Path(env_val)
    which = shutil.which(fallback)
    if which:
        return Path(which)
    return Path(fallback)


TOOL_BINARIES = {
    "codex": _resolve_binary("AGCONSOLE_CODEX_BIN", "codex"),
    "codex-pro": Path(os.getenv("AGCONSOLE_CODEX_PRO_BIN", str(_resolve_binary("AGCONSOLE_CODEX_BIN", "codex")))),
    "claude": _resolve_binary("AGCONSOLE_CLAUDE_BIN", "claude"),
    "opencode": _resolve_binary("AGCONSOLE_OPENCODE_BIN", "opencode"),
    "hermes": _resolve_binary("AGCONSOLE_HERMES_BIN", "hermes"),
    "shell": Path(os.getenv("AGCONSOLE_SHELL_BIN", "/usr/bin/zsh")),
}


@dataclass(frozen=True)
class LaunchSpec:
    argv: list[str]
    environment: dict[str, str]
    secret_files: list[Path]


class ProviderAdapter:
    tool: str

    def __init__(self, registry: AuthRegistry):
        self.registry = registry

    @property
    def binary(self) -> Path:
        return TOOL_BINARIES[self.tool]

    def availability(self, context_name: str | None = None) -> dict[str, Any]:
        context = self.registry.get_context(self.tool, context_name)
        if not self.binary.is_file():
            return {**context, "status": "error", "reason": "tool launcher is missing"}
        return context

    def auth_status(self, context_name: str | None = None) -> dict[str, Any]:
        return self.availability(context_name)

    @property
    def can_isolate_skills(self) -> bool:
        capability = SKILL_TOOL_CAPABILITIES.get(self.tool)
        return bool(capability and capability.can_isolate_skills)

    def build_environment(self, context: dict[str, Any]) -> dict[str, str]:
        return {}

    def secret_files(self, context: dict[str, Any]) -> list[Path]:
        secret_ref = context.get("secret_ref")
        return [self.registry.secret_path(secret_ref)] if secret_ref else []

    def build_argv(
        self,
        *,
        context: dict[str, Any],
        profile: str,
        cwd: Path,
        role: str,
        context_path: Path,
        agent_mode: str | None,
        model: str | None,
        read_only: bool,
        reasoning_effort: str | None = None,
        plan_reasoning_effort: str | None = None,
    ) -> list[str]:
        raise NotImplementedError

    def build_launch_spec(self, **kwargs: Any) -> LaunchSpec:
        context = kwargs["context"]
        return LaunchSpec(
            argv=self.build_argv(**kwargs),
            environment=self.build_environment(context),
            secret_files=self.secret_files(context),
        )

    def login(self, context: dict[str, Any]) -> int:
        raise RuntimeError(f"interactive login is not implemented for {self.tool}")

    def interrupt(self, session: dict[str, Any]) -> None:
        """Prepare a provider for the shared tmux Ctrl-C lifecycle action."""

    def restart(self, session: dict[str, Any]) -> None:
        """Prepare a provider before its pinned launcher is respawned."""

    def resume(self, session: dict[str, Any]) -> None:
        """Prepare a provider before the shared tmux session is attached."""

    def doctor(self, context_name: str | None = None) -> dict[str, Any]:
        context = self.auth_status(context_name)
        return {"ok": context["status"] == "ready", "context": context}


def _codex_pin_args(
    *,
    model: str | None,
    reasoning_effort: str | None,
    plan_reasoning_effort: str | None,
) -> list[str]:
    """Build `-c` override args for a Codex model/effort pin."""
    args: list[str] = []
    if model:
        args += ["-c", f'model="{model}"']
    if reasoning_effort:
        args += ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    if plan_reasoning_effort:
        args += ["-c", f'plan_mode_reasoning_effort="{plan_reasoning_effort}"']
    return args


class CodexAdapter(ProviderAdapter):
    tool = "codex"

    def build_environment(self, context: dict[str, Any]) -> dict[str, str]:
        return {"CODEX_HOME": str(self.registry.codex_home(context["name"], tool=self.tool))}

    def build_argv(self, **kwargs: Any) -> list[str]:
        mode = kwargs["agent_mode"] or ("plan" if kwargs["read_only"] else "auto")
        if mode not in {"plan", "auto"}:
            raise ValueError("Codex mode must be plan or auto")
        if kwargs["read_only"] and mode != "plan":
            raise ValueError("read-only profiles must use Codex Plan mode")
        plan_network = os.getenv("AGCONSOLE_CODEX_PLAN_NETWORK_ACCESS", "").lower() in {
            "1",
            "true",
            "yes",
        }
        sandbox = "workspace-write" if mode == "plan" and plan_network else (
            "read-only" if mode == "plan" else "workspace-write"
        )
        approval = "never" if mode == "plan" else "on-request"
        role = kwargs["role"]
        if mode == "plan":
            role += (
                "\n\nThis Codex session is in Plan mode. Inspect and reason, but do not modify "
                "files or system state. Return an actionable plan for the user to approve."
            )
        argv = [
            str(self.binary),
            "--sandbox",
            sandbox,
            "--ask-for-approval",
            approval,
        ]
        if mode == "plan" and plan_network:
            argv += ["-c", "sandbox_workspace_write.network_access=true"]
        argv += [
            "-C",
            str(kwargs["cwd"]),
            "-c",
            f"developer_instructions={role}",
        ]
        argv += _codex_pin_args(
            model=kwargs.get("model"),
            reasoning_effort=kwargs.get("reasoning_effort"),
            plan_reasoning_effort=kwargs.get("plan_reasoning_effort"),
        )
        return argv

    def login(self, context: dict[str, Any]) -> int:
        env = {**os.environ, **self.build_environment(context)}
        return subprocess.run([str(self.binary), "login", "--device-auth"], env=env).returncode


class CodexProAdapter(CodexAdapter):
    """A separately configured Codex provider using the same Codex CLI semantics."""

    tool = "codex-pro"


class ClaudeAdapter(ProviderAdapter):
    tool = "claude"

    def build_argv(self, **kwargs: Any) -> list[str]:
        args = [str(self.binary), "--append-system-prompt", kwargs["role"]]
        if kwargs["read_only"]:
            args.extend(["--permission-mode", "plan"])
        return args


class OpenCodeAdapter(ProviderAdapter):
    tool = "opencode"

    def build_argv(self, **kwargs: Any) -> list[str]:
        mode = kwargs["agent_mode"] or "plan"
        if mode not in {"plan", "build"}:
            raise ValueError("OpenCode agent mode must be plan or build")
        model = kwargs.get("model")
        if not model:
            raise ValueError("OpenCode model is required")
        return [
            str(self.binary), str(kwargs["cwd"]), "--agent", mode,
            "--model", model, "--auto",
        ]

    def build_launch_spec(self, **kwargs: Any) -> LaunchSpec:
        spec = super().build_launch_spec(**kwargs)
        environment = {
            **spec.environment,
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {"instructions": [str(kwargs["context_path"])]}, separators=(",", ":")
            ),
        }
        return LaunchSpec(spec.argv, environment, spec.secret_files)


class HermesAdapter(ProviderAdapter):
    tool = "hermes"

    def build_launch_spec(self, **kwargs: Any) -> LaunchSpec:
        context = kwargs["context"]
        alias = Path.home() / ".local" / "bin" / f"hermes-agent-console-{kwargs['profile']}"
        environment = {
            "HERMES_INFERENCE_PROVIDER": str(context.get("provider") or "openrouter"),
            "HERMES_INFERENCE_MODEL": "openai/gpt-4o-mini",
            "AGENT_CONSOLE_CONTEXT_FILE": str(kwargs["context_path"]),
        }
        return LaunchSpec(
            [str(alias), "--cli"], environment, self.secret_files(context)
        )

    def build_argv(self, **kwargs: Any) -> list[str]:
        alias = Path.home() / ".local" / "bin" / f"hermes-agent-console-{kwargs['profile']}"
        return [str(alias), "--cli"]


class ShellAdapter(ProviderAdapter):
    tool = "shell"

    def build_argv(self, **kwargs: Any) -> list[str]:
        return [str(self.binary), "-l"]


ADAPTERS = {
    "codex": CodexAdapter,
    "codex-pro": CodexProAdapter,
    "claude": ClaudeAdapter,
    "opencode": OpenCodeAdapter,
    "hermes": HermesAdapter,
    "shell": ShellAdapter,
}


def provider_adapter(tool: str, registry: AuthRegistry) -> ProviderAdapter:
    try:
        return ADAPTERS[tool](registry)
    except KeyError as exc:
        raise ValueError(f"unsupported tool: {tool}") from exc
