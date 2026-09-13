"""Maintenance-only release/schema compatibility, never an inspection fallback.

Schema 11 adds private request data. A return to schema 10 is supported only
while that feature has never stored requests or noninteractive sessions.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sqlite3
import stat


class SchemaCompatibilityError(ValueError):
    pass


def release_schema_version(release: Path) -> int:
    """Read a literal version from validated release source without importing it."""
    source = release / "agent_console" / "database.py"
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(release.resolve(strict=True))
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 262144:
            raise ValueError()
        tree = ast.parse(source.read_text(encoding="utf-8"))
        versions = [node.value.value for node in tree.body
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "SCHEMA_VERSION"
                    and isinstance(node.value, ast.Constant)]
        if len(versions) != 1 or type(versions[0]) is not int or versions[0] not in {10, 11}:
            raise ValueError()
        return versions[0]
    except (OSError, ValueError, SyntaxError):
        raise SchemaCompatibilityError("release schema is unsupported or unavailable") from None


def _integration_disabled(config_dir: Path) -> bool:
    path = config_dir / "plan-integration.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
                return False
            value = json.loads(stream.read(65537))
            return isinstance(value, dict) and value.get("enabled") is False
    except (OSError, ValueError):
        return False


def prepare_database_for_release(database: Path, target_version: int, config_dir: Path) -> dict:
    """Check/prepare a maintenance transition under the caller's admission lock.

    This may update only schema_meta during the strictly empty-feature 11→10
    compatibility rollback. It never drops extension tables/columns, sessions,
    receipts or tombstones. Unlike inspection, maintenance may touch WAL files.
    Config must remain disabled throughout the authorized release operation.
    """
    if type(target_version) is not int or target_version not in {10, 11}:
        raise SchemaCompatibilityError("target schema is unsupported")
    if not _integration_disabled(config_dir):
        raise SchemaCompatibilityError("this release requires native planning to remain disabled")
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchall()
            if len(rows) != 1 or rows[0][0] not in {"10", "11"}:
                raise SchemaCompatibilityError("stored schema is unsupported")
            current = int(rows[0][0])
            # Also inspect extension data on schema10 after a prior compatibility
            # rollback; absence is expected only for the original schema10 layout.
            table = connection.execute("SELECT type FROM sqlite_master WHERE name='integration_requests'").fetchone()
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            if target_version == 10 and (current == 11 or table or "execution_kind" in columns):
                if not _integration_disabled(config_dir):
                    raise SchemaCompatibilityError("schema10 rollback requires disabled planning")
                if not table or table[0] != "table" or "execution_kind" not in columns:
                    raise SchemaCompatibilityError("schema11 layout is incomplete")
                if connection.execute("SELECT 1 FROM integration_requests LIMIT 1").fetchone():
                    raise SchemaCompatibilityError("schema10 rollback blocked by retained request data")
                if connection.execute("SELECT 1 FROM sessions WHERE execution_kind IS NULL OR execution_kind != 'interactive' LIMIT 1").fetchone():
                    raise SchemaCompatibilityError("schema10 rollback blocked by noninteractive sessions")
                connection.execute("UPDATE schema_meta SET value='10' WHERE key='schema_version'")
            return {"previous_schema": current, "target_schema": target_version,
                    "compatibility_rollback": current == 11 and target_version == 10}
    except (sqlite3.Error, OSError):
        raise SchemaCompatibilityError("schema compatibility check unavailable") from None
    finally:
        if 'connection' in locals():
            connection.close()
