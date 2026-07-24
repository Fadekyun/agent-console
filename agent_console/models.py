from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PROVIDERS = {"openrouter", "opencode", "opencode-go"}

# Preferred models by provider (fallback to cheapest flash model if not found)
PREFERRED_MODELS = {
    "opencode-go": "opencode-go/deepseek-v4-flash",
    "opencode": "opencode/big-pickle",
}


def preferred_model(models: list[dict[str, Any]], provider: str) -> dict[str, Any]:
    """Return the preferred model for a provider, falling back to cheapest flash model."""
    selectable = [model for model in models if model.get("selectable")]
    if not selectable:
        raise ValueError("model catalogue has no selectable models")
    
    # Try preferred model first
    preferred_id = PREFERRED_MODELS.get(provider)
    if preferred_id:
        for model in selectable:
            if model["model"] == preferred_id:
                return model
    
    # Fall back to cheapest model with "flash" in the name
    flash_models = [m for m in selectable if "flash" in m.get("id", "").lower()]
    if flash_models:
        return min(flash_models, key=model_cost_key)
    
    # Final fallback: cheapest model
    return min(selectable, key=model_cost_key)


def parse_verbose_models(output: str, provider: str) -> list[dict[str, Any]]:
    if provider not in PROVIDERS:
        raise ValueError("provider must be openrouter or opencode")
    decoder = json.JSONDecoder()
    models: list[dict[str, Any]] = []
    offset = 0
    while offset < len(output):
        start = output.find("{", offset)
        if start < 0:
            break
        try:
            raw, consumed = decoder.raw_decode(output[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        offset = start + consumed
        if not isinstance(raw, dict) or raw.get("providerID") != provider or not raw.get("id"):
            continue
        cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
        cache = cost.get("cache") if isinstance(cost.get("cache"), dict) else {}
        limits = raw.get("limit") if isinstance(raw.get("limit"), dict) else {}
        capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
        status = str(raw.get("status") or "active")
        models.append(
            {
                "id": str(raw["id"]),
                "model": f"{provider}/{raw['id']}",
                "provider": provider,
                "name": str(raw.get("name") or raw["id"]),
                "status": status,
                "selectable": status not in {"deprecated", "unavailable"},
                "cost": {
                    "input": _number(cost.get("input")),
                    "output": _number(cost.get("output")),
                    "cache_read": _number(cache.get("read")),
                    "reasoning": _number(cost.get("reasoning")),
                },
                "limits": {
                    "context": _integer(limits.get("context")),
                    "output": _integer(limits.get("output")),
                },
                "capabilities": {
                    key: bool(capabilities.get(key))
                    for key in ("reasoning", "attachment", "toolcall")
                },
            }
        )
    return sorted(models, key=model_cost_key)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def model_cost_key(model: dict[str, Any]) -> tuple[Any, ...]:
    cost = model["cost"]
    unknown = cost["output"] is None or cost["input"] is None
    return (
        unknown,
        float("inf") if cost["output"] is None else cost["output"],
        float("inf") if cost["input"] is None else cost["input"],
        float("inf") if cost["cache_read"] is None else cost["cache_read"],
        model["model"].lower(),
    )


def estimate_models(
    models: list[dict[str, Any]],
    *,
    uncached_input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
) -> list[dict[str, Any]]:
    values = (uncached_input_tokens, cached_input_tokens, output_tokens, reasoning_tokens)
    if any(not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("token counts must be non-negative integers")
    results = []
    for model in models:
        cost = model["cost"]
        if cost["input"] is None or cost["output"] is None:
            estimated = None
        else:
            cache_rate = cost["cache_read"] if cost["cache_read"] is not None else cost["input"]
            reasoning_rate = cost["reasoning"] if cost["reasoning"] is not None else cost["output"]
            estimated = (
                uncached_input_tokens * cost["input"]
                + cached_input_tokens * cache_rate
                + output_tokens * cost["output"]
                + reasoning_tokens * reasoning_rate
            ) / 1_000_000
        results.append({**model, "estimated_usd": estimated, "cheapest": False})
    priced = [item for item in results if item["selectable"] and item["estimated_usd"] is not None]
    if priced:
        cheapest = min(item["estimated_usd"] for item in priced)
        for item in results:
            item["cheapest"] = item["estimated_usd"] == cheapest and item["selectable"]
    return sorted(
        results,
        key=lambda item: (
            item["estimated_usd"] is None,
            float("inf") if item["estimated_usd"] is None else item["estimated_usd"],
            model_cost_key(item),
        ),
    )


@dataclass
class ModelCatalogue:
    cache_dir: Path
    executable: str = os.getenv("AGCONSOLE_OPENCODE_BIN", "opencode")
    ttl_seconds: int = 900
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def _cache_path(self, provider: str) -> Path:
        if provider not in PROVIDERS:
            raise ValueError("provider must be openrouter or opencode")
        return self.cache_dir / f"{provider}.json"

    def list(self, provider: str, *, refresh: bool = False) -> dict[str, Any]:
        path = self._cache_path(provider)
        cached = self._read(path)
        fresh = cached and time.time() - cached.get("refreshed_epoch", 0) < self.ttl_seconds
        if cached and fresh and not refresh:
            return {**cached, "stale": False}
        try:
            result = self.runner(
                [self.executable, "models", provider, "--verbose"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError("OpenCode model catalogue command failed")
            models = parse_verbose_models(result.stdout, provider)
            if not models:
                raise RuntimeError("OpenCode returned no valid models")
            payload = {"provider": provider, "models": models, "refreshed_epoch": int(time.time())}
            self._write(path, payload)
            return {**payload, "stale": False}
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            if cached:
                return {**cached, "stale": True}
            raise

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and isinstance(value.get("models"), list) else None
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
