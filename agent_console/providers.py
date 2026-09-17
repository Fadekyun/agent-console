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
    "pi": _resolve_binary("AGCONSOLE_PI_BIN", "pi"),
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

    @property
    def can_run_planning_task(self) -> bool:
        return False

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

    @property
    def can_run_planning_task(self) -> bool:
        return True

    def build_planning_task_argv(
        self,
        *,
        cwd: Path,
        output_schema: Path,
        final_output: Path,
    ) -> list[str]:
        """Build the fixed native non-interactive planning command.

        Prompt delivery is deliberately absent here: the runner writes it to stdin once.
        """
        return [
            str(self.binary),
            "-c", 'approval_policy="never"',
            "exec",
            "--json",
            "--sandbox", "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--output-schema", str(output_schema),
            "--output-last-message", str(final_output),
            "-C", str(cwd),
            "-",
        ]

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


class CommandCodeAdapter(ProviderAdapter):
    """Native harness configuration contains references, never credential values."""

    def build_launch_spec(self, **kwargs: Any) -> LaunchSpec:
        from .commandcode import (COMMANDCODE_DEFAULT_REASONING_EFFORT, COMMANDCODE_ENV_VAR,
                                 PI_API_KEY_REFERENCE, ensure_pi_auth_env_reference,
                                 hermes_mcp_servers, install_hermes_reasoning_gate,
                                 pi_mcp_config, pi_model_entries, selected_model,
                                 session_mcp_servers, supports_reasoning, write_private_json)

        context = kwargs["context"]
        model = selected_model(context, kwargs.get("model"))
        context_path = kwargs["context_path"]
        root = context_path.parent / (context_path.stem + "-" + self.tool)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = {"AGENT_CONSOLE_CONTEXT_FILE": str(context_path)}
        # MCP entries are generated per session from variable names only, so a
        # session never depends on a host-global hand-edited harness config.
        mcp_servers = session_mcp_servers()
        if self.tool == "pi":
            write_private_json(root / "models.json", {"providers": {"commandcode": {
                "baseUrl": context["base_url"], "api": "openai-completions",
                "apiKey": PI_API_KEY_REFERENCE, "authHeader": True,
                "headers": {"User-Agent": "agent-console-commandcode/1.0"},
                "models": pi_model_entries(context, selected=model),
            }}})
            ensure_pi_auth_env_reference(root / "auth.json")
            if mcp_servers:
                write_private_json(root / "mcp.json", pi_mcp_config(mcp_servers))
            environment["PI_CODING_AGENT_DIR"] = str(root)
            argv = [str(self.binary), "--provider", "commandcode", "--model", model,
                    "--append-system-prompt", str(context_path)]
        else:
            hermes_config: dict[str, Any] = {
                "model": {"provider": "custom", "default": model,
                          "base_url": context["base_url"],
                          "api_key": "${" + COMMANDCODE_ENV_VAR + "}"},
            }
            if supports_reasoning(model):
                # CommandCode only honours a top-level reasoning_effort; Hermes'
                # bundled "custom" profile emits it from agent.reasoning_effort.
                # Non-reasoning models must omit it (the endpoint 400s for them).
                hermes_config["agent"] = {
                    "reasoning_effort": COMMANDCODE_DEFAULT_REASONING_EFFORT,
                }
            if mcp_servers:
                hermes_config["mcp_servers"] = hermes_mcp_servers(mcp_servers)
            write_private_json(root / "config.yaml", hermes_config)
            # Gate the session-level effort by the current request's model so a
            # /model switch to an unsupported model cannot inherit it (HTTP 400).
            install_hermes_reasoning_gate(root)
            environment["HERMES_HOME"] = str(root)
            argv = [str(self.binary), "chat", "--cli", "--provider", "custom", "--model", model]
        return LaunchSpec(argv, environment, self.secret_files(context))

    def build_argv(self, **kwargs: Any) -> list[str]:
        return self.build_launch_spec(**kwargs).argv


class HermesAdapter(CommandCodeAdapter):
    tool = "hermes"


class PiAdapter(CommandCodeAdapter):
    tool = "pi"


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
    "pi": PiAdapter,
    "shell": ShellAdapter,
}


def provider_adapter(tool: str, registry: AuthRegistry) -> ProviderAdapter:
    try:
        return ADAPTERS[tool](registry)
    except KeyError as exc:
        raise ValueError(f"unsupported tool: {tool}") from exc
