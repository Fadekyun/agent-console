from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


CONTEXT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REGISTRY_VERSION = 1


def validate_context_name(value: str) -> str:
    if not CONTEXT_NAME.fullmatch(value or ""):
        raise ValueError("context names must use 1-64 letters, numbers, dot, dash, or underscore")
    return value


def default_registry() -> dict[str, Any]:
    return {
        "version": REGISTRY_VERSION,
        "defaults": {
            "codex": "default",
            "codex-pro": "default",
            "claude": "default",
            "opencode": "opencode-go-default",
            "hermes": "openrouter-main",
            "shell": "default",
        },
        "contexts": {
            "codex": {
                "default": {
                    "provider": "openai",
                    "kind": "oauth-native",
                    "source_ref": "codex/default",
                    "enabled": True,
                    "verified": False,
                }
            },
            "codex-pro": {
                "default": {
                    "provider": "openai",
                    "kind": "oauth-native",
                    "source_ref": "codex-pro/default",
                    "enabled": True,
                    "verified": False,
                }
            },
            "claude": {
                "default": {
                    "provider": "anthropic",
                    "kind": "oauth-native",
                    "source_ref": "claude/default",
                    "enabled": False,
                    "verified": False,
                    "disabled_reason": "subscription inactive",
                }
            },
            "opencode": {
                "openrouter-main": {
                    "provider": "openrouter",
                    "kind": "api-key",
                    "secret_ref": "openrouter-main",
                    "enabled": True,
                    "verified": True,
                },
                "opencode-go-default": {
                    "provider": "opencode-go",
                    "kind": "oauth-native",
                    "source_ref": "opencode/provider-native",
                    "enabled": True,
                    "verified": True,
                },
                "opencode-zen-default": {
                    "provider": "opencode",
                    "kind": "oauth-native",
                    "source_ref": "opencode/provider-native",
                    "enabled": True,
                    "verified": True,
                },
            },
            "hermes": {
                "openrouter-main": {
                    "provider": "openrouter",
                    "kind": "api-key",
                    "secret_ref": "openrouter-main",
                    "source_ref": "hermes/default",
                    "enabled": True,
                    "verified": False,
                }
            },
            "shell": {
                "default": {
                    "provider": "local",
                    "kind": "none",
                    "enabled": True,
                    "verified": True,
                }
            },
        },
    }


class AuthRegistry:
    def __init__(self, config_dir: Path, *, home: Path | None = None):
        self.config_dir = config_dir.expanduser()
        self.home = (home or Path.home()).expanduser()
        self.registry_path = self.config_dir / "auth-contexts.json"
        self.secrets_dir = self.config_dir / "secrets.d"
        self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_dir.chmod(0o700)
        self.secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.secrets_dir.chmod(0o700)
        if not self.registry_path.exists():
            self._write(default_registry())
        self._ensure_builtin_contexts()

    def _ensure_builtin_contexts(self) -> None:
        data = self._read()
        codex_pro_contexts = data.setdefault("contexts", {}).setdefault("codex-pro", {})
        dirty = False
        if "default" not in codex_pro_contexts:
            codex_pro_contexts["default"] = {
                "provider": "openai",
                "kind": "oauth-native",
                "source_ref": "codex-pro/default",
                "enabled": True,
                "verified": False,
            }
            dirty = True
        defaults = data.setdefault("defaults", {})
        if not defaults.get("codex-pro"):
            defaults["codex-pro"] = "default"
            dirty = True
        contexts = data.setdefault("contexts", {}).setdefault("opencode", {})
        
        # Migrate old opencode-go-default (was mapping to opencode/ZEN)
        old_entry = contexts.get("opencode-go-default")
        if old_entry and old_entry.get("provider") == "opencode":
            # Rename to opencode-zen-default
            contexts["opencode-zen-default"] = old_entry.copy()
            del contexts["opencode-go-default"]
            dirty = True
        
        # Ensure opencode-go-default exists (GO, paid)
        if "opencode-go-default" not in contexts:
            contexts["opencode-go-default"] = {
                "provider": "opencode-go",
                "kind": "oauth-native",
                "source_ref": "opencode/provider-native",
                "enabled": True,
                "verified": True,
            }
            dirty = True
        
        # Ensure opencode-zen-default exists (ZEN, free)
        if "opencode-zen-default" not in contexts:
            contexts["opencode-zen-default"] = {
                "provider": "opencode",
                "kind": "oauth-native",
                "source_ref": "opencode/provider-native",
                "enabled": True,
                "verified": True,
            }
            dirty = True
        
        # Update default to opencode-go-default
        defaults = data.setdefault("defaults", {})
        if defaults.get("opencode") != "opencode-go-default":
            defaults["opencode"] = "opencode-go-default"
            dirty = True
        
        if dirty:
            self._write(data)

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if data.get("version") != REGISTRY_VERSION:
            raise ValueError("unsupported authentication registry version")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".auth-contexts.", dir=self.config_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.registry_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def secret_path(self, credential: str) -> Path:
        return self.secrets_dir / f"{validate_context_name(credential)}.env"

    def codex_home(self, context: str, *, tool: str = "codex") -> Path:
        context = validate_context_name(context)
        if tool not in {"codex", "codex-pro"}:
            raise ValueError(f"{tool} does not use a Codex native store")
        path = self.config_dir / tool / context
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
        skills = self.home / ".codex" / "skills"
        context_skills = path / "skills"
        if skills.is_dir() and not context_skills.exists():
            context_skills.symlink_to(skills, target_is_directory=True)
        return path

    def list_contexts(self, tool: str | None = None) -> list[dict[str, Any]]:
        data = self._read()
        tools = [tool] if tool else sorted(data["contexts"])
        result: list[dict[str, Any]] = []
        for tool_name in tools:
            for name, context in sorted(data["contexts"].get(tool_name, {}).items()):
                result.append(self._public(tool_name, name, context, data))
        return result

    def _public(
        self,
        tool: str,
        name: str,
        context: dict[str, Any],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = data or self._read()
        status, reason = self._status(tool, name, context)
        return {
            "tool": tool,
            "name": name,
            "provider": context.get("provider"),
            "kind": context.get("kind"),
            "enabled": bool(context.get("enabled", True)),
            "verified": bool(context.get("verified", False)),
            "default": data.get("defaults", {}).get(tool) == name,
            "status": status,
            "reason": reason,
        }

    def _status(self, tool: str, name: str, context: dict[str, Any]) -> tuple[str, str | None]:
        if not context.get("enabled", True):
            return "disabled", context.get("disabled_reason") or "context disabled"
        if tool in {"codex", "codex-pro"}:
            auth_file = self.codex_home(name, tool=tool) / "auth.json"
            return ("ready", None) if auth_file.is_file() else (
                "setup-required",
                f"run agentctl auth login {tool} --context {name}",
            )
        secret_ref = context.get("secret_ref")
        if secret_ref:
            secret = self.secret_path(secret_ref)
            if not secret.is_file():
                return "setup-required", f"credential {secret_ref} is not configured"
            if secret.stat().st_mode & 0o077:
                return "error", f"credential {secret_ref} permissions are too broad"
        if tool == "hermes" and not context.get("verified", False):
            return "setup-required", "Hermes context is configured but still under acceptance testing"
        return "ready", None

    def get_context(self, tool: str, name: str | None = None, *, require_ready: bool = False) -> dict[str, Any]:
        data = self._read()
        selected = name or data.get("defaults", {}).get(tool)
        if not selected:
            raise KeyError(f"no default authentication context for {tool}")
        validate_context_name(selected)
        context = data.get("contexts", {}).get(tool, {}).get(selected)
        if context is None:
            raise KeyError(f"authentication context not found: {tool}/{selected}")
        public = self._public(tool, selected, context, data)
        if require_ready and public["status"] != "ready":
            raise RuntimeError(f"{tool}/{selected} is {public['status']}: {public['reason']}")
        return {"tool": tool, "name": selected, **context, **public}

    def add_context(
        self,
        tool: str,
        name: str,
        *,
        provider: str,
        kind: str,
        secret_ref: str | None = None,
        source_ref: str | None = None,
        enabled: bool = True,
        verified: bool = False,
        make_default: bool = False,
    ) -> dict[str, Any]:
        name = validate_context_name(name)
        if secret_ref:
            validate_context_name(secret_ref)
        data = self._read()
        context: dict[str, Any] = {
            "provider": provider,
            "kind": kind,
            "enabled": enabled,
            "verified": verified,
        }
        if secret_ref:
            context["secret_ref"] = secret_ref
        if source_ref:
            context["source_ref"] = source_ref
        data.setdefault("contexts", {}).setdefault(tool, {})[name] = context
        if make_default or not data.setdefault("defaults", {}).get(tool):
            data["defaults"][tool] = name
        self._write(data)
        return self.get_context(tool, name)

    def set_default(self, tool: str, name: str) -> dict[str, Any]:
        context = self.get_context(tool, name)
        data = self._read()
        data.setdefault("defaults", {})[tool] = name
        self._write(data)
        return {**context, "default": True}

    def disable(self, tool: str, name: str, reason: str = "context disabled") -> dict[str, Any]:
        self.get_context(tool, name)
        data = self._read()
        data["contexts"][tool][name]["enabled"] = False
        data["contexts"][tool][name]["disabled_reason"] = reason
        self._write(data)
        return self.get_context(tool, name)

    def catalog(self) -> list[dict[str, Any]]:
        contexts = self.list_contexts()
        result = []
        for tool in sorted({item["tool"] for item in contexts}):
            candidates = [item for item in contexts if item["tool"] == tool]
            default = next((item for item in candidates if item["default"]), candidates[0])
            result.append(
                {
                    "name": tool,
                    "status": default["status"],
                    "reason": default["reason"],
                    "default_context": default["name"],
                    "contexts": candidates,
                }
            )
        return result

    def doctor(self) -> dict[str, Any]:
        problems: list[str] = []
        data = self._read()
        if self.registry_path.stat().st_mode & 0o077:
            problems.append("authentication registry permissions are too broad")
        for tool, default in data.get("defaults", {}).items():
            if default not in data.get("contexts", {}).get(tool, {}):
                problems.append(f"default context is missing: {tool}/{default}")
        for item in self.list_contexts():
            if item["status"] == "error":
                problems.append(f"{item['tool']}/{item['name']}: {item['reason']}")
        return {
            "ok": not problems,
            "problems": problems,
            "contexts": self.list_contexts(),
            "registry_mode": f"{self.registry_path.stat().st_mode & 0o777:04o}",
        }
