from __future__ import annotations

import getpass
import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any


OPENROUTER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{20,200}$")


def openrouter_path(home: Path | None = None, credential: str = "openrouter-main") -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", credential):
        raise ValueError("credential name is invalid")
    return (home or Path.home()) / ".config" / "agent-console" / "secrets.d" / f"{credential}.env"


def set_openrouter_secret(
    home: Path | None = None,
    credential: str = "openrouter-main",
) -> dict[str, Any]:
    first = getpass.getpass("New OpenRouter key: ")
    second = getpass.getpass("Confirm OpenRouter key: ")
    if first != second:
        raise ValueError("key entries do not match")
    if not OPENROUTER_PATTERN.fullmatch(first):
        raise ValueError("key format is invalid")

    target = openrouter_path(home, credential)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=".secrets.env.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("export OPENROUTER_API_KEY=" + shlex.quote(first) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"configured": True, "credential": credential, "path": str(target), "mode": "0600"}


def migrate_openrouter_secret(home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    legacy = home / ".config" / "opencode" / "secrets.env"
    target = openrouter_path(home)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    if target.exists() and legacy.exists() and not legacy.is_symlink():
        raise FileExistsError("both legacy and canonical OpenRouter credential files exist")
    migrated = False
    if not target.exists() and legacy.is_file():
        os.replace(legacy, target)
        target.chmod(0o600)
        migrated = True
    if target.exists():
        legacy.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if legacy.is_symlink() and legacy.resolve() != target.resolve():
            legacy.unlink()
        if not legacy.exists():
            legacy.symlink_to(target)
    return {
        "configured": target.is_file(),
        "credential": "openrouter-main",
        "migrated": migrated,
        "legacy_compatibility_link": legacy.is_symlink(),
        "mode": f"{target.stat().st_mode & 0o777:04o}" if target.exists() else None,
    }


def secret_status(home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    target = openrouter_path(home)
    legacy = home / ".config" / "opencode" / "secrets.env"
    configured = target.is_file() and "OPENROUTER_API_KEY=" in target.read_text(
        encoding="utf-8", errors="ignore"
    )
    mode = f"{target.stat().st_mode & 0o777:04o}" if target.exists() else None
    duplicate_sources: list[str] = []
    for candidate in (home / ".zshrc", home / ".hermes" / ".env"):
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(?m)^\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*\S+", text):
            duplicate_sources.append(str(candidate))
    hermes_config = home / ".hermes" / "config.yaml"
    if hermes_config.is_file():
        text = hermes_config.read_text(encoding="utf-8", errors="ignore")
        model = re.search(r"(?ms)^model:\s*\n(.*?)(?=^\S|\Z)", text)
        api_key = re.search(r"(?m)^\s+api_key:\s*(.*?)\s*$", model.group(1)) if model else None
        value = api_key.group(1).strip().strip("'\"") if api_key else ""
        if value:
            duplicate_sources.append(str(hermes_config))
    return {
        "openrouter": {
            "configured": configured,
            "credential": "openrouter-main",
            "path": str(target),
            "mode": mode,
            "permissions_ok": mode == "0600" if mode else False,
            "consumers": ["opencode", "hermes"],
            "single_source_ok": configured and not duplicate_sources,
            "duplicate_sources": duplicate_sources,
            "legacy_compatibility_link": legacy.is_symlink()
            and legacy.resolve() == target.resolve(),
        }
    }
