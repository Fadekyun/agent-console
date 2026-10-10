from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
from functools import lru_cache
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


@lru_cache(maxsize=32)
def _supports_native_flag(binary, mtime, size, command, flag):
    try:
        result = subprocess.run(
            [binary, command, "--help"] if command else [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and flag in result.stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _require_native_flag(binary: Path, *, command: str | None, flag: str) -> None:
    try:
        stat = binary.stat()
    except OSError as exc:
        raise RuntimeError(f"Codex Pro native approval support is unavailable: {binary}") from exc
    if not _supports_native_flag(str(binary), stat.st_mtime_ns, stat.st_size, command, flag):
        location = f"{command} --help" if command else "--help"
        raise RuntimeError(
            f"Codex Pro requires native {flag} support; installed CLI does not advertise "
            f"{flag} in {location}"
        )


@lru_cache(maxsize=32)
def _workflow_probe(binary,mtime,size,required_flags=()):
    try:
        help_result=subprocess.run([binary,'exec','--help'],capture_output=True,text=True,timeout=5,check=False)
        version=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=5,check=False)
        if help_result.returncode or not all(flag in help_result.stdout for flag in ['--output-schema','--output-last-message','--sandbox', *required_flags]):
            return {'supported':False,'reason':'installed CLI lacks the required native task flags'}
        return {'supported':True,'version':version.stdout.strip()[:100] if not version.returncode else 'unknown','binary':binary}
    except (OSError,subprocess.TimeoutExpired):return {'supported':False,'reason':'native task capability probe failed or timed out'}


@dataclass(frozen=True)
class LaunchSpec:
    argv: list[str]
    environment: dict[str, str]
    secret_files: list[Path]
    resolved_environment: dict[str, str] | None = None
    environment_revision: int | None = None


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
    def can_run_workflow_task(self) -> bool:
        return False

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

    def configure_shared_skills(
        self,
        *,
        environment: dict[str, str],
        isolated_skills_root: Path,
    ) -> None:
        """Materialize the session's shared-skill discovery for this provider.

        Called on both session create and restart. Implementations must merge
        into any existing provider configuration without removing unrelated
        settings. Codex and Codex Pro discover the isolated root through the
        session overlay symlink, so only providers with an explicit config file
        need to act.
        """

    def restart(self, session: dict[str, Any]) -> None:
        """Prepare a provider before its pinned launcher is respawned."""

    def resume(self, session: dict[str, Any]) -> None:
        """Prepare a provider before the shared tmux session is attached."""

    def doctor(self, context_name: str | None = None) -> dict[str, Any]:
        context = self.auth_status(context_name)
        return {"ok": context["status"] == "ready", "context": context}


def normalize_codex_automatic_review_argv(argv: list[str]) -> list[str]:
    """Native automatic review owns workspace-write; reject broader/narrower overrides."""
    if "--approve-for-me" not in argv:
        return list(argv)
    result = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"--sandbox", "-s"}:
            if index + 1 >= len(argv) or argv[index + 1] != "workspace-write":
                raise ValueError("automatic review requires workspace-write sandbox semantics")
            index += 2
            continue
        if arg.startswith("--sandbox="):
            if arg != "--sandbox=workspace-write":
                raise ValueError("automatic review requires workspace-write sandbox semantics")
            index += 1
            continue
        result.append(arg)
        index += 1
    return result


def normalize_codex_automatic_review_launcher(text: str) -> str:
    """Migrate only the pinned exec argv; preserve environment, resume and prompt values."""
    prefix, separator, command = text.rpartition("\nexec ")
    if not separator:
        return text
    argv = shlex.split(command)
    normalized = normalize_codex_automatic_review_argv(argv)
    if normalized == argv:
        return text
    return prefix + separator + shlex.join(normalized) + "\n"


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
    def can_run_workflow_task(self) -> bool:
        return True

    def workflow_info(self):
        try:stat=self.binary.stat()
        except OSError:return {'supported':False,'reason':'tool launcher is missing'}
        return _workflow_probe(str(self.binary),stat.st_mtime_ns,stat.st_size)

    def workflow_argv(self, interactive_argv, *, schema, output, read_only):
        argv=list(interactive_argv)
        # Read-only roles stay read-only even when interactive Plan sessions opt
        # into network access. Codex Pro writable workflow tasks retain its
        # explicit native automatic-review default; other unattended tasks
        # never approve new permissions.
        if self.tool == "codex-pro" and not read_only:
            _require_native_flag(self.binary, command="exec", flag="--approve-for-me")
            if "--approve-for-me" not in argv:
                approval_index = argv.index('--ask-for-approval')
                del argv[approval_index:approval_index + 2]
                argv.append('--approve-for-me')
        else:
            # A read action may start from a writable profile's auto argv.
            # Native automatic review implies workspace-write, so remove it
            # before applying the narrower workflow boundary.
            argv = [arg for arg in argv if arg != '--approve-for-me']
            if '--ask-for-approval' in argv:
                argv[argv.index('--ask-for-approval')+1]='never'
            else:
                argv += ['--ask-for-approval', 'never']
        sandbox = 'read-only' if read_only else 'workspace-write'
        if '--sandbox' in argv:
            argv[argv.index('--sandbox') + 1] = sandbox
        else:
            argv += ['--sandbox', sandbox]
        argv = normalize_codex_automatic_review_argv(argv)
        for index in range(len(argv)-2,-1,-1):
            if argv[index:index+2]==['-c','sandbox_workspace_write.network_access=true']:
                del argv[index:index+2]
        return argv+['exec','--skip-git-repo-check','--output-schema',str(schema),'--output-last-message',str(output),'-']

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

    def workflow_info(self):
        try:
            stat = self.binary.stat()
        except OSError:
            return {'supported': False, 'reason': 'tool launcher is missing'}
        return _workflow_probe(
            str(self.binary), stat.st_mtime_ns, stat.st_size,
            required_flags=('--approve-for-me',),
        )

    def build_argv(self, **kwargs: Any) -> list[str]:
        mode = kwargs["agent_mode"] or ("plan" if kwargs["read_only"] else "auto")
        argv = super().build_argv(**kwargs)
        if mode == "auto":
            _require_native_flag(self.binary, command=None, flag="--approve-for-me")
            approval_index = argv.index("--ask-for-approval")
            del argv[approval_index:approval_index + 2]
            argv.append("--approve-for-me")
            argv = normalize_codex_automatic_review_argv(argv)
        return argv


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

    def configure_shared_skills(
        self,
        *,
        environment: dict[str, str],
        isolated_skills_root: Path,
    ) -> None:
        # Both paths point to the immutable Console-selected snapshot. Other
        # native project/plugin sources remain the harness's responsibility.
        if self.tool == "pi":
            agent_dir = environment.get("PI_CODING_AGENT_DIR")
            if not agent_dir:
                raise ValueError("Pi skill delivery requires its per-session agent directory")
            target = Path(agent_dir) / "skills"
            if target.is_symlink() and target.resolve() == isolated_skills_root.resolve():
                return
            if target.exists() or target.is_symlink():
                raise ValueError("Pi skills directory already exists; preserve it and review delivery")
            target.symlink_to(isolated_skills_root, target_is_directory=True)
            return
        if self.tool != "hermes":
            return
        hermes_home = environment.get("HERMES_HOME")
        if not hermes_home:
            # Legacy Hermes launchers predate the per-session home; there is no
            # config to update and no unintended exposure from leaving it alone.
            return
        from .commandcode import merge_hermes_skill_dirs

        merge_hermes_skill_dirs(Path(hermes_home) / "config.yaml", isolated_skills_root)

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
        mcp_servers = session_mcp_servers(kwargs.get("resolved_environment"))
        if self.tool == "pi":
            write_private_json(root / "models.json", {"providers": {"commandcode": {
                "baseUrl": context["base_url"], "api": "openai-completions",
                "apiKey": PI_API_KEY_REFERENCE, "authHeader": True,
                "headers": {"User-Agent": "agent-console-commandcode/1.0"},
                "models": pi_model_entries(context, selected=model),
            }}})
            ensure_pi_auth_env_reference(root / "auth.json")
            per_session_mcp = pi_mcp_config(mcp_servers)
            write_private_json(root / "mcp.json", per_session_mcp)
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
