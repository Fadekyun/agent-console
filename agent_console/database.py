from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    tmux_name TEXT NOT NULL UNIQUE,
                    tool TEXT,
                    profile TEXT,
                    parent_session_id TEXT,
                    created_at TEXT NOT NULL,
                    last_activity TEXT,
                    initial_task TEXT,
                    repository TEXT,
                    worktree TEXT,
                    status TEXT NOT NULL,
                    managed INTEGER NOT NULL DEFAULT 1,
                    creator_surface TEXT NOT NULL DEFAULT 'CLI',
                    linked_plan_id TEXT,
                    launcher_path TEXT,
                    exit_reason TEXT,
                    archived_transcript TEXT,
                    socket_scope TEXT NOT NULL DEFAULT 'canonical',
                    auth_context TEXT,
                    agent_mode TEXT,
                    provider TEXT,
                    model TEXT,
                    permission_mode TEXT,
                    attention_state TEXT NOT NULL DEFAULT 'normal',
                    attention_note TEXT,
                    attention_updated_at TEXT,
                    attention_updated_by TEXT,
                    FOREIGN KEY(parent_session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    repository TEXT,
                    profile TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    artifact_dir TEXT NOT NULL,
                    source TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS delegations (
                    id TEXT PRIMARY KEY,
                    parent_session_id TEXT NOT NULL,
                    child_session_id TEXT,
                    profile TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_path TEXT,
                    FOREIGN KEY(parent_session_id) REFERENCES sessions(id),
                    FOREIGN KEY(child_session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "socket_scope" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN socket_scope TEXT NOT NULL DEFAULT 'canonical'"
                )
            for name in ("auth_context", "agent_mode", "provider", "model", "permission_mode"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} TEXT")
            attention_columns = {
                "attention_state": "TEXT NOT NULL DEFAULT 'normal'",
                "attention_note": "TEXT",
                "attention_updated_at": "TEXT",
                "attention_updated_by": "TEXT",
            }
            for name, definition in attention_columns.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def audit(
        self,
        action: str,
        target: str | None,
        outcome: str,
        *,
        actor: str = "system",
        surface: str = "CLI",
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(created_at, actor, surface, action, target, outcome, details_json) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (utc_now(), actor, surface, action, target, outcome, json.dumps(details or {})),
            )
