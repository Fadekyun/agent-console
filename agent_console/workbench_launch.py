"""Reviewed launch recipes and recorded Console configuration.

Only explicit operator endpoints launch from recipes. A continuation is a new
conversation in the preserved workspace, not a claim to restore model memory.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from .database import utc_now
from .providers import provider_adapter
from .skill_registry import SkillRegistry
from .skills import _resolve_canonical_root
from .validation import contained_path
from .workflow_service import WorkflowService
from .workflow_store import WorkflowStore, bounded_text, canonical, identifier

CONFIG_KEYS = {'tool', 'profile', 'repository', 'worktree', 'auth_context',
               'agent_mode', 'provider', 'model', 'reasoning_effort',
               'plan_reasoning_effort', 'project_id'}
REQUEST_KEYS = CONFIG_KEYS | {'task', 'name'}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def launcher_identity(manager, tool):
    binary = provider_adapter(tool, manager.auth).binary
    if not binary.is_file():
        raise ValueError('Tool launcher is unavailable')
    content = binary.read_bytes()
    version = None
    try:
        result = subprocess.run([str(binary), '--version'], capture_output=True,
                                text=True, timeout=3, check=False)
        match = re.search(r'\b\d+\.\d+(?:\.\d+){0,2}(?:[-+][\w.-]+)?', result.stdout[:2000])
        if result.returncode == 0 and match:
            version = match[0]
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    # Never expose arbitrary --version output or launcher contents/environment.
    return {'path': str(binary), 'sha256': hashlib.sha256(content).hexdigest(), 'version': version}


class LaunchCatalog:
    def __init__(self, manager):
        self.manager = manager
        self.store = WorkflowStore(manager.settings.state_dir)
        self.store.migrate()
        with self.store.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS workbench_launch_schema(version INTEGER NOT NULL)')
            row = db.execute('SELECT version FROM workbench_launch_schema').fetchone()
            if row and row[0] != 1:
                raise ValueError('unsupported workbench launch schema')
            if not row:
                db.execute('INSERT INTO workbench_launch_schema VALUES(1)')
            db.execute('''CREATE TABLE IF NOT EXISTS launch_configurations(
                session_id TEXT NOT NULL, sequence INTEGER NOT NULL, created_at TEXT NOT NULL,
                receipt_json TEXT NOT NULL, PRIMARY KEY(session_id,sequence))''')
            db.execute('''CREATE TABLE IF NOT EXISTS work_recipes(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,revision INTEGER NOT NULL,
                request_json TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
                actor TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS work_launch_requests(
                request_key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,name TEXT NOT NULL,
                source_session_id TEXT,state TEXT NOT NULL,session_id TEXT,error TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')

    def session(self, identity):
        return WorkflowService(self.manager).session(identity)

    def configuration(self, identity):
        with self.manager.database.connect() as db:
            row = db.execute('SELECT tmux_name FROM sessions WHERE id=? OR tmux_name=?', (identity, identity)).fetchone()
        if not row:
            raise KeyError('Session not found')
        session = self.manager.inspect(row[0])
        with self.store.connect() as db:
            receipts = [json.loads(r[0]) for r in db.execute(
                'SELECT receipt_json FROM launch_configurations WHERE session_id=? ORDER BY sequence',
                (session['id'],))]
        known = {key: session.get(key) for key in CONFIG_KEYS if key != 'worktree'}
        known['worktree'] = bool(session.get('worktree'))
        return {'session_id': session['id'], 'session_name': session['tmux_name'],
                'running': session['running'], 'latest': receipts[-1] if receipts else None,
                'receipts': receipts, 'known': known,
                'notice': 'Recorded Console launch settings. Repository instructions and native plugins can add context; model memory is not restored.' if receipts else
                          'This session predates configuration receipts. Reasoning settings and exact launch configuration are unknown; review a new launch instead.'}

    def describe(self, prepared, *, task=None, name=None, source_id=None):
        config = dict(prepared['config'])
        for key, value in config.items():
            if isinstance(value, str):
                bounded_text(value, key, 4000)
        registry = SkillRegistry(_resolve_canonical_root(), self.manager.database.path.parent)
        skills = sorted([{'name': s['name'], 'hash': registry.inspect(s['name'])['hash']}
                         for s in prepared['skills']['materialized']], key=lambda s: s['name'])
        warnings = []
        if config['tool'] != 'shell' and not config['model']:
            warnings.append('Model uses the harness default; its actual selection is not pinned.')
        if config['tool'] in {'codex', 'codex-pro'} and not config['reasoning_effort']:
            warnings.append('Reasoning effort uses the harness default.')
        launcher = launcher_identity(self.manager, config['tool'])
        if not launcher['version']:
            warnings.append('Harness version could not be verified.')
        value = {'config': config, 'task': bounded_text(task or '', 'task', 12000),
                 'name': name, 'skills': skills,
                 'profile_hash': hashlib.sha256(prepared['profile_content'].encode()).hexdigest(),
                 'permission_mode': prepared['permission_mode'], 'launcher': launcher,
                 'warnings': warnings, 'source_session_id': source_id, 'input_result_id': None,
                 'workspace': config['repository']}
        if source_id:
            source = self.session(source_id)
            if source_id != source['id']:
                raise ValueError('Continue by durable session ID')
            if source['running']:
                raise ValueError('The source is still running; open its terminal instead of continuing it twice')
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_attempts'").fetchone():
                    if db.execute('SELECT 1 FROM workflow_attempts WHERE session_id=?', (source['id'],)).fetchone():
                        raise ValueError('Use Next steps to retry this supervised attempt with its required inputs')
            previous = self.configuration(source['id'])['latest']
            if not previous or previous.get('invalidated'):
                raise ValueError('Exact launch settings are unavailable; review a new session from the known settings')
            for key in ['config', 'skills', 'profile_hash', 'permission_mode', 'launcher']:
                if value[key] != previous[key]:
                    raise ValueError('Recorded ' + key.replace('_', ' ') + ' changed; review a new launch rather than silently changing the continuation')
            workspace = source.get('worktree') or source['repository']
            value['workspace'] = str(contained_path(Path(workspace), self.manager.settings.workspace_root))
            for active in self.manager.list_sessions():
                if active['running'] and (active.get('worktree') or active.get('repository')) == value['workspace']:
                    raise ValueError('Another session is using the preserved workspace; stop or finish it first')
            latest = self.store.results(source['id'])
            value['input_result_id'] = latest[0]['id'] if latest else None
            warnings.append('Continues in the existing workspace, including uncommitted files. This starts a new conversation; the latest explicit result is delivered as an attributed input.')
        return {**value, 'hash': digest(value)}

    def preview(self, request, *, source_id=None):
        if not isinstance(request, dict) or set(request) - REQUEST_KEYS:
            raise ValueError('Unknown launch configuration field')
        if 'worktree' in request and type(request['worktree']) is not bool:
            raise ValueError('worktree must be boolean')
        if not request.get('tool') or not request.get('profile'):
            raise ValueError('Choose a tool and role')
        for key, value in request.items():
            if key != 'worktree' and value is not None and not isinstance(value, str):
                raise ValueError('Launch configuration values must be text')
        name = request.get('name')
        if name:
            from .validation import validate_session_name
            validate_session_name(name)
        prepared = self.manager.prepare_launch(**{key: value for key, value in request.items() if key in CONFIG_KEYS})
        return self.describe(prepared, task=request.get('task'), name=name, source_id=source_id)

    def record(self, session_id, view, *, workspace, request_id=None):
        receipt = {key: value for key, value in view.items() if key not in {'name', 'task', 'hash'}}
        receipt.update({'workspace': str(workspace), 'request_id': request_id, 'created_at': utc_now(),
                        'session_id': session_id, 'sequence': time.time_ns()})
        with self.store.connect(write=True) as db:
            db.execute('INSERT INTO launch_configurations VALUES(?,?,?,?)',
                       (session_id, receipt['sequence'], receipt['created_at'], canonical(receipt)))
        return receipt

    def invalidate(self, session_id, reason):
        view = self.configuration(session_id)['latest']
        if view:
            self.record(session_id, {**view, 'invalidated': reason}, workspace=view['workspace'])

    def recipes(self):
        with self.store.connect() as db:
            return [self._recipe(row) for row in db.execute('SELECT * FROM work_recipes WHERE deleted=0 ORDER BY title,id')]

    @staticmethod
    def _recipe(row):
        return {key: row[key] for key in ['id', 'title', 'revision', 'actor', 'updated_at']} | {'request': json.loads(row['request_json'])}

    def save_recipe(self, title, request, *, actor, recipe_id=None, expected_revision=None):
        title = bounded_text(title, 'Recipe title', 120, True)
        view = self.preview(request)
        saved = {**view['config'], 'task': view['task']}
        with self.store.connect(write=True) as db:
            if recipe_id:
                old = db.execute('SELECT * FROM work_recipes WHERE id=? AND deleted=0', (recipe_id,)).fetchone()
                if not old or old['revision'] != expected_revision:
                    raise ValueError('Recipe changed; reload it before saving')
                revision = old['revision'] + 1
                db.execute('UPDATE work_recipes SET title=?,revision=?,request_json=?,actor=?,updated_at=? WHERE id=?',
                           (title, revision, canonical(saved), actor, utc_now(), recipe_id))
            else:
                recipe_id = identifier('recipe'); revision = 1
                db.execute('INSERT INTO work_recipes VALUES(?,?,?,?,0,?,?)', (recipe_id, title, revision, canonical(saved), actor, utc_now()))
            self.store.event(db, 'recipe.saved', recipe_id, {'revision': revision}, actor)
            return self._recipe(db.execute('SELECT * FROM work_recipes WHERE id=?', (recipe_id,)).fetchone())

    def remove_recipe(self, recipe_id, revision, *, actor):
        with self.store.connect(write=True) as db:
            changed = db.execute('UPDATE work_recipes SET deleted=1,revision=revision+1 WHERE id=? AND revision=? AND deleted=0',
                                 (recipe_id, revision)).rowcount
            if not changed:
                raise ValueError('Recipe changed; reload it before removing')
            self.store.event(db, 'recipe.removed', recipe_id, {}, actor)
        return {'removed': True}

    def launch_status(self, key):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM work_launch_requests WHERE request_key=?', (key,)).fetchone()
        if not row:
            raise KeyError('Launch request not found')
        data = dict(row)
        # A process may die after creating the session but before acknowledging it.
        # Recover only from its durable configuration receipt, never a name alone.
        if data['state'] in {'creating', 'unknown'}:
            with self.manager.database.connect() as db:
                session = db.execute('SELECT id FROM sessions WHERE tmux_name=?', (data['name'],)).fetchone()
            if session:
                config = self.configuration(session['id'])['latest']
                if config and config.get('request_id') == key:
                    if config.get('input_result_id'):
                        WorkflowService(self.manager).send(config['input_result_id'], session['id'], actor=data['actor'],
                            request_key='continuation-'+key, note='Selected latest result for this explicit continuation.')
                    with self.store.connect(write=True) as db:
                        db.execute("UPDATE work_launch_requests SET state='created',session_id=?,updated_at=? WHERE request_key=?",
                                   (session['id'], utc_now(), key))
                    data.update(state='created', session_id=session['id'])
        return {key: data[key] for key in ['request_key', 'state', 'session_id', 'name', 'error', 'source_session_id']}

    def launch(self, request, *, expected_hash, request_key, actor, source_id=None):
        bounded_text(request_key, 'Request key', 100, True)
        request_hash = digest({'request': request, 'expected_hash': expected_hash, 'source': source_id})
        with self.store.connect() as db:
            old = db.execute('SELECT * FROM work_launch_requests WHERE request_key=?', (request_key,)).fetchone()
        if old:
            if old['request_hash'] != request_hash:
                raise ValueError('Request key was already used for a different launch')
            return self.launch_status(request_key)
        view = self.preview(request, source_id=source_id)
        if view['hash'] != expected_hash:
            raise ValueError('Launch configuration changed; review the current preview before launching')
        name = view['name'] or self.manager.generated_name(view['config']['tool'], view['config']['profile'])
        with self.store.connect(write=True) as db:
            inserted = db.execute('INSERT OR IGNORE INTO work_launch_requests VALUES(?,?,?,?,?,NULL,?,?,?,?)',
                                  (request_key, request_hash, name, source_id, 'creating', '', actor, utc_now(), utc_now())).rowcount
        if not inserted:
            return self.launch(request, expected_hash=expected_hash, request_key=request_key, actor=actor, source_id=source_id)
        try:
            session = self.manager.create(**view['config'], task=view['task'] or None, name=name,
                                          creator_surface='web', parent_session_id=source_id,
                                          _reviewed_launch=view, _launch_request=request_key,
                                          _continue_from=source_id)
            if view['input_result_id']:
                WorkflowService(self.manager).send(view['input_result_id'], session['id'], actor=actor,
                    request_key='continuation-'+request_key, note='Selected latest result for this explicit continuation.')
            with self.store.connect(write=True) as db:
                db.execute("UPDATE work_launch_requests SET state='created',session_id=?,updated_at=? WHERE request_key=?",
                           (session['id'], utc_now(), request_key))
                self.store.event(db, 'launch.created', session['id'], {'request_key': request_key, 'source': source_id}, actor)
        except Exception:
            with self.store.connect(write=True) as db:
                db.execute("UPDATE work_launch_requests SET state='unknown',error=?,updated_at=? WHERE request_key=?",
                           ('Launch was not acknowledged. Inspect its recorded session before starting another request.', utc_now(), request_key))
            raise
        return self.launch_status(request_key)
