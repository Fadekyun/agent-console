"""CommandCode catalogue validation and value-blind host-local provisioning."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_URL = "https://api.commandcode.ai/provider/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
# Native harnesses read the credential from this environment variable; the value
# itself only ever lives in the host-local secret file sourced by the launcher.
COMMANDCODE_ENV_VAR = "CMD_API_KEY"
# Used only when the catalogue omits a usable context_length; the API reports the
# authoritative value for every CommandCode model, so this should rarely apply.
FALLBACK_CONTEXT_WINDOW = 128000
# pi's `maxTokens` is the requested output ceiling. Keep the previously validated
# conservative value for every model rather than guessing per-model output limits.
MAX_OUTPUT_TOKENS = 8192

# Hermes starts CommandCode sessions with this reasoning level. It is only
# written for models in REASONING_MODELS; Hermes' bundled "custom" profile
# turns it into a top-level ``reasoning_effort`` request field.
COMMANDCODE_DEFAULT_REASONING_EFFORT = "high"

# pi >= 0.74 treats a plain `api_key` string as a literal and only resolves the
# explicit ``$NAME`` template form, so every credential reference written for a
# native harness uses that form. The value itself stays in the launcher's
# environment (sourced from the host-local secret file).
PI_API_KEY_REFERENCE = "$" + COMMANDCODE_ENV_VAR

# MCP servers offered to native harnesses. Only the *names* of the environment
# variables are stored here; each generated config carries an interpolation
# reference and the value never leaves the Console process environment. A
# server is emitted only when its token variable is present, so a Console
# without that credential produces no entry rather than a broken one.
MCP_SERVERS: tuple[dict[str, Any], ...] = (
    {
        "name": "n8n",
        "url": "http://192.168.1.73/mcp-server/http",
        "url_env": "N8N_MCP_URL",
        "token_env": "N8N_MCP_TOKEN",
        "request_timeout_ms": 180000,
    },
    {
        "name": "directus",
        "url": "http://192.168.1.71:8055/mcp",
        "url_env": "DIRECTUS_MCP_URL",
        "token_env": "DIRECTUS_MCP_TOKEN",
        "request_timeout_ms": 120000,
    },
)

# CommandCode's OpenAI-compatible endpoint only honours the *top-level*
# ``reasoning_effort`` field and validates it against
# {"low", "medium", "high", "xhigh", "max"}: "none"/"minimal" return HTTP
# 400, while a nested ``reasoning`` object or a ``think`` flag is silently
# ignored. The ``/models`` catalogue carries no capability metadata, so support
# is an explicit, evidence-probed allowlist. Models absent here stay
# non-reasoning: pi does not advertise thinking levels and Hermes does not send
# ``reasoning_effort`` (which would 400 for e.g. the Claude entries).
#
# Probed 2026-09-15 against the live catalogue with the configured key: each
# listed model accepted ``reasoning_effort:"high"`` and returned reasoning
# tokens. Re-probe and update if CommandCode changes its catalogue.
REASONING_MODELS = frozenset({
    "MiniMaxAI/MiniMax-M2.5",
    "MiniMaxAI/MiniMax-M3",
    "Qwen/Qwen3.6-Max-Preview",
    "Qwen/Qwen3.6-Plus",
    "Qwen/Qwen3.7-Flash",
    "Qwen/Qwen3.7-Max",
    "Qwen/Qwen3.7-Plus",
    "Qwen/Qwen3.8-27B",
    "Qwen/Qwen3.8-Flash",
    "Qwen/Qwen3.8-Max",
    "Qwen/Qwen3.8-Max-0902",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-flash-fast",
    "deepseek/deepseek-v4-flash-vision-exp",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4.1-flash",
    "google/gemini-3.7-flash",
    "google/gemini-3.8-flash",
    "inclusionai/ling-3.0-flash-sante:free",
    "meituan/LongCat-2.0:free",
    "meta/muse-spark-1.2",
    "meta/muse-spark-1.2-contributor",
    "meta/muse-spark-1.3",
    "meta/muse-spark-1.3-contributor",
    "moonshotai/Kimi-K2.6",
    "moonshotai/Kimi-K2.7-Code",
    "moonshotai/Kimi-K2.7-Code-Highspeed",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "stepfun/Step-3.7-Flash",
    "tencent/hy3-paid",
    "tencent/hy4-preview",
    "thinkingmachines/inkling",
    "thinkingmachines/inkling-small",
    "xai/grok-4.5",
    "xai/grok-4.6",
    "xiaomi/mimo-v2.5",
    "xiaomi/mimo-v2.5-pro",
    "z-ai/glm-5.3-flash",
    "zai-org/GLM-5.1",
    "zai-org/GLM-5.2",
    "zai-org/GLM-5.3",
})

# pi thinking levels hidden/mapped for CommandCode. ``minimal`` is dropped
# because the endpoint 400s on it; ``off`` stays unmapped (pi sends nothing and
# the model keeps its own default); ``xhigh`` is mapped explicitly so the level
# is offered at all (pi only lists xhigh when the map defines it).
REASONING_THINKING_LEVEL_MAP = {"minimal": None, "xhigh": "xhigh"}


def supports_reasoning(model_id: str | None) -> bool:
    """Return True when CommandCode accepts reasoning_effort for *model_id*."""
    return bool(model_id) and model_id in REASONING_MODELS


def _positive_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0 and value.is_integer():
        return int(value)
    return fallback


def _catalogue_entry(raw: dict[str, Any]) -> dict[str, Any]:
    model_id = raw["id"]
    name = raw.get("name")
    return {
        "id": model_id,
        "name": name if isinstance(name, str) and name.strip() else model_id,
        "context_length": _positive_int(raw.get("context_length"), FALLBACK_CONTEXT_WINDOW),
    }


def selected_model(context: dict, model: str | None = None) -> str:
    if context.get("provider") != "commandcode" or context.get("base_url") != BASE_URL:
        raise ValueError("Pi/Hermes requires a verified CommandCode context")
    selected = model or context.get("model")
    if not context.get("verified") or not selected or selected not in context.get("models", []):
        raise ValueError("model is not in the authenticated CommandCode catalogue; rerun provisioning")
    return selected


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".commandcode-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def session_mcp_servers(environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return the MCP servers this Console can offer a native session.

    A server qualifies only when its token variable is present in the Console
    process environment. The optional URL variable lets a deployment point at a
    different endpoint; only the resolved URL and variable names are returned.
    Values are never read into the result.
    """
    env = os.environ if environ is None else environ
    servers: list[dict[str, Any]] = []
    for server in MCP_SERVERS:
        if not env.get(server["token_env"]):
            continue
        servers.append({**server, "url": env.get(server["url_env"]) or server["url"]})
    return servers


def pi_mcp_config(servers: list[dict[str, Any]]) -> dict[str, Any]:
    """Build pi's per-session `mcp.json` (the highest-precedence pi config)."""
    return {"mcpServers": {
        server["name"]: {
            "url": server["url"],
            "auth": "bearer",
            "bearerTokenEnv": server["token_env"],
            "lifecycle": "lazy",
            "requestTimeoutMs": _positive_int(server.get("request_timeout_ms"), 120000),
        }
        for server in servers
    }}


def hermes_mcp_servers(servers: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the Hermes `mcp_servers` block, using ``${VAR}`` references."""
    return {
        server["name"]: {
            "url": server["url"],
            "headers": {"Authorization": "Bearer ${" + server["token_env"] + "}"},
        }
        for server in servers
    }


def ensure_pi_auth_env_reference(path: Path) -> None:
    """Keep pi's native `auth.json` pointed at the env var, never a literal key.

    pi >= 0.74 resolves an `api_key` entry through `resolveConfigValue`, which
    expands the explicit ``$NAME`` template form from the process environment
    and otherwise treats the string as a literal. A previously stored literal is
    replaced while unrelated providers are kept.
    """
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            data = existing
    data["commandcode"] = {"type": "api_key", "key": PI_API_KEY_REFERENCE}
    write_private_json(path, data)


def catalogue(key: str) -> list[dict[str, Any]]:
    """Return the authenticated catalogue with id, display name and context length."""
    if not re.fullmatch(r"[A-Za-z0-9_.=-]{20,256}", key):
        raise ValueError("credential format invalid")
    req = urllib.request.Request(BASE_URL + "/models", headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json", "User-Agent": "agent-console-commandcode/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"CommandCode catalogue HTTP {exc.code}") from None
    except (OSError, ValueError):
        raise RuntimeError("CommandCode catalogue request failed") from None
    models = [
        _catalogue_entry(m)
        for m in data.get("data", [])
        if isinstance(m, dict) and isinstance(m.get("id"), str)
    ]
    if DEFAULT_MODEL not in [model["id"] for model in models]:
        raise ValueError(f"requested model unavailable: {DEFAULT_MODEL}; no configuration written")
    return models


def pi_model_entries(
    context: dict[str, Any], *, selected: str | None = None,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> list[dict[str, Any]]:
    """Build every pi `models.json` entry so `/model` offers the whole catalogue.

    Contexts provisioned before catalogue metadata was stored only carry model ids;
    those fall back to `FALLBACK_CONTEXT_WINDOW` until provisioning is rerun.
    """
    raw_entries = context.get("model_catalogue")
    if not isinstance(raw_entries, list) or not raw_entries:
        raw_entries = [{"id": model_id} for model_id in context.get("models", [])]
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        model_id = raw["id"]
        if model_id in seen:
            continue
        seen.add(model_id)
        name = raw.get("name")
        entries.append(_pi_model_entry(
            model_id,
            name if isinstance(name, str) and name.strip() else model_id,
            _positive_int(raw.get("context_length"), FALLBACK_CONTEXT_WINDOW),
            max_tokens,
        ))
    if selected and selected not in seen:
        entries.append(_pi_model_entry(selected, selected, FALLBACK_CONTEXT_WINDOW, max_tokens))
    return entries


def install_hermes_reasoning_gate(root: Path) -> None:
    """Install the model-aware reasoning gate into a Hermes session home.

    Hermes keeps ``agent.reasoning_effort`` for the whole session, so the
    gate re-checks the current request's model against the allowlist and drops
    the field for models that would reject it (e.g. after a ``/model`` switch).
    """
    source = Path(__file__).resolve().parent / "hermes_provider_plugins" / "commandcode"
    target = root / "plugins" / "model-providers" / "commandcode"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    for name in ("__init__.py", "plugin.yaml"):
        destination = target / name
        shutil.copyfile(source / name, destination)
        destination.chmod(0o600)
    write_private_json(root / "reasoning-models.json", {
        "models": sorted(REASONING_MODELS),
        "default_effort": COMMANDCODE_DEFAULT_REASONING_EFFORT,
    })


def _pi_model_entry(model_id: str, name: str, context_window: int, max_tokens: int) -> dict[str, Any]:
    """Build one pi model entry, advertising thinking only when supported."""
    entry: dict[str, Any] = {
        "id": model_id,
        "name": name,
        "contextWindow": context_window,
        "maxTokens": max_tokens,
        "compat": {"supportsDeveloperRole": False},
    }
    if supports_reasoning(model_id):
        entry["reasoning"] = True
        entry["thinkingLevelMap"] = dict(REASONING_THINKING_LEVEL_MAP)
    return entry


def provision(config_dir: Path, key: str) -> dict:
    # The availability gate runs before creating a registry or touching credentials.
    models = catalogue(key)
    from .auth import AuthRegistry
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = config_dir / "backups" / ("commandcode-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    for path in (config_dir / "auth-contexts.json", config_dir / "secrets.d/commandcode-main.env"):
        if path.exists():
            target = backup / path.name
            shutil.copy2(path, target)
            target.chmod(0o600)
    registry = AuthRegistry(config_dir)
    secret = registry.secret_path("commandcode-main")
    fd, tmp = tempfile.mkstemp(dir=secret.parent, prefix=".commandcode-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("export " + COMMANDCODE_ENV_VAR + "=" + shlex.quote(key) + "\n")
        os.replace(tmp, secret)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    data = registry._read()
    for tool in ("pi", "hermes"):
        data.setdefault("contexts", {}).setdefault(tool, {})["commandcode-main"] = {
            "provider": "commandcode", "kind": "api-key", "secret_ref": "commandcode-main",
            "base_url": BASE_URL, "model": DEFAULT_MODEL,
            "models": [model["id"] for model in models], "model_catalogue": models,
            "enabled": True, "verified": True, "catalogue_verified_at": stamp,
        }
        data["defaults"][tool] = "commandcode-main"
    registry._write(data)
    return {"model": DEFAULT_MODEL, "catalogue_models": len(models), "backup": str(backup), "secret_mode": "0600"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Pi/Hermes from a CommandCode key on stdin; never logs its value")
    parser.add_argument("--config-dir", type=Path, default=Path.home() / ".config/agent-console")
    args = parser.parse_args()
    try:
        result = provision(args.config_dir, sys.stdin.read().strip())
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
