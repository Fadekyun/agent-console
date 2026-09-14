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

BASE_URL = "https://api.commandcode.ai/provider/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


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


def catalogue(key: str) -> list[str]:
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
    models = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if DEFAULT_MODEL not in models:
        raise ValueError(f"requested model unavailable: {DEFAULT_MODEL}; no configuration written")
    return models


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
            f.write("export CMD_API_KEY=" + shlex.quote(key) + "\n")
        os.replace(tmp, secret)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    data = registry._read()
    for tool in ("pi", "hermes"):
        data.setdefault("contexts", {}).setdefault(tool, {})["commandcode-main"] = {
            "provider": "commandcode", "kind": "api-key", "secret_ref": "commandcode-main",
            "base_url": BASE_URL, "model": DEFAULT_MODEL, "models": models,
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
