"""Shared owner environment scope checks and value-blind descriptions."""
from .environment import read_private


def scope(manager, project_id):
    manager.environment.scope(project_id)
    if project_id is not None:
        with manager.database.connect() as conn:
            if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
                raise KeyError("Project does not exist")
    return project_id


def mutate(manager, project_id, operation):
    # Project deletion holds the same database writer lock while clearing
    # its private scope. Recheck existence inside the transaction, preventing
    # an edit validated before deletion from recreating inaccessible values.
    if project_id is None:
        return operation()
    manager.environment.scope(project_id)
    with manager.database.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
            raise KeyError("Project does not exist")
        return operation()


def describe(manager, project_id):
    result = manager.environment.describe(scope(manager, project_id))
    with manager.database.connect() as conn:
        rows = conn.execute("SELECT id, tmux_name, project_id, status FROM sessions WHERE managed=1 AND execution_kind!='integration-plan'").fetchall()
    sessions = []
    for row in rows:
        if project_id and row["project_id"] != project_id:
            continue
        snapshot = read_private(manager.settings.state_dir / "environment-launches" / f"{row['id']}.json")
        revision = snapshot.get("revision") if snapshot else None
        sessions.append({"id": row["id"], "name": row["tmux_name"], "status": row["status"],
                         "revision": revision, "refresh_required": revision != manager.environment.revision(row["project_id"])})
    result["sessions"] = sessions
    return result
