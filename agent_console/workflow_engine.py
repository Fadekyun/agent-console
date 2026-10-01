"""Reviewed next steps and durable dispatch receipts for connected work."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import re
from pathlib import Path
import time

from .database import utc_now
from .profiles import PROFILE_SCHEMA, validate_profile_capability
from .providers import provider_adapter
from .skill_registry import SkillRegistry, read_deliveries
from .skills import _resolve_canonical_root, resolve_session_skills
from .validation import contained_path, validate_profile, validate_tool
from .workflow_service import WorkflowService
from .workflow_store import bounded_text, canonical, identifier

DEFAULT_POLICY={'mode':'suggestions','repositories':[],'actions':[],'roles':[],'harnesses':[],'targets':[],
                'max_concurrent':2,'max_total':4,'max_depth':2,'max_reruns':3}
CONFIG_KEYS={'tool','profile','repository','worktree','auth_context','agent_mode','model','reasoning_effort','project_id','action','target'}
ACTIVE={'reserved','creating','prepared','starting','running','unknown'}


class WorkflowEngine:
    def __init__(self,manager):
        self.manager=manager;self.svc=WorkflowService(manager);self.store=self.svc.store;self.graph=self.svc.graph()
        with self.store.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS dispatch_schema(version INTEGER NOT NULL)')
            row=db.execute('SELECT version FROM dispatch_schema').fetchone()
            if row and row[0]!=1:raise ValueError('unsupported workflow dispatch schema')
            if not row:db.execute('INSERT INTO dispatch_schema VALUES(1)')
            for sql in [
                '''CREATE TABLE IF NOT EXISTS workflow_policies(root_id TEXT PRIMARY KEY,state TEXT NOT NULL,
                   version INTEGER NOT NULL,policy_json TEXT NOT NULL,actor TEXT NOT NULL,updated_at TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS workflow_steps(id TEXT PRIMARY KEY,root_id TEXT NOT NULL,owner_id TEXT NOT NULL,
                   task TEXT NOT NULL,reason TEXT NOT NULL,expected_output TEXT NOT NULL,config_json TEXT NOT NULL,
                   decision TEXT NOT NULL,version INTEGER NOT NULL,approved_json TEXT,request_key TEXT NOT NULL,
                   request_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',
                   retry_requested INTEGER NOT NULL DEFAULT 0,UNIQUE(owner_id,request_key))''',
                '''CREATE TABLE IF NOT EXISTS workflow_attempts(id TEXT PRIMARY KEY,step_id TEXT NOT NULL REFERENCES workflow_steps(id),
                   generation INTEGER NOT NULL,step_version INTEGER NOT NULL,session_id TEXT UNIQUE NOT NULL,name TEXT UNIQUE NOT NULL,
                   state TEXT NOT NULL,input_signature TEXT NOT NULL,inputs_json TEXT NOT NULL,config_json TEXT NOT NULL,
                   error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                   pid INTEGER,start_time TEXT,pgid INTEGER,boot_id TEXT,exit_code INTEGER,runner_pid INTEGER,runner_start TEXT,runner_boot TEXT,retired_at TEXT,UNIQUE(step_id,generation))''',
            ]:db.execute(sql)

    def root(self,identity):
        with self.store.connect() as db:
            graph=self.graph._graph(db,identity)
            return graph['root_session_id'] if graph else self.svc.session(identity)['id']

    def policy(self,root,db=None):
        if db is None:
            with self.store.connect() as db:return self.policy(root,db)
        row=db.execute('SELECT * FROM workflow_policies WHERE root_id=?',(root,)).fetchone()
        return {**dict(row),'policy':json.loads(row['policy_json'])} if row else {'root_id':root,'state':'running','version':0,'policy':dict(DEFAULT_POLICY)}

    def configure(self,identity,*,policy,expected_version,actor):
        root=self.root(identity)
        if set(policy)!=set(DEFAULT_POLICY):raise ValueError('review the complete workflow envelope')
        if policy['mode'] not in {'suggestions','auto'}:raise ValueError('choose suggestions or auto')
        for key,maximum in [('max_concurrent',16),('max_total',100),('max_depth',10),('max_reruns',10)]:
            if type(policy[key]) is not int or not (0 if key=='max_reruns' else 1)<=policy[key]<=maximum:
                raise ValueError('invalid workflow limit: '+key)
        for key in ['repositories','actions','roles','harnesses','targets']:
            if not isinstance(policy[key],list) or len(policy[key])>30:raise ValueError('invalid envelope '+key)
            for value in policy[key]:bounded_text(value,key,2000,True)
            policy[key]=sorted(set(policy[key]))
        policy['repositories']=[str(contained_path(Path(p),self.manager.settings.workspace_root)) for p in policy['repositories']]
        if set(policy['actions'])-{'read','write','test'}:raise ValueError('release actions require the separate exact-candidate release gate')
        for role in policy['roles']:validate_profile(role)
        for tool in policy['harnesses']:validate_tool(tool)
        if policy['mode']=='auto' and any(not policy[k] for k in ['repositories','actions','roles','harnesses']):
            raise ValueError('auto mode needs explicit repositories, actions, roles and harnesses')
        with self.store.connect(write=True) as db:
            previous=self.policy(root,db)
            if previous['version']!=expected_version:raise ValueError('policy changed; reload before reviewing')
            if previous['state']=='stopped':raise ValueError('stopped workflows cannot be restarted; begin new work')
            db.execute('INSERT OR REPLACE INTO workflow_policies VALUES(?,?,?,?,?,?)',(root,previous['state'],expected_version+1,canonical(policy),actor,utc_now()))
            self.store.event(db,'workflow.envelope_reviewed',root,{'version':expected_version+1,'policy':policy},actor)
        return self.inspect(root)

    def normalize(self,root,config):
        if set(config)-CONFIG_KEYS:raise ValueError('unknown launch configuration field')
        for key,value in config.items():
            if key!='worktree' and value is not None and not isinstance(value,str):raise ValueError('launch configuration values must be text')
        parent=self.svc.session(root)
        data={key:value for key,value in config.items() if value is not None};data.setdefault('tool',parent['tool']);data.setdefault('profile',parent['profile'])
        data.setdefault('repository',parent.get('repository') or str(self.manager.settings.workspace_root))
        data['repository']=str(contained_path(Path(data['repository']),self.manager.settings.workspace_root))
        validate_tool(data['tool']);validate_profile(data['profile'])
        read_only=PROFILE_SCHEMA[data['profile']]['read_write_capability']=='read_only'
        data.setdefault('action','read' if read_only else 'write');data.setdefault('target','')
        if data['action'] not in {'read','write','test'}:raise ValueError('unsupported action; release needs an exact-candidate gate')
        if read_only and data['action']=='write':raise ValueError('a read-only role cannot perform a write step')
        data.setdefault('worktree',not read_only)
        if type(data['worktree']) is not bool:raise ValueError('worktree must be boolean')
        for key,value in data.items():
            if isinstance(value,str):bounded_text(value,key,4000)
        return data

    def preview(self,root,config):
        config=self.normalize(root,config)
        capability=validate_profile_capability(config['profile'],config['tool'],config.get('agent_mode'),worktree=config['worktree'])
        if not capability['allowed']:raise ValueError(capability['reason'])
        adapter=provider_adapter(config['tool'],self.manager.auth)
        if not adapter.can_run_workflow_task:raise ValueError('setup required: this harness has no verified native workflow input adapter')
        adapter_info=adapter.workflow_info()
        if not adapter_info['supported']:raise ValueError('setup required: '+adapter_info['reason'])
        context=adapter.availability(config.get('auth_context'))
        if context['status']!='ready':raise ValueError('setup required: '+str(context.get('reason') or context['status']))
        config['auth_context']=context['name']
        selected=resolve_session_skills(self.manager.database,config['profile'],config['tool'],shared_allowlist=self.manager.settings.shared_skills,repository=config['repository'])
        if not selected['validation']['valid']:raise ValueError('; '.join(selected['validation']['issues']))
        if selected['validation']['effective'] and not adapter.can_isolate_skills:raise ValueError('setup required: selected skills cannot be isolated')
        registry=SkillRegistry(_resolve_canonical_root(),self.manager.database.path.parent)
        skills=sorted([{'name':s['name'],'hash':registry.inspect(s['name'])['hash']} for s in selected['materialized']],key=lambda s:s['name'])
        value={'config':config,'skills':skills,'adapter':adapter_info}
        return {**value,'hash':hashlib.sha256(canonical(value).encode()).hexdigest()}

    def propose(self,identity,*,task,reason,expected_output,config,dependencies,request_key,actor):
        for label,value,limit in [('task',task,12000),('reason',reason,4000),('expected output',expected_output,4000),('request key',request_key,100)]:bounded_text(value,label,limit,True)
        owner=self.svc.session(identity)['id'];root=self.root(owner);config=self.normalize(root,config)
        request_hash=hashlib.sha256(canonical([task,reason,expected_output,config,dependencies]).encode()).hexdigest()
        with self.store.connect(write=True) as db:
            if self.policy(root,db)['state']=='stopped':raise ValueError('workflow is stopped')
            owner=self.graph._logical(db,owner)
            existing=db.execute('SELECT * FROM workflow_steps WHERE owner_id=? AND request_key=?',(owner,request_key)).fetchone()
            if existing:
                if existing['request_hash']!=request_hash:raise ValueError('request key already used for a different proposal')
                return self.step(existing['id'])
            graph=self.graph._graph(db,owner)
            if not graph:
                graph={'id':identifier('work'),'root_session_id':root,'version':0}
                db.execute('INSERT INTO work_graphs VALUES(?,?,?)',(graph['id'],root,0))
                db.execute('INSERT INTO work_nodes VALUES(?,?,NULL,?)',(owner,graph['id'],'Initial session'))
            if db.execute('SELECT COUNT(*) FROM work_nodes WHERE graph_id=?',(graph['id'],)).fetchone()[0]>=100:raise ValueError('workflow node limit reached')
            step_id=identifier('step')
            db.execute('INSERT INTO work_nodes VALUES(?,?,?,?)',(step_id,graph['id'],owner,task))
            self.graph._edges(db,graph,step_id,dependencies)
            self.graph._revision(db,graph,actor)
            db.execute('INSERT INTO workflow_steps(id,root_id,owner_id,task,reason,expected_output,config_json,decision,version,request_key,request_hash,actor,created_at) VALUES(?,?,?,?,?,?,?,\'proposed\',1,?,?,?,?)',
                       (step_id,root,owner,task,reason,expected_output,canonical(config),request_key,request_hash,actor,utc_now()))
            self.store.event(db,'workflow.proposed',step_id,{'task':task,'reason':reason,'expected_output':expected_output},actor,graph['id'])
        if self.policy(root)['policy']['mode']=='auto':
            try:
                preview=self.preview(root,config)
                self.decide(step_id,decision='accepted',expected_version=1,preview_hash=preview['hash'],actor='envelope:'+root,automatic=True)
            except (ValueError,RuntimeError) as error:self._error(step_id,str(error))
        return self.step(step_id)

    def step(self,step_id):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM workflow_steps WHERE id=?',(step_id,)).fetchone()
            if not row:raise KeyError('workflow step not found')
            data=dict(row);data['config']=json.loads(data.pop('config_json'));data['approved']=json.loads(data.pop('approved_json') or 'null')
            data['dependencies']=[{'source_id':r['source_id'],'readiness':r['readiness']} for r in db.execute('SELECT source_id,readiness FROM work_edges WHERE target_id=? ORDER BY source_id',(step_id,))]
            data['attempts']=[dict(r) for r in db.execute('SELECT * FROM workflow_attempts WHERE step_id=? ORDER BY generation',(step_id,))]
            for a in data['attempts']:
                a['inputs']=json.loads(a.pop('inputs_json'));a.pop('config_json');a.pop('pid');a.pop('start_time');a.pop('pgid');a.pop('boot_id');a.pop('runner_pid');a.pop('runner_start');a.pop('runner_boot')
                results=self.store.results(a['session_id'],limit=1);a['result']={key:results[0][key] for key in ('id','kind','outcome')} if results else None
            return data

    def _error(self,step_id,error):
        with self.store.connect(write=True) as db:db.execute('UPDATE workflow_steps SET error=? WHERE id=?',(error[:1000],step_id))

    def _limits(self,db,step,policy,automatic):
        accepted=db.execute("SELECT COUNT(*) FROM workflow_steps WHERE root_id=? AND decision='accepted' AND id!=?",(step['root_id'],step['id'])).fetchone()[0]
        graph=self.graph._graph(db,step['root_id'])
        base=db.execute('SELECT COUNT(*) FROM work_nodes n WHERE graph_id=? AND NOT EXISTS(SELECT 1 FROM workflow_steps s WHERE s.id=n.session_id)',(graph['id'],)).fetchone()[0]
        if accepted+base+1>policy['max_total']:raise ValueError('total session limit reached; review the workflow budget')
        depth=0;owner=step['id']
        while owner:
            node=db.execute('SELECT owner_id FROM work_nodes WHERE session_id=?',(owner,)).fetchone()
            owner=node[0] if node else None
            if owner:depth+=1
        if depth>policy['max_depth']:raise ValueError('descendant depth limit reached')
        if automatic:
            cfg=json.loads(step['config_json'])
            for key,field in [('repositories','repository'),('roles','profile'),('harnesses','tool'),('actions','action')]:
                if cfg[field] not in policy[key]:raise ValueError('proposal is outside the reviewed '+key)
            if cfg.get('target') and cfg['target'] not in policy['targets']:raise ValueError('proposal is outside the reviewed targets')

    def decide(self,step_id,*,decision,expected_version,actor,preview_hash=None,automatic=False):
        if decision not in {'accepted','rejected'}:raise ValueError('choose accepted or rejected')
        step=self.step(step_id);preview=self.preview(step['root_id'],step['config']) if decision=='accepted' else None
        if preview and preview_hash!=preview['hash']:raise ValueError('launch preview changed; inspect and approve it again')
        if preview:preview['authority']='envelope' if automatic else 'operator'
        with self.store.connect(write=True) as db:
            row=db.execute('SELECT * FROM workflow_steps WHERE id=?',(step_id,)).fetchone()
            if row['version']!=expected_version:raise ValueError('proposal changed; reload before reviewing')
            if row['decision']!='proposed':raise ValueError('proposal already reviewed')
            policy=self.policy(row['root_id'],db)
            if policy['state']=='stopped':raise ValueError('workflow is stopped')
            if decision=='accepted':self._limits(db,row,policy['policy'],automatic)
            db.execute('UPDATE workflow_steps SET decision=?,approved_json=?,version=version+1,error=\'\' WHERE id=?',(decision,canonical(preview) if preview else None,step_id))
            self.store.event(db,'workflow.'+decision,step_id,{'version':expected_version+1,'preview_hash':preview_hash},actor)
        return self.step(step_id)

    def control(self,identity,*,state,actor):
        if state not in {'running','paused','stopped'}:raise ValueError('invalid workflow state')
        root=self.root(identity)
        with self.store.connect(write=True) as db:
            previous=self.policy(root,db)
            if previous['state']=='stopped' and state!='stopped':raise ValueError('stopped workflow cannot be resumed')
            db.execute('INSERT OR REPLACE INTO workflow_policies VALUES(?,?,?,?,?,?)',(root,state,previous['version']+1,canonical(previous['policy']),actor,utc_now()))
            self.store.event(db,'workflow.'+state,root,{},actor)
        return self.inspect(root)

    def inspect(self,identity):
        root=self.root(identity)
        with self.store.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM workflow_steps WHERE root_id=? ORDER BY created_at,id',(root,))]
        return {'root_id':root,'policy':self.policy(root),'steps':[self.step(i) for i in ids],'graph':self.graph.inspect(root)}

    @contextmanager
    def dispatch_lock(self):
        path=self.manager.settings.state_dir/'workflow-dispatch.lock'
        with path.open('a') as stream:
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:yield False;return
            try:yield True
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

    def _attempt(self,attempt_id):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM workflow_attempts WHERE id=?',(attempt_id,)).fetchone()
            if not row:raise KeyError('workflow attempt not found')
            return dict(row)

    def _attempt_state(self,attempt_id,state,error=''):
        with self.store.connect(write=True) as db:
            db.execute('UPDATE workflow_attempts SET state=?,error=?,updated_at=? WHERE id=?',(state,error[:1000],utc_now(),attempt_id))
            self.store.event(db,'workflow.attempt_'+state,attempt_id,{'reason':error[:1000]},'dispatcher')

    def _owned_session(self,attempt):
        with self.manager.database.connect() as db:
            row=db.execute('SELECT id,tmux_name,creator_surface FROM sessions WHERE id=?',(attempt['session_id'],)).fetchone()
        if not row:return None
        if row['creator_surface']!='workflow:'+attempt['id']:raise ValueError('workflow session ownership mismatch')
        return self.manager.inspect(row['tmux_name'])

    def retire(self,attempt):
        session=self._owned_session(attempt)
        if session is None and self.manager.tmux.exists(attempt['name']):raise ValueError('Unregistered launch terminal needs reconciliation')
        if session and session['running']:
            # Preserve viewable output before releasing terminal capacity. Never
            # retire a session not attributed to this exact launch receipt.
            self.manager.archive(session['tmux_name'])
            self.manager.kill(session['tmux_name'])
        with self.store.connect(write=True) as db:db.execute('UPDATE workflow_attempts SET retired_at=? WHERE id=?',(utc_now(),attempt['id']))

    def tick(self):
        """One bounded sweep; multiple web workers share a process-level lock."""
        with self.dispatch_lock() as owned:
            if not owned:return
            with self.store.connect() as db:sequence=db.execute('SELECT COALESCE(MAX(sequence),0) FROM workflow_events').fetchone()[0]
            with self.manager.database.connect() as db:
                live=tuple(r[0] for r in db.execute("SELECT id FROM sessions WHERE status IN ('reserved','attached','detached') ORDER BY id"))
                held=db.execute('SELECT COUNT(*) FROM integration_requests WHERE admission_held=1').fetchone()[0]
            fingerprint=(sequence,live,held)
            with self.store.connect() as db:
                attempts=[dict(r) for r in db.execute("SELECT * FROM workflow_attempts WHERE state IN ('reserved','creating','prepared','starting','running','unknown') ORDER BY created_at")]
            if not attempts and getattr(self,'_last_fingerprint',None)==fingerprint:return
            self._last_fingerprint=fingerprint
            for attempt in attempts:
                try:
                    step=self.step(attempt['step_id']);policy=self.policy(step['root_id'])
                    if policy['state']=='stopped':
                        self._attempt_state(attempt['id'],'cancelled','Workflow stopped')
                        self._terminate(attempt);continue
                    if attempt['state']=='starting':
                        from datetime import datetime, timezone
                        if (datetime.now(timezone.utc)-datetime.fromisoformat(attempt['updated_at'])).total_seconds()>120:
                            self._attempt_state(attempt['id'],'unknown','Native startup was not acknowledged; reconcile before retry')
                        continue
                    if attempt['state']=='prepared':
                        session=self._owned_session(attempt)
                        if not session or not session['running']:
                            current=self._attempt(attempt['id'])
                            if current['state']=='prepared':self._attempt_state(attempt['id'],'unknown','Prepared terminal disappeared before native startup; reconcile before retry')
                        continue
                    if attempt['state']=='running':
                        from .task_runner import process_identity_matches, _proc_start_time, _current_boot_id
                        if attempt['runner_pid'] and attempt['runner_start'] and _proc_start_time(attempt['runner_pid'])==attempt['runner_start'] and _current_boot_id()==attempt['runner_boot']:continue
                        if attempt['pid'] and process_identity_matches(attempt['pid'],attempt['start_time'],attempt['pgid'],attempt['boot_id']):
                            self._attempt_state(attempt['id'],'unknown','Task is still running but its supervising runner disappeared');continue
                        # The runner sets the terminal outcome before it exits. A
                        # missing handle without that receipt is explicitly uncertain.
                        current=self._attempt(attempt['id'])
                        if current['state']=='running':self._attempt_state(attempt['id'],'unknown','Native task handle disappeared without a completion receipt')
                        continue
                    if attempt['state']=='creating':
                        session=self._owned_session(attempt)
                        if session:self._prepare(attempt,session)
                        else:self._attempt_state(attempt['id'],'unknown','Launch interrupted before the session record was committed; reconcile before retry')
                    elif attempt['state']=='reserved' and policy['state']=='running':
                        from datetime import datetime, timezone
                        if not attempt['error'] or (datetime.now(timezone.utc)-datetime.fromisoformat(attempt['updated_at'])).total_seconds()>=30:self._launch(attempt)
                except Exception as error:
                    self._attempt_state(attempt['id'],'unknown','Reconciliation needs attention: '+type(error).__name__)
            # Reap completed owned terminals even while paused; pause affects new
            # dispatch, not the already-authorized attempt's completion/history.
            with self.store.connect() as db:
                finished=[dict(r) for r in db.execute("SELECT * FROM workflow_attempts WHERE state IN ('completed','failed','cancelled') AND retired_at IS NULL")]
            for attempt in finished:
                try:self.retire(attempt)
                except (KeyError,RuntimeError,ValueError) as error:self._error(attempt['step_id'],'Cleanup needs attention: '+str(error))
            with self.store.connect() as db:stopped=[r[0] for r in db.execute("SELECT root_id FROM workflow_policies WHERE state='stopped'")]
            for root in stopped:
                graph=self.graph.inspect(root)
                base_ids=[n['session_id'] for n in graph['nodes'] if not n['session_id'].startswith('step-')] or [root]
                for session_id in base_ids:
                    try:
                        session=self.svc.session(session_id)
                        if session['running']:
                            self.manager.archive(session['tmux_name']);self.manager.kill(session['tmux_name'])
                    except KeyError:pass
            with self.store.connect() as db:ids=[r[0] for r in db.execute("SELECT id FROM workflow_steps WHERE decision='accepted' ORDER BY created_at,id")]
            for step_id in ids:
                try:self._schedule(step_id)
                except (ValueError,RuntimeError,FileNotFoundError) as error:self._error(step_id,str(error))

    def _schedule(self,step_id):
        step=self.step(step_id);policy=self.policy(step['root_id'])
        if policy['state']!='running':return
        attempts=step['attempts']
        if any(a['state'] in ACTIVE for a in attempts):return
        graph=self.graph.inspect(step_id);ready=graph['readiness'][step_id]
        if ready['blocked']:self._error(step_id,'; '.join(ready['reasons']));return
        latest=attempts[-1] if attempts else None
        if latest and not step['retry_requested'] and latest['input_signature']==ready['signature'] and latest['step_version']==step['version']:return
        if len(attempts)>policy['policy']['max_reruns']:raise ValueError('recomputation limit reached; review the workflow budget')
        preview=self.preview(step['root_id'],step['config'])
        if not step['approved'] or preview['hash']!=step['approved']['hash']:raise ValueError('approved skills/configuration changed; edit and review the step again')
        with self.store.connect(write=True) as db:
            current=db.execute('SELECT * FROM workflow_steps WHERE id=?',(step_id,)).fetchone()
            if current['version']!=step['version'] or current['decision']!='accepted':return
            current_policy=self.policy(step['root_id'],db)
            if current_policy['state']!='running':return
            self._limits(db,current,current_policy['policy'],step['approved'].get('authority')=='envelope')
            active=db.execute("SELECT COUNT(*) FROM workflow_attempts a JOIN workflow_steps s ON s.id=a.step_id WHERE s.root_id=? AND a.state IN ('reserved','creating','prepared','starting','running','unknown')",(step['root_id'],)).fetchone()[0]
            graph_info=self.graph._graph(db,step['root_id'])
            base_ids=[r[0] for r in db.execute('SELECT session_id FROM work_nodes n WHERE graph_id=? AND NOT EXISTS(SELECT 1 FROM workflow_steps s WHERE s.id=n.session_id)',(graph_info['id'],))]
            with self.manager.database.connect() as sessions_db:
                existing_active=sum(bool(sessions_db.execute("SELECT 1 FROM sessions WHERE id=? AND status IN ('reserved','attached','detached')",(identity,)).fetchone()) for identity in base_ids)
            if active+existing_active>=current_policy['policy']['max_concurrent']:raise ValueError('waiting for workflow concurrency capacity')
            total=db.execute('SELECT COUNT(*) FROM workflow_attempts a JOIN workflow_steps s ON s.id=a.step_id WHERE s.root_id=?',(step['root_id'],)).fetchone()[0]
            if total+len(base_ids)>=current_policy['policy']['max_total']:raise ValueError('total launched-session budget reached; review before adding attempts')
            attempt_id=identifier('attempt');session_id=identifier('sess');name='flow-'+(re.sub('[^a-z0-9]+','-',step['task'].lower()).strip('-')[:35] or 'task')+'-'+attempt_id[8:16]
            frozen={**preview,'task':step['task'],'expected_output':step['expected_output'],'authority':step['approved'].get('authority','operator')}
            db.execute('INSERT INTO workflow_attempts(id,step_id,generation,step_version,session_id,name,state,input_signature,inputs_json,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,\'reserved\',?,?,?,?,?)',
                       (attempt_id,step_id,len(attempts)+1,step['version'],session_id,name,ready['signature'],canonical(ready['inputs']),canonical(frozen),utc_now(),utc_now()))
            db.execute("UPDATE workflow_steps SET retry_requested=0,error='' WHERE id=?",(step_id,))
            self.store.event(db,'workflow.launch_reserved',attempt_id,{'step_id':step_id,'session_id':session_id,'generation':len(attempts)+1},'dispatcher')
        self._launch(self._attempt(attempt_id))

    def _launch(self,attempt):
        frozen=json.loads(attempt['config_json']);step=self.step(attempt['step_id'])
        if self.policy(step['root_id'])['state']!='running':return
        self._attempt_state(attempt['id'],'creating')
        config={k:v for k,v in frozen['config'].items() if k not in {'action','target'}}
        with self.store.connect() as db:parent=self.graph._native(db,step['owner_id'])
        try:
            session=self.manager.create(**config,name=attempt['name'],task=step['task'],parent_session_id=parent,
                creator_surface='workflow:'+attempt['id'],_workflow_attempt=attempt['id'],_workflow_session_id=attempt['session_id'])
        except Exception as error:
            # No native task can run while its receipt is merely creating. Keep
            # the exact name/ID for reconciliation; never mint a second launch.
            if self.manager.tmux.exists(attempt['name']):self._attempt_state(attempt['id'],'unknown','Creation failed with a remaining terminal: '+str(error));return
            if isinstance(error,RuntimeError) and str(error).startswith(('child-session limit reached','managed-session limit reached')):
                self._attempt_state(attempt['id'],'reserved',str(error));self._error(step['id'],'Waiting for Console capacity');return
            self._attempt_state(attempt['id'],'failed',str(error));self._error(step['id'],str(error));return
        self._prepare(attempt,session)

    def _prepare(self,attempt,session):
        frozen=json.loads(attempt['config_json']);delivery=read_deliveries(self.manager.settings.state_dir,session['id'])
        actual=sorted([{'name':s['name'],'hash':s['hash']} for s in (delivery.get('latest') or {}).get('skills',[])],key=lambda s:s['name'])
        if actual!=frozen['skills']:
            self._attempt_state(attempt['id'],'failed','Skill content changed during launch; review again');self.retire(attempt);return
        with self.store.connect(write=True) as db:
            db.execute('INSERT OR REPLACE INTO work_node_bindings VALUES(?,?)',(attempt['step_id'],session['id']))
        try:
            graph=self.graph.inspect(attempt['step_id'])
            result=self.graph.deliver(attempt['step_id'],expected_version=graph['version'],expected_signature=attempt['input_signature'],actor='dispatcher')
        except (ValueError,KeyError) as error:
            self._attempt_state(attempt['id'],'cancelled','Input changed before native startup: '+str(error));self.retire(attempt);return
        with self.store.connect(write=True) as db:
            db.execute("UPDATE workflow_attempts SET state='prepared',inputs_json=?,updated_at=? WHERE id=? AND state='creating'",(result['inputs_json'],utc_now(),attempt['id']))

    def _terminate(self,attempt):
        from .task_runner import process_identity_matches, terminate_owned_group
        current=self._attempt(attempt['id'])
        if current['pid'] and process_identity_matches(current['pid'],current['start_time'],current['pgid'],current['boot_id']):
            if not terminate_owned_group(current['pid'],current['start_time'],current['pgid'],current['boot_id'],grace_seconds=2):
                self._attempt_state(attempt['id'],'unknown','Process termination could not be verified');return
        self.retire(current)

    def retry(self,step_id,*,actor):
        step=self.step(step_id)
        if any(a['state'] in ACTIVE for a in step['attempts']):raise ValueError('current attempt must finish or be reconciled first')
        with self.store.connect(write=True) as db:
            db.execute('UPDATE workflow_steps SET retry_requested=1 WHERE id=?',(step_id,))
            self.store.event(db,'workflow.retry_requested',step_id,{},actor)
        return self.step(step_id)

    def edit(self,step_id,*,task,reason,expected_output,config,dependencies,expected_version,actor):
        step=self.step(step_id);config=self.normalize(step['root_id'],config)
        for label,value,limit in [('task',task,12000),('reason',reason,4000),('expected output',expected_output,4000)]:bounded_text(value,label,limit,True)
        with self.store.connect(write=True) as db:
            row=db.execute('SELECT * FROM workflow_steps WHERE id=?',(step_id,)).fetchone()
            if row['version']!=expected_version:raise ValueError('proposal changed; reload before editing')
            graph=self.graph._graph(db,step_id);self.graph._edges(db,graph,step_id,dependencies);self.graph._revision(db,graph,actor)
            db.execute("UPDATE workflow_steps SET task=?,reason=?,expected_output=?,config_json=?,decision='proposed',approved_json=NULL,version=version+1,error='' WHERE id=?",(task,reason,expected_output,canonical(config),step_id))
            db.execute('UPDATE work_nodes SET purpose=? WHERE session_id=?',(task,step_id))
            self.store.event(db,'workflow.edited',step_id,{'version':expected_version+1},actor)
        return self.step(step_id)

    def reconcile(self,attempt_id,*,outcome,summary,actor):
        """Record an operator-observed outcome; never turn uncertainty into a retry."""
        from .task_runner import _active_group_members, _proc_start_time, _current_boot_id
        if outcome not in {'not-started','pass','fail','blocked'}:raise ValueError('invalid reconciliation outcome')
        bounded_text(summary,'reconciliation evidence',4000,True)
        with self.dispatch_lock() as owned:
            if not owned:raise ValueError('dispatcher is busy; retry reconciliation shortly')
            attempt=self._attempt(attempt_id)
            if attempt['state']!='unknown':raise ValueError('only an uncertain attempt needs reconciliation')
            if attempt['runner_pid'] and attempt['runner_start'] and _proc_start_time(attempt['runner_pid'])==attempt['runner_start'] and _current_boot_id()==attempt['runner_boot']:
                raise ValueError('supervising runner is still active; wait or stop the workflow')
            if attempt['pgid'] and _current_boot_id()==attempt['boot_id'] and _active_group_members(attempt['pgid'])!={}:
                raise ValueError('native process group is still active or unobservable; reconcile it before recording an outcome')
            if outcome=='not-started' and attempt['runner_pid']:
                raise ValueError('native startup was claimed; record the actual observed outcome instead')
            session=self._owned_session(attempt)
            if outcome!='not-started':
                if not session:raise ValueError('native session identity is missing; cannot attribute a result')
                self.svc.publish(session['id'],{'kind':'final','outcome':outcome,'summary':summary,'checks':['Operator reconciled uncertain attempt '+attempt_id],
                    'artifacts':[],'request_key':'reconciled-'+attempt_id},actor)
            elif session is None and self.manager.tmux.exists(attempt['name']):
                manifest=self.manager.settings.state_dir/'workflow-attempts'/attempt_id/'launch.json'
                if not manifest.is_file() or json.loads(manifest.read_text()).get('attempt_id')!=attempt_id:
                    raise ValueError('unregistered terminal ownership cannot be verified')
                # Startup could never pass the receipt gate; the operator explicitly
                # reconciled this exact unregistered launch, not an unrelated pane.
                captured,_=self.manager.tmux.capture(attempt['name'])
                transcript=self.manager.settings.state_dir/'transcripts'/(attempt_id+'.txt')
                transcript.write_text(captured);transcript.chmod(0o600)
                self.manager.tmux.kill(attempt['name'])
            self._attempt_state(attempt_id,'cancelled' if outcome=='not-started' else 'completed',summary)
            self.retire(self._attempt(attempt_id))
            with self.store.connect(write=True) as db:self.store.event(db,'workflow.outcome_reconciled',attempt_id,{'outcome':outcome,'evidence':summary},actor)
            return self.step(attempt['step_id'])
