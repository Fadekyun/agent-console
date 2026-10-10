"""CommandCode reasoning gate for Hermes' ``custom`` provider profile.

Installed per CommandCode session under
``$HERMES_HOME/plugins/model-providers/commandcode/``. Hermes discovers user
plugins after bundled ones, so registering ``custom`` here overrides the
bundled profile (last-writer-wins) for that session only.

Why this exists: CommandCode's OpenAI-compatible endpoint only accepts a
top-level ``reasoning_effort`` from ``{low, medium, high, xhigh, max}`` and
returns HTTP 400 for models that do not support it (for example the Claude
entries). Hermes keeps ``agent.reasoning_effort`` for the whole session, so a
``/model`` switch from a supported model to an unsupported one would otherwise
inherit the effort and fail. This profile re-checks the *current request's*
model against the allowlist written next to HERMES_HOME before emitting it.

The allowlist lives in ``reasoning-models.json`` (written by the Agent Console
provisioner) so there is a single source of truth.
"""

from __future__ import annotations

import json

from providers import get_provider_profile, register_provider
from providers.base import ProviderProfile

_SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _supported_models() -> frozenset:
    try:
        from hermes_constants import get_hermes_home

        payload = json.loads(
            (get_hermes_home() / "reasoning-models.json").read_text(encoding="utf-8")
        )
        return frozenset(str(model) for model in payload.get("models") or ())
    except Exception:
        return frozenset()


_BASE = get_provider_profile("custom")


class _CommandCodeCustomProfile(type(_BASE) if _BASE is not None else ProviderProfile):
    """Bundled ``custom`` behaviour plus a model-aware ``reasoning_effort`` gate."""

    def build_api_kwargs_extras(self, *, reasoning_config=None, model=None, **kwargs):
        extra_body, top_level = super().build_api_kwargs_extras(
            reasoning_config=reasoning_config, model=model, **kwargs
        )
        effort = top_level.get("reasoning_effort")
        if not isinstance(effort, str):
            return extra_body, top_level
        if model not in _supported_models():
            # Unsupported model (or an unknown switch target): never inherit the
            # session-level effort, which the endpoint would reject with a 400.
            top_level.pop("reasoning_effort", None)
        elif effort == "minimal":
            # CommandCode rejects "minimal"; the nearest accepted level is "low".
            top_level["reasoning_effort"] = "low"
        elif effort not in _SUPPORTED_EFFORTS:
            top_level.pop("reasoning_effort", None)
        return extra_body, top_level


if _BASE is not None:
    _profile = _CommandCodeCustomProfile.__new__(_CommandCodeCustomProfile)
    _profile.__dict__.update(_BASE.__dict__)
    register_provider(_profile)
