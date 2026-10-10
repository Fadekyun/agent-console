"""Exact-result release grants and one-shot, observed external operations.

Adapters are operator-installed programs, never task text or imported skills.
A grant binds a selected commit, current evidence, action and configured target.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading

from .database import utc_now
from .task_runner import _active_group_members, _current_boot_id, _proc_start_time, process_identity_matches
from .workflow_service import WorkflowService
from .workflow_store import bounded_text, canonical, identifier

ACTIONS={'push','merge','deploy','release'}
ACTIVE={'queued','running','unknown'}


def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def regular(path,limit=1024*1024):
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_uid not in {0,os.getuid()}:
            raise ValueError('release configuration and adapters must be owner-controlled regular files')
        if info.st_size>limit:raise ValueError('release configuration or adapter exceeds size limit')
        return os.read(fd,limit+1)
    finally:os.close(fd)


class ReleaseService:
    def __init__(self,manager):
        self.manager=manager;self.svc=WorkflowService(manager);self.store=self.svc.store;self.graph=self.svc.graph()
        self.config_path=(manager.settings.config_dir or manager.settings.state_dir/'config')/'release-targets.json'
        with self.store.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS release_schema(version INTEGER NOT NULL)')
            row=db.execute('SELECT version FROM release_schema').fetchone()
            if row and row[0]!=1:raise ValueError('unsupported release schema')
            if not row:db.execute('INSERT INTO release_schema VALUES(1)')
            db.execute('''CREATE TABLE IF NOT EXISTS release_grants(
                id TEXT PRIMARY KEY,source_session_id TEXT NOT NULL,candidate_result_id TEXT NOT NULL REFERENCES results(id),
                target TEXT NOT NULL,action TEXT NOT NULL,preview_json TEXT NOT NULL,request_key TEXT UNIQUE NOT NULL,
                request_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS release_attempts(
                id TEXT PRIMARY KEY,grant_id TEXT NOT NULL REFERENCES release_grants(id),mode TEXT NOT NULL,
                state TEXT NOT NULL,request_key TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                pid INTEGER,start_time TEXT,boot_id TEXT,summary TEXT NOT NULL DEFAULT '',external_reference TEXT NOT NULL DEFAULT '',
                UNIQUE(grant_id,request_key))''')

    @contextmanager
    def lock(self):
        path=self.manager.settings.state_dir/'release-dispatch.lock'
        with path.open('a') as stream:
            path.chmod(0o600);fcntl.flock(stream,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

    def targets(self):
        if not self.config_path.exists():return []
        raw=json.loads(regular(self.config_path,65536))
        if not isinstance(raw,dict) or set(raw)!={'version','targets'} or raw['version']!=1 or not isinstance(raw['targets'],list) or len(raw['targets'])>20:
            raise ValueError('invalid release target configuration')
        result=[];seen=set()
        for item in raw['targets']:
            if not isinstance(item,dict) or set(item)!={'id','label','actions','apply','probe','timeout_seconds','environment'}:raise ValueError('invalid release target fields')
            identity=bounded_text(item['id'],'target ID',80,True)
            if not re.fullmatch('[a-z0-9][a-z0-9-]*',identity) or identity in seen:raise ValueError('invalid or duplicate release target')
            seen.add(identity);bounded_text(item['label'],'target label',120,True)
            if not isinstance(item['actions'],list) or not item['actions'] or set(item['actions'])-ACTIONS:raise ValueError('invalid release target actions')
            if type(item['timeout_seconds']) is not int or not 1<=item['timeout_seconds']<=300:raise ValueError('invalid release adapter timeout')
            if not isinstance(item['environment'],list) or len(item['environment'])>20:raise ValueError('invalid adapter environment references')
            for key in item['environment']:
                if not isinstance(key,str) or not re.fullmatch('[A-Z][A-Z0-9_]*',key) or key.startswith(('AGENT_CONSOLE_','AGCONSOLE_','PYTHON','LD_')):raise ValueError('invalid adapter environment reference')
            fingerprints={}
            for mode in ['apply','probe']:
                argv=item[mode]
                if not isinstance(argv,list) or not 1<=len(argv)<=16 or any(not isinstance(arg,str) for arg in argv):raise ValueError('adapter command must be an argument list')
                for arg in argv:bounded_text(arg,'adapter argument',2000,True)
                if not Path(argv[0]).is_absolute():raise ValueError('release adapter must have an absolute executable path')
                content=regular(argv[0],8*1024*1024)
                if not os.access(argv[0],os.X_OK):raise ValueError('release adapter is not executable')
                fingerprints[mode]=hashlib.sha256(content).hexdigest()
            result.append({**item,'fingerprint':digest([item,fingerprints])})
        return result

    def catalog(self):
        return [{k:item[k] for k in ['id','label','actions','fingerprint']} for item in self.targets()]

    def target(self,identity,action):
        item=next((t for t in self.targets() if t['id']==identity),None)
        if not item:raise ValueError('setup required: configure this release target before authorizing it')
        if action not in item['actions']:raise ValueError('this action is not configured for the release target')
        return item

    def _current_result(self,db,result_id):
        row=db.execute('SELECT * FROM results WHERE id=?',(result_id,)).fetchone()
        if not row:raise ValueError('selected result is unavailable')
        result=self.store.result_row(row)
        node=self.graph._logical(db,result['session_id']);native=self.graph._native(db,node)
        latest=db.execute('SELECT id FROM results WHERE session_id=? ORDER BY version DESC LIMIT 1',(native,)).fetchone()
        if not latest or latest[0]!=result_id:raise ValueError('selected candidate or evidence has been superseded')
        if result['kind']!='final' or result['outcome']!='pass':raise ValueError('release requires passing final candidate and evidence results')
        graph=self.graph._graph(db,node)
        if graph:
            ready=self.graph._readiness(db,self.graph._content(db,graph))[node]
            if ready.get('stale') or ready.get('blocked'):raise ValueError('candidate or evidence has stale or failed prerequisites')
        return result

    def preview(self,candidate_result_id,evidence_result_ids,action,target,*,db=None):
        if db is None:
            with self.store.connect() as connection:return self.preview(candidate_result_id,evidence_result_ids,action,target,db=connection)
        adapter=self.target(target,action)
        candidate=self._current_result(db,candidate_result_id)
        self._dispatch_allowed(db,candidate['session_id'])
        commits=[(i,a) for i,a in enumerate(candidate['artifacts']) if a['kind']=='commit']
        if len(commits)!=1:raise ValueError('select a final result containing exactly one commit snapshot')
        index,commit=commits[0]
        # Verify immutable bytes now and again immediately before the adapter runs.
        self.store.artifact(candidate_result_id,index)
        if not isinstance(evidence_result_ids,list) or not 1<=len(evidence_result_ids)<=16 or len(set(evidence_result_ids))!=len(evidence_result_ids):raise ValueError('select one or more distinct check results')
        evidence=[]
        for identity in sorted(evidence_result_ids):
            result=self._current_result(db,identity)
            if not result['checks']:raise ValueError('release evidence must describe actual checks')
            if identity!=candidate_result_id:
                consumed=self._consumed_before(db,result,candidate_result_id)
                if not consumed:raise ValueError('evidence must have consumed this exact candidate before publication')
            evidence.append({k:result[k] for k in ['id','session_id','version','content_hash','checks']})
        value={'candidate_result_id':candidate_result_id,'source_session_id':candidate['session_id'],
               'candidate_sha':commit['sha'],'artifact_index':index,'artifact_hash':commit['hash'],
               'repository':commit['repository'],'evidence':evidence,'action':action,'target':target,
               'target_label':adapter['label'],'target_fingerprint':adapter['fingerprint']}
        return {**value,'hash':digest(value)}

    def _root(self,db,session_id):
        graph=self.graph._graph(db,session_id)
        return graph['root_session_id'] if graph else session_id

    def _dispatch_allowed(self,db,session_id):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_policies'").fetchone():
            policy=db.execute('SELECT state FROM workflow_policies WHERE root_id=?',(self._root(db,session_id),)).fetchone()
            if policy and policy['state']!='running':raise ValueError('workflow is '+policy['state']+'; new release actions are disabled')

    def stop_workflow(self,root,actor):
        with self.lock():
            with self.store.connect(write=True) as db:
                attempts=[]
                for row in db.execute("SELECT a.*,g.source_session_id FROM release_attempts a JOIN release_grants g ON g.id=a.grant_id WHERE a.state IN ('queued','running')"):
                    if self._root(db,row['source_session_id'])==root:attempts.append(dict(row))
                for attempt in attempts:
                    db.execute("UPDATE release_attempts SET state='unknown',summary='Workflow stopped; reconcile the external outcome',updated_at=? WHERE id=?",(utc_now(),attempt['id']))
                    self.store.event(db,'release.stop_requested',attempt['grant_id'],{'attempt_id':attempt['id']},actor)
            for attempt in attempts:
                if process_identity_matches(attempt['pid'],attempt['start_time'],attempt['pid'],attempt['boot_id']):
                    try:os.kill(attempt['pid'],signal.SIGTERM)
                    except ProcessLookupError:pass

    @staticmethod
    def _consumed_before(db,result,candidate_id):
        # Event ordering is authoritative even when two events share a second.
        return db.execute("""SELECT 1 FROM inbox i JOIN workflow_events consumed
            ON consumed.target=i.id AND consumed.kind='input.consumed'
            JOIN workflow_events published ON published.target=? AND published.kind='result.published'
            WHERE i.target_session_id=? AND i.result_id=? AND i.state='consumed'
            AND consumed.sequence<published.sequence""",(result['id'],result['session_id'],candidate_id)).fetchone()

    def evidence_options(self,candidate_id):
        with self.store.connect() as db:
            candidate=self.store.result_row(db.execute('SELECT * FROM results WHERE id=?',(candidate_id,)).fetchone())
            rows=db.execute('''SELECT r.* FROM results r WHERE r.version=(SELECT MAX(version) FROM results WHERE session_id=r.session_id)
                AND (r.session_id=? OR r.session_id IN (SELECT target_session_id FROM inbox WHERE result_id=?))''',(candidate['session_id'],candidate_id)).fetchall()
            choices=[]
            for row in rows:
                result=self.store.result_row(row);reason=''
                try:
                    self._current_result(db,result['id'])
                    if not result['checks']:raise ValueError('No checks recorded')
                    if result['id']!=candidate_id and not self._consumed_before(db,result,candidate_id):raise ValueError('This result did not consume the selected candidate before publication')
                except ValueError as error:reason=str(error)
                choices.append({k:result[k] for k in ['id','session_id','version','summary','checks']}|{'eligible':not reason,'reason':reason})
        return choices

    def authorize(self,*,candidate_result_id,evidence_result_ids,action,target,expected_hash,request_key,actor):
        bounded_text(request_key,'request key',100,True)
        request_hash=digest([candidate_result_id,evidence_result_ids,action,target,expected_hash])
        with self.store.connect(write=True) as db:
            old=db.execute('SELECT * FROM release_grants WHERE request_key=?',(request_key,)).fetchone()
            if old:
                if old['request_hash']!=request_hash:raise ValueError('request key already authorizes different release work')
                identity=old['id']
            else:
                view=self.preview(candidate_result_id,evidence_result_ids,action,target,db=db)
                if view['hash']!=expected_hash:raise ValueError('release preview changed; review the candidate and evidence again')
                identity=identifier('release')
                db.execute('INSERT INTO release_grants VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,view['source_session_id'],candidate_result_id,target,action,canonical(view),request_key,request_hash,actor,utc_now()))
                self.store.event(db,'release.authorized',identity,{'candidate_sha':view['candidate_sha'],'action':action,'target':target,'evidence_ids':evidence_result_ids},actor)
        return self.inspect(identity)

    def _grant(self,db,identity):
        row=db.execute('SELECT * FROM release_grants WHERE id=?',(identity,)).fetchone()
        if not row:raise KeyError('release authorization not found')
        result=dict(row);result['preview']=json.loads(result.pop('preview_json'));return result

    @staticmethod
    def busy(attempt):
        if not attempt['pid']:return False
        boot=_current_boot_id()
        if not boot:return True
        if attempt['boot_id']!=boot:return False
        # The worker and its adapters share this dedicated process group. Keep
        # an orphan adapter blocking even after its worker leader has died.
        return _active_group_members(attempt['pid'])!={}

    def inspect(self,identity):
        with self.store.connect() as db:
            grant=self._grant(db,identity)
            grant['attempts']=[dict(r) for r in db.execute('SELECT * FROM release_attempts WHERE grant_id=? ORDER BY rowid',(identity,))]
        for attempt in grant['attempts']:
            attempt['active']=self.busy(attempt)
            if attempt['state'] in {'queued','running'} and not attempt['active']:attempt['state']='unknown'
        return grant

    def list(self,session_id):
        session=self.svc.session(session_id)
        with self.store.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM release_grants WHERE source_session_id=? ORDER BY created_at DESC LIMIT 50',(session['id'],))]
        return [self.inspect(identity) for identity in ids]

    def start(self,identity,*,mode,request_key,actor):
        if mode not in {'apply','probe'}:raise ValueError('invalid release operation')
        bounded_text(request_key,'request key',100,True)
        with self.lock():
            with self.store.connect(write=True) as db:
                grant=self._grant(db,identity);view=grant['preview']
                previous=db.execute('SELECT * FROM release_attempts WHERE grant_id=? AND request_key=?',(identity,request_key)).fetchone()
                if previous:
                    if previous['mode']!=mode:raise ValueError('request key used for a different operation')
                    return dict(previous)
                latest=db.execute('SELECT * FROM release_attempts WHERE grant_id=? ORDER BY rowid DESC LIMIT 1',(identity,)).fetchone()
                # Hold the target across all grants and both action classes.
                others=db.execute('SELECT a.* FROM release_attempts a JOIN release_grants g ON g.id=a.grant_id WHERE g.target=? AND a.rowid=(SELECT MAX(b.rowid) FROM release_attempts b WHERE b.grant_id=g.id)',(grant['target'],)).fetchall()
                for attempt in others:
                    if self.busy(attempt):raise ValueError('release target has an active adapter; wait for its result')
                    if attempt['grant_id']!=identity and attempt['state'] in ACTIVE:raise ValueError('reconcile the unresolved release for this target first')
                if mode=='apply':
                    if latest and latest['state']=='applied':raise ValueError('this release is already applied; inspect its recorded outcome')
                    if latest and latest['state']!='not-applied':raise ValueError('external outcome must be checked before any retry')
                    current=self.preview(view['candidate_result_id'],[e['id'] for e in view['evidence']],view['action'],view['target'],db=db)
                    if current['hash']!=view['hash']:raise ValueError('authorized candidate, evidence or target changed; review a new release')
                elif not latest:raise ValueError('there is no external attempt to reconcile')
                adapter=self.target(view['target'],view['action'])
                if adapter['fingerprint']!=view['target_fingerprint']:raise ValueError('configured target adapter changed; restore the reviewed adapter before reconciliation')
                attempt_id=identifier('release-attempt');now=utc_now()
                db.execute("INSERT INTO release_attempts(id,grant_id,mode,state,request_key,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?)",(attempt_id,identity,mode,request_key,now,now))
                self.store.event(db,'release.'+mode+'_requested',identity,{'attempt_id':attempt_id,'candidate_sha':view['candidate_sha'],'action':view['action'],'target':view['target']},actor)
            self._spawn(attempt_id,adapter,view)
            return self.attempt(attempt_id)

    def attempt(self,identity):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM release_attempts WHERE id=?',(identity,)).fetchone()
        if not row:raise KeyError('release attempt not found')
        return dict(row)

    def _spawn(self,attempt_id,adapter,view):
        from dataclasses import asdict
        folder=self.manager.settings.state_dir/'release-attempts'/attempt_id;folder.mkdir(parents=True,mode=0o700)
        settings={k:str(v) if isinstance(v,Path) else v for k,v in asdict(self.manager.settings).items()}
        manifest=folder/'worker.json';manifest.write_text(canonical({'settings':settings,'attempt_id':attempt_id,'view':view,'adapter':adapter}));manifest.chmod(0o600)
        source=str(Path(__file__).resolve().parent.parent)
        bootstrap=f'import sys;sys.path.insert(0,{source!r});from agent_console.release_runner import run;run(sys.argv[1])'
        environment={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR'}|set(adapter['environment'])}
        process=None
        try:
            process=subprocess.Popen([sys.executable,'-I','-c',bootstrap,str(manifest)],env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            start=_proc_start_time(process.pid);boot=_current_boot_id()
            if not start or not boot:raise RuntimeError('worker identity unavailable')
            with self.store.connect(write=True) as db:db.execute('UPDATE release_attempts SET pid=?,start_time=?,boot_id=? WHERE id=?',(process.pid,start,boot,attempt_id))
            threading.Thread(target=process.wait,daemon=True).start()
        except BaseException:
            with self.store.connect(write=True) as db:db.execute("UPDATE release_attempts SET state='unknown',summary='Release worker launch was not acknowledged; check the external outcome',updated_at=? WHERE id=?",(utc_now(),attempt_id))
            if process:threading.Thread(target=process.wait,daemon=True).start()
            raise
