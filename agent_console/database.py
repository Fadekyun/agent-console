from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 11


EVIDENCE_TYPES = frozenset({"review", "verification", "scout"})
EVIDENCE_RESULTS = frozenset({"pass", "fail", "blocked"})
REQUIRED_EVIDENCE_TYPES = frozenset({"review", "verification", "scout"})


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

                CREATE TABLE IF NOT EXISTS session_groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    purpose TEXT,
                    parent_session_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(parent_session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    repository TEXT,
                    description TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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

                CREATE TABLE IF NOT EXISTS plan_evidence (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    candidate_sha TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    result TEXT NOT NULL,
                    detail TEXT,
                    session_id TEXT,
                    session_name TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(id)
                );

                CREATE TABLE IF NOT EXISTS superpower_approvals (
                    profile TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approved_surface TEXT NOT NULL DEFAULT 'CLI',
                    approved_at TEXT NOT NULL,
                    revoked_at TEXT,
                    PRIMARY KEY (profile, skill_name)
                );

                CREATE TABLE IF NOT EXISTS skill_assignments (
                    profile TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    assigned_by TEXT NOT NULL DEFAULT 'system',
                    assigned_surface TEXT NOT NULL DEFAULT 'CLI',
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY (profile, skill_name)
                );

                CREATE TABLE IF NOT EXISTS group_members (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL REFERENCES session_groups(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    added_at TEXT NOT NULL,
                    added_by TEXT NOT NULL DEFAULT 'system',
                    UNIQUE(group_id, session_id)
                );

                CREATE TABLE IF NOT EXISTS session_waits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_session_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    deadline_at TEXT NOT NULL,
                    poll_interval_seconds INTEGER NOT NULL DEFAULT 10,
                    outcome TEXT,
                    completed_at TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(parent_session_id) REFERENCES sessions(id)
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
            if "project_id" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT REFERENCES projects(id)")
            if "evidence_capability_hash" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN evidence_capability_hash TEXT")
            if "execution_kind" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'interactive'"
                )

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS integration_requests (
                    id TEXT PRIMARY KEY,
                    integration TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    requester_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    canonical_hash TEXT NOT NULL,
                    canonical_payload_json TEXT NOT NULL,
                    project_alias TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    frozen_context TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    frozen_prompt TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    artifact_dir TEXT NOT NULL,
                    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    accepted_at TEXT NOT NULL,
                    content_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_at TEXT,
                    launch_nonce TEXT NOT NULL,
                    claimed_at TEXT,
                    provider_tool TEXT NOT NULL,
                    auth_context TEXT NOT NULL,
                    provider_version TEXT,
                    child_pid INTEGER,
                    child_start_time TEXT,
                    child_pgid INTEGER,
                    child_boot_id TEXT,
                    thread_id TEXT,
                    receipt_json TEXT NOT NULL DEFAULT '{}',
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    final_artifact_hash TEXT,
                    final_artifact_name TEXT,
                    reason_code TEXT NOT NULL,
                    admission_held INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(integration, request_key)
                );
                CREATE INDEX IF NOT EXISTS idx_integration_requests_state
                    ON integration_requests(integration, state);
                """
            )
            integration_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(integration_requests)").fetchall()
            }
            if "content_expires_at" not in integration_columns:
                conn.execute("ALTER TABLE integration_requests ADD COLUMN content_expires_at TEXT")
                conn.execute(
                    "UPDATE integration_requests SET content_expires_at="
                    "datetime(accepted_at, '+30 days') WHERE content_expires_at IS NULL"
                )
            if "child_boot_id" not in integration_columns:
                conn.execute("ALTER TABLE integration_requests ADD COLUMN child_boot_id TEXT")

            plans_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(plans)").fetchall()
            }
            if "release_blocked_at" not in plans_columns:
                conn.execute("ALTER TABLE plans ADD COLUMN release_blocked_at TEXT")
                conn.execute("ALTER TABLE plans ADD COLUMN release_blocked_reason TEXT")
            releases_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(releases)").fetchall()
            } if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='releases'"
            ).fetchone() else set()
            if not releases_columns:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS releases (
                        release_name TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        source_sha TEXT,
                        plan_id TEXT,
                        file_count INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'created',
                        current_at TEXT,
                        canary_at TEXT,
                        promoted_at TEXT,
                        rollback_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(status);
                    """
                )

            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

            # Migrate auth contexts: rename opencode-go-default to opencode-zen-default
            # for sessions that were using the old ZEN provider (provider=opencode)
            conn.execute("""
                UPDATE sessions 
                SET auth_context = 'opencode-zen-default' 
                WHERE tool = 'opencode' 
                AND auth_context = 'opencode-go-default' 
                AND provider = 'opencode'
            """)
            
            # Ensure sessions with opencode-go provider use opencode-go-default context
            conn.execute("""
                UPDATE sessions 
                SET auth_context = 'opencode-go-default' 
                WHERE tool = 'opencode' 
                AND provider = 'opencode-go'
                AND (auth_context IS NULL OR auth_context != 'opencode-go-default')
            """)

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

    def prune_audit_events(self, retention_days: int = 395) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_str = cutoff.isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff_str,))
            return conn.total_changes
