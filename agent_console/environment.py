"""Private write-only environment settings and deterministic session resolution.

Values are never included in public descriptions. Files are data, never shell.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .broker_client import PROTECTED_NAMES, strip_protected

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
RESERVED = frozenset({
    "HOME", "PATH", "SHELL", "USER", "LOGNAME", "PWD", "OLDPWD", "IFS", "ENV",
    "BASH_ENV", "SHELLOPTS", "BASHOPTS", "CDPATH", "GLOBIGNORE", "PYTHONPATH",
    "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS", "NODE_PATH", "PERL5OPT",
    "RUBYOPT", "ZDOTDIR", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "CODEX_HOME", "CLAUDE_HOME",
    "CLAUDE_CONFIG_DIR", "HERMES_HOME", "PI_CODING_AGENT_DIR", "TMUX", "TMUX_PANE",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR",
})
PREFIXES = ("AGENT_CONSOLE_", "AGCONSOLE_", "LD_", "DYLD_", "BASH_FUNC_",
            "PYTHON", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def validate_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Environment variable name is invalid")
    if name in RESERVED or name.startswith(PREFIXES):
        raise ValueError("Environment variable name is reserved for execution or Console control")
    return name


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Environment directory must be a private owned directory")
    path.chmod(0o700)


def read_private(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Environment file must be owner-only (0600)")
        with os.fdopen(fd, encoding="utf-8") as handle:
            fd = -1
            return json.load(handle)
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Environment file is invalid") from None
    finally:
        if fd >= 0:
            os.close(fd)


def write_private(path, data):
    path = Path(path)
    private_directory(path.parent)
    if path.is_symlink():
        raise ValueError("Environment file cannot be a symbolic link")
    fd, tmp = tempfile.mkstemp(prefix=".environment-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def credential_values(paths):
    """Read existing export KEY='value' credential files without shell expansion."""
    result = {}
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, separator, raw = line.partition("=")
                validate_name(key)
                if not separator:
                    raise ValueError()
                # shlex performs quoting only, never variable/command expansion.
                tokens = shlex.split(raw, comments=True)
                if len(tokens) > 1:
                    raise ValueError()
                result[key] = tokens[0] if tokens else ""
        except (OSError, UnicodeError, ValueError):
            raise ValueError("Selected credential file is missing or not literal KEY=value data") from None
    return result


class EnvironmentStore:
    def __init__(self, config_dir, *, broker=None):
        self.root = Path(config_dir) / "environment"
        self.path = self.root / "variables.json"
        self.broker = broker

    def _read(self):
        data = read_private(self.path)
        if data is None:
            return {"version": 1, "revision": 0, "scopes": {}, "scope_revisions": {}}
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("scopes"), dict):
            raise ValueError("Environment store schema is invalid")
        return data

    @contextmanager
    def _lock(self):
        private_directory(self.root)
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def scope(project_id=None):
        if project_id is not None and (not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", project_id)):
            raise ValueError("Project identity is invalid")
        return "global" if project_id is None else "project:" + project_id

    def put(self, name, *, value=None, state="enabled", project_id=None):
        validate_name(name)
        scope = self.scope(project_id)
        if self.broker and name in PROTECTED_NAMES:
            self.broker.put(name, value=value, state=state, project_id=project_id)
            return self.describe(project_id)
        if state not in {"enabled", "disabled", "suppressed"} or (state == "suppressed" and project_id is None):
            raise ValueError("Environment state is invalid for this scope")
        if value is not None:
            try:
                valid = isinstance(value, str) and "\0" not in value and len(value.encode("utf-8")) <= 32768
            except UnicodeError:
                valid = False
            if not valid:
                raise ValueError("Environment value must be valid text without NUL, at most 32 KiB")
        with self._lock():
            data = self._read()
            scope_entries = data["scopes"].setdefault(scope, {})
            existing = scope_entries.get(name, {})
            if state != "suppressed" and value is None and "value" not in existing:
                raise ValueError("A value is required for a new environment variable")
            data["revision"] += 1
            data.setdefault("scope_revisions", {})[scope] = data["revision"]
            entry = {"state": state, "revision": data["revision"]}
            if state != "suppressed":
                entry["value"] = existing.get("value") if value is None else value
            scope_entries[name] = entry
            if len(scope_entries) > 128 or sum(len(k.encode()) + len(v.get("value", "").encode()) + 2 for k, v in scope_entries.items()) > 65536:
                raise ValueError("Environment scope exceeds 128 variables or 64 KiB")
            if len(json.dumps(data).encode()) > 1048576:
                raise ValueError("Environment store exceeds its 1 MiB limit")
            write_private(self.path, data)
        return self.describe(project_id)

    def delete(self, name, *, project_id=None):
        validate_name(name)
        self.scope(project_id)
        if self.broker and name in PROTECTED_NAMES:
            self.broker.delete(name, project_id=project_id)
            return self.describe(project_id)
        with self._lock():
            data = self._read()
            entries = data["scopes"].get(self.scope(project_id), {})
            if name not in entries:
                raise KeyError("Environment variable is not configured in this scope")
            del entries[name]
            data["revision"] += 1
            data.setdefault("scope_revisions", {})[self.scope(project_id)] = data["revision"]
            write_private(self.path, data)
        return self.describe(project_id)

    def clear_project(self, project_id):
        """Remove scoped values during an explicitly authorized project deletion.

        Callers serialize project existence checks with database mutations before
        entering this lock, so a waiting API edit cannot recreate an orphan scope.
        """
        if project_id is None:
            raise ValueError("A project identity is required")
        scope = self.scope(project_id)
        if self.broker:
            self.broker.clear_project(project_id)
        with self._lock():
            data = self._read()
            if scope not in data["scopes"]:
                return
            del data["scopes"][scope]
            data.setdefault("scope_revisions", {}).pop(scope, None)
            data["revision"] += 1
            write_private(self.path, data)

    def _resolve(self, data, project_id, baseline, credentials):
        values = {k: v for k, v in baseline.items() if not ((k.startswith(("AGENT_CONSOLE_", "AGCONSOLE_")) and k.endswith("_CAPABILITY")) or k.startswith(("AGENT_CONSOLE_SESSION_", "AGENT_CONSOLE_PARENT_", "AGENT_CONSOLE_PROJECT_", "AGENT_CONSOLE_LINKED_")))}
        sources = {k: "host" for k in values}
        values.update(credentials)
        sources.update({k: "credential" for k in credentials})
        for scope in ["global"] + ([self.scope(project_id)] if project_id else []):
            for name, entry in data["scopes"].get(scope, {}).items():
                validate_name(name)
                if entry["state"] == "suppressed":
                    values.pop(name, None)
                    sources.pop(name, None)
                elif entry["state"] == "enabled":
                    value = entry.get("value")
                    if not isinstance(value, str) or "\0" in value:
                        raise ValueError("Environment store contains invalid value data")
                    values[name], sources[name] = value, scope
        return values, sources

    def resolve(self, project_id=None, *, baseline=None, secret_files=(), account_ref=None):
        data = self._read()
        values, _ = self._resolve(data, project_id, dict(os.environ) if baseline is None else baseline, credential_values(secret_files))
        if self.broker:
            values = strip_protected(values)
            values.update(self.broker.environment(project_id, account_ref))
        try:
            total_bytes = sum(len(k.encode()) + len(v.encode()) + 2 for k, v in values.items())
        except (UnicodeError, AttributeError):
            raise ValueError("Resolved environment contains invalid text") from None
        if total_bytes > 131072:
            raise ValueError("Resolved environment exceeds 128 KiB; reduce managed or host values")
        return values, self.revision(project_id, data=data)

    def revision(self, project_id=None, *, data=None):
        data = data or self._read()
        revisions = data.get("scope_revisions", {})
        return max(revisions.get("global", 0), revisions.get(self.scope(project_id), 0))

    def describe(self, project_id=None):
        data = self._read()
        scope = self.scope(project_id)
        baseline = dict(os.environ)
        _, sources = self._resolve(data, project_id, baseline, {})
        entries = [{"name": name, "state": entry["state"], "revision": entry["revision"], "scope": scope,
                    "has_value": "value" in entry, "overrides_host": name in baseline,
                    "overrides_global": project_id is not None and name in data["scopes"].get("global", {})}
                   for name, entry in sorted(data["scopes"].get(scope, {}).items())]
        managed = set(data["scopes"].get("global", {})) | set(data["scopes"].get(scope, {}))
        result = {"scope": scope, "revision": self.revision(project_id, data=data), "entries": entries,
                "effective": [{"name": key, "source": sources[key]} for key in sorted(sources) if key in managed],
                "host_names": sorted(k for k in baseline if NAME.fullmatch(k) and k not in RESERVED and not k.startswith(PREFIXES))}
        if self.broker:
            status = self.broker.describe(project_id)
            result["entries"] = [e for e in entries if e["name"] not in PROTECTED_NAMES]
            result["entries"] += [{**e, "scope": scope, "protected": True, "immediate": True}
                                  for e in status["entries"]]
            result["entries"].sort(key=lambda e: e["name"])
            result["effective"] = [e for e in result["effective"] if e["name"] not in PROTECTED_NAMES]
            result["effective"] += [{**e, "protected": True, "immediate": True} for e in status["effective"] if e.get("available")]
            result["host_names"] = sorted([n for n in result["host_names"] if n not in PROTECTED_NAMES] + status.get("host_names", []))
            result.update(broker_enabled=True, protected_names=sorted(PROTECTED_NAMES), broker_revision=status["revision"])
        return result
