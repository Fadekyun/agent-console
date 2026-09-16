from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence

from .tmux import Tmux
from .validation import validate_session_name


ENV_ALLOWLIST_VARIABLE = "AGENT_CONSOLE_SESSION_ENV_ALLOWLIST"
BINDING_MARKER = "AGENT_CONSOLE_ENV_BINDINGS"
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# These variables can alter process loading, shell startup, Console control-plane
# behavior, or tmux's own terminal contract. The generic picker must never be a
# route for changing them even if they are accidentally named in the allowlist.
FORBIDDEN_BINDINGS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "TMUX",
        "TMUX_PANE",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "BASH_ENV",
        "ENV",
        "NODE_OPTIONS",
        "RUBYOPT",
        "PERL5OPT",
        "GIT_SSH_COMMAND",
        BINDING_MARKER,
    }
)


class EnvironmentBindingError(ValueError):
    """A names-only runtime binding request is invalid or unavailable."""


def _validate_name(name: str) -> str:
    value = str(name).strip()
    if not ENV_NAME_PATTERN.fullmatch(value):
        raise EnvironmentBindingError(f"invalid environment variable name: {value!r}")
    if value in FORBIDDEN_BINDINGS or value.startswith("AGENT_CONSOLE_"):
        raise EnvironmentBindingError(f"environment variable is reserved: {value}")
    return value


def configured_allowlist(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    source = os.environ if environ is None else environ
    raw = source.get(ENV_ALLOWLIST_VARIABLE, "")
    if not raw.strip():
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        if not item.strip():
            continue
        name = _validate_name(item)
        if name not in seen:
            values.append(name)
            seen.add(name)
    return tuple(values)


def environment_catalog(environ: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    source = os.environ if environ is None else environ
    return [
        {"name": name, "available": name in source}
        for name in configured_allowlist(source)
    ]


def validate_requested_names(
    names: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    source = os.environ if environ is None else environ
    allowed = set(configured_allowlist(source))
    result: list[str] = []
    seen: set[str] = set()
    for item in names:
        name = _validate_name(item)
        if name not in allowed:
            raise EnvironmentBindingError(f"environment variable is not allowlisted: {name}")
        if name not in seen:
            result.append(name)
            seen.add(name)
    missing = [name for name in result if name not in source]
    if missing:
        raise EnvironmentBindingError(
            "allowlisted environment variable(s) are unavailable: " + ", ".join(missing)
        )
    return tuple(result)


def resolve_requested_values(
    names: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    validated = validate_requested_names(names, environ=source)
    return {name: source[name] for name in validated}


def current_bindings(tmux: Tmux, session_name: str) -> tuple[str, ...]:
    validate_session_name(session_name)
    result = tmux.run(
        "show-environment", "-t", session_name, BINDING_MARKER, check=False
    )
    if result.returncode != 0:
        return ()
    line = result.stdout.strip()
    prefix = f"{BINDING_MARKER}="
    if not line.startswith(prefix):
        return ()
    raw = line[len(prefix):]
    if not raw:
        return ()
    values: list[str] = []
    for item in raw.split(","):
        try:
            values.append(_validate_name(item))
        except EnvironmentBindingError:
            # Fail closed: never use an invalid marker to address another env key.
            return ()
    return tuple(dict.fromkeys(values))


def binding_status(tmux: Tmux, session_name: str) -> dict[str, object]:
    selected = current_bindings(tmux, session_name)
    available = {item["name"]: bool(item["available"]) for item in environment_catalog()}
    return {
        "session": session_name,
        "bindings": [
            {"name": name, "available": available.get(name, False)}
            for name in selected
        ],
        "effective_after_restart": True,
    }


def apply_bindings(
    tmux: Tmux,
    session_name: str,
    names: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    validate_session_name(session_name)
    if not tmux.exists(session_name):
        raise KeyError(f"tmux session not found: {session_name}")

    selected = validate_requested_names(names, environ=environ)
    values = resolve_requested_values(selected, environ=environ)
    previous = set(current_bindings(tmux, session_name))

    # Values are passed as argv to the local tmux client and become tmux session
    # environment state. They are never written to the launcher, database, API,
    # or audit payload. Same-UID processes remain mutually trusted by design.
    for name in sorted(previous - set(selected)):
        tmux.run("set-environment", "-u", "-t", session_name, name)
    for name in selected:
        tmux.run("set-environment", "-t", session_name, name, values[name])

    if selected:
        tmux.run(
            "set-environment",
            "-t",
            session_name,
            BINDING_MARKER,
            ",".join(selected),
        )
    else:
        tmux.run("set-environment", "-u", "-t", session_name, BINDING_MARKER, check=False)

    return {
        "session": session_name,
        "bindings": [{"name": name, "available": True} for name in selected],
        "effective_after_restart": True,
    }
