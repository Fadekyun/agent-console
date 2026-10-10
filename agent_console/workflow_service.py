"""Session-bound operations for versioned results and explicit handoffs."""
import hashlib
import os
from pathlib import Path

from .workflow_store import WorkflowStore


class WorkflowService:
    def __init__(self, manager):
        self.manager = manager
        self.store = WorkflowStore(manager.settings.state_dir)
        self.store.migrate()

    def session(self, identity):
        with self.manager.database.connect() as db:
            row = db.execute('SELECT tmux_name FROM sessions WHERE id=? OR tmux_name=?', (identity,identity)).fetchone()
        if row is None: raise KeyError('session not found')
        session = self.manager.inspect(row['tmux_name'])
        if not session.get('managed') or session.get('execution_kind') == 'integration-plan':
            raise ValueError('choose a managed interactive session')
        return session

    def current(self, identity=None):
        # Native launchers already mint this capability for every managed session.
        # Agent CLI mutation must be attributed to that session, including after rename.
        session_id = os.getenv('AGENT_CONSOLE_SESSION_ID')
        capability = os.getenv('AGENT_CONSOLE_EVIDENCE_CAPABILITY')
        if not session_id or not capability:
            raise PermissionError('run the agent command inside its managed session; operators use the authenticated UI')
        session = self.session(identity or session_id)
        digest = hashlib.sha256(capability.encode()).hexdigest()
        with self.manager.database.connect() as db:
            row = db.execute('SELECT id FROM sessions WHERE id=? AND evidence_capability_hash=?', (session_id,digest)).fetchone()
        if not row or session['id'] != session_id:
            raise PermissionError('session capability does not authorize this source or recipient')
        return session

    def publish(self, identity, payload, actor):
        session = self.session(identity)
        result = self.store.publish(session, **payload, actor=actor, workspace=self.manager.settings.workspace_root)
        self.manager.database.audit('result.published',session['id'],'success',actor=actor,
                                    details={'result_id':result['id'],'version':result['version'],'kind':result['kind'],'outcome':result['outcome']})
        return result

    def send(self, result_id, target, *, request_key, note='', actor, source=None):
        result = self.store.result(result_id)
        if source and self.session(source)['id'] != result['session_id']:
            raise PermissionError('result belongs to a different source session')
        recipient = self.session(target)
        return self.store.send(result_id, recipient['id'], note=note, request_key=request_key, actor=actor)

    def graph(self):
        from .workflow_graph import WorkflowGraph
        return WorkflowGraph(self.store)

    def attach(self, owner, target, *, purpose, dependencies, expected_version, actor):
        from .profiles import PROFILE_SCHEMA, validate_profile_capability
        parent=self.session(owner);child=self.session(target)
        if child['id']!=target:
            raise ValueError('attach by durable session ID, not a mutable session name')
        if child['profile'] not in PROFILE_SCHEMA.get(parent['profile'],{}).get('allowed_collaboration_profiles',set()):
            raise ValueError('these roles cannot collaborate')
        capability=validate_profile_capability(child['profile'],child['tool'],child.get('agent_mode'),worktree=bool(child.get('worktree_path') or child.get('worktree')))
        if not capability['allowed']:raise ValueError(capability['reason'])
        # Existing sessions keep their selected skill snapshot and native process.
        # This operator action connects inputs; it grants no new tools or role.
        return self.graph().attach(parent['id'],child['id'],purpose=purpose,dependencies=dependencies,
                                   expected_version=expected_version,actor=actor)


def authenticate_session(manager, session_id, capability):
    if not session_id or not capability or len(capability) > 256:
        raise PermissionError('valid session reporting capability required')
    digest=hashlib.sha256(capability.encode()).hexdigest()
    with manager.database.connect() as db:
        row=db.execute('SELECT id,tmux_name FROM sessions WHERE id=? AND evidence_capability_hash=? AND managed=1', (session_id,digest)).fetchone()
    if not row: raise PermissionError('valid session reporting capability required')
    return dict(row)
