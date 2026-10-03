"""Capability-authenticated peer inspection and descendant control.

The service owns database access. A harness has only its existing reporting
capability; neither caller-supplied role names nor mutable session names grant
authority. Operator endpoints retain their existing authorization.
"""
from __future__ import annotations

from types import SimpleNamespace

from .inspection_views import InspectionViews, read_route, READ_ROUTES
from .profiles import READ_ONLY_PROFILES

MAX_DELEGATION_DEPTH = 8


def child_worktree_required(profile, repository):
    from pathlib import Path
    return profile not in READ_ONLY_PROFILES and (
        profile in {'coder', 'bugfix'} or bool(repository and (Path(repository) / '.git').exists()))


def validate_child(parent, *, profile, repository, project_id, agent_mode, worktree):
    from pathlib import Path
    from .profiles import PROFILE_SCHEMA
    meta = PROFILE_SCHEMA.get(parent.get('profile') or 'general', {})
    if profile not in meta.get('allowed_delegation_profiles', ()):
        raise PermissionError('parent role cannot delegate this child role')
    restricted = parent.get('profile') in READ_ONLY_PROFILES or parent.get('agent_mode') == 'plan'
    if restricted and (profile not in READ_ONLY_PROFILES or agent_mode not in (None, 'plan')):
        raise PermissionError('read-only or Plan parent cannot delegate writable work')
    if project_id != parent.get('project_id'):
        raise PermissionError('child must inherit the parent project')
    expected = parent.get('repository')
    if expected and (not repository or Path(repository).resolve() != Path(expected).resolve()):
        raise PermissionError('child must inherit the parent repository')
    if child_worktree_required(profile, repository) and not worktree:
        raise PermissionError('writable child sessions require an isolated worktree')


class SessionControl:
    def __init__(self, manager, identity):
        self.manager = manager
        self.identity = identity
        with manager.database.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM sessions')]
            self.all_sessions = {row['id']: row for row in rows}
            self.current = self.all_sessions[identity['id']]
            project = self.current.get('project_id')
            ids = {row['id'] for row in rows if project and row.get('project_id') == project}
            if not project:
                root = self.root(self.current['id'])
                ids = {row['id'] for row in rows if self.root(row['id']) == root}
            sessions = []
            for row in rows:
                if row['id'] in ids:
                    row = dict(row)
                    row.pop('evidence_capability_hash', None)
                    sessions.append(row)
            delegations = [dict(row) for row in db.execute('SELECT * FROM delegations')
                           if row['parent_session_id'] in ids and row['child_session_id'] in ids]
        self.views = InspectionViews(manager.settings, snapshot={'sessions':sessions,
             'delegations':delegations, 'session_groups':[], 'group_members':[]}, current_id=identity['id'])
        # Observed but unrecorded tmux sessions have no authenticated project.
        self.views.sessions = [row for row in self.views.sessions if row['id'] in ids]

    def root(self, key):
        seen = set()
        while key in self.all_sessions:
            if key in seen or len(seen) > 128:
                raise ValueError('invalid session ancestry')
            seen.add(key)
            parent = self.all_sessions[key].get('parent_session_id')
            if parent not in self.all_sessions:
                return key
            key = parent
        return key

    def target(self, name=None):
        if not name or name in {self.identity['id'], self.identity['tmux_name']}:
            name = self.current['tmux_name']
        for row in self.views.sessions:
            if name in (row['id'], row['tmux_name']):
                return row
        raise PermissionError('session is outside the permitted project or tree')

    def authorize_control(self, target, *, own=False):
        if target['id'] == self.current['id']:
            if own:
                return
            raise PermissionError('this operation requires a descendant session')
        if self.current.get('profile') in READ_ONLY_PROFILES or self.current.get('agent_mode') == 'plan':
            raise PermissionError('read-only sessions cannot control other sessions')
        key, seen = target.get('parent_session_id'), set()
        while key in self.all_sessions and key not in seen:
            if key == self.current['id']:
                return
            seen.add(key)
            key = self.all_sessions[key].get('parent_session_id')
        raise PermissionError('session is not a descendant of the caller')

    def run(self, command, payload):
        if command == 'read':
            route = tuple(payload['route'])
            if route not in READ_ROUTES or route[0] != 'session' or len(route) != 2:
                raise ValueError('unsupported session inspection route')
            args = SimpleNamespace(name=None, current=False, relative=None, index=None,
                                   session_id=None, lines=200, **{})
            for key in ('name','current','relative','index','session_id','lines'):
                if key in payload:
                    setattr(args, key, payload[key])
            if args.name:
                args.name = self.target(args.name)['tmux_name']
            return read_route(args, route, views=self.views)
        target = self.target(payload.get('name'))
        actor = 'session:' + self.current['id']
        if command == 'attention':
            self.authorize_control(target, own=True)
            return self.manager.set_attention(target['tmux_name'], state=payload['state'],
                 note=payload.get('note'), actor=actor, surface='session-api')
        if command == 'delegate':
            if target['id'] != self.current['id']:
                raise PermissionError('delegation must originate from the caller')
            allowed = {'profile','task','repository','tool','auth_context','agent_mode','model','provider',
                       'reasoning_effort','plan_reasoning_effort'}
            config = {key:value for key,value in payload.items() if key in allowed}
            config['name'] = payload.get('child_name')
            return self.manager.delegate(parent=self.current['id'], creator_surface='session-api', **config)
        if command == 'children':
            self.authorize_control(target, own=True)
            from .session_relatives import relatives
            group = relatives(self.views.sessions, target['id'])
            ids = set(group['relations']['descendant'])
            return {'children':[row for row in self.views.sessions if row['id'] in ids]}
        self.authorize_control(target)
        name = target['tmux_name']
        if command == 'interrupt':
            return self.manager.interrupt(name)
        if command == 'restart-agent':
            return self.manager.restart(name)
        if command == 'kill':
            return self.manager.kill(name, allow_unmanaged=False)
        raise ValueError('unsupported session control operation')

