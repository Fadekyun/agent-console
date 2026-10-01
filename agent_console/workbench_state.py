"""Operator work summaries: ownership, native attempts and outcomes stay distinct."""
import json
import shutil
import time
from fastapi import APIRouter, Depends

from .workflow_service import WorkflowService


def result_summary(result):
    if not result:return None
    return {key:result[key] for key in ('id','session_id','version','kind','outcome','created_at')} | {
        'summary':result['summary'][:1200],
        'artifacts':[{key:value for key,value in item.items() if key in {'kind','label','sha','hash'}} for item in result['artifacts']],
    }


class WorkbenchState:
    def __init__(self, manager):
        self.manager=manager;self._readiness=None;self._readiness_at=0

    def readiness(self, *, refresh=False):
        if not refresh and self._readiness and time.monotonic()-self._readiness_at<30:return self._readiness
        catalog=self.manager.tool_catalog();warnings=[];settings=self.manager.settings
        for available,label in [(settings.workspace_root.is_dir(),'Workspace is unavailable'),
                                (settings.profile_dir.is_dir(),'Role profiles are unavailable'),
                                (bool(shutil.which('tmux')),'Terminal service is unavailable')]:
            if not available:warnings.append(label)
        for tool in catalog:
            if tool['status']=='error':warnings.append(tool['name']+': '+(tool.get('reason') or 'setup needs attention'))
        if not any(tool['status']=='ready' and tool['name']!='shell' for tool in catalog):warnings.append('No agent harness is ready; set up a tool account')
        self._readiness={'ready':not warnings,'warnings':warnings,'tools':catalog};self._readiness_at=time.monotonic()
        return self._readiness

    def snapshot(self):
        sessions=self.manager.list_sessions();by_id={s['id']:s for s in sessions}
        svc=WorkflowService(self.manager);graph=svc.graph();store=svc.store
        with self.manager.database.connect() as db:
            projects={r['id']:r['name'] for r in db.execute('SELECT id,name FROM projects')}
        with store.connect() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            latest={r['session_id']:result_summary(store.result_row(r)) for r in db.execute('SELECT r.* FROM results r WHERE version=(SELECT MAX(version) FROM results WHERE session_id=r.session_id)')}
            steps={r['id']:dict(r) for r in db.execute('SELECT * FROM workflow_steps')} if 'workflow_steps' in tables else {}
            attempts=[dict(r) for r in db.execute('SELECT * FROM workflow_attempts ORDER BY generation')] if 'workflow_attempts' in tables else []
            policies={r['root_id']:r['state'] for r in db.execute('SELECT root_id,state FROM workflow_policies')} if 'workflow_policies' in tables else {}
            releases={}
            if 'release_attempts' in tables:
                from .workflow_release import ReleaseService
                for row in db.execute('''SELECT g.source_session_id,g.id,g.action,g.target,a.state,a.updated_at,a.pid,a.boot_id
                    FROM release_grants g JOIN release_attempts a ON a.grant_id=g.id
                    WHERE a.rowid=(SELECT MAX(rowid) FROM release_attempts WHERE grant_id=g.id)
                    ORDER BY a.updated_at'''):
                    entry=dict(row);previous=releases.get(row['source_session_id'])
                    if entry['state'] in {'queued','running'} and not ReleaseService.busy(entry):entry['state']='unknown'
                    entry.pop('pid');entry.pop('boot_id')
                    if not previous or previous['state']!='unknown':releases[row['source_session_id']]=entry
            bindings={r['node_id']:r['session_id'] for r in db.execute('SELECT * FROM work_node_bindings')}
            ownership={r['session_id']:dict(r) for r in db.execute('SELECT * FROM work_nodes')}
            readiness={}
            for row in db.execute('SELECT * FROM work_graphs'):
                readiness.update(graph._readiness(db,graph._content(db,dict(row))))
        alias={a['session_id']:a['step_id'] for a in attempts}
        alias.update({native:logical for logical,native in bindings.items()})
        attempts_by_step={key:[] for key in steps}
        for attempt in attempts:
            attempts_by_step.setdefault(attempt['step_id'],[]).append({key:attempt[key] for key in ('id','session_id','name','generation','state','created_at','updated_at','error')} | {'result':latest.get(attempt['session_id'])})
        nodes={}
        for identity in list(by_id)+[key for key in ownership if key not in by_id]:
            if identity in alias:continue
            step=steps.get(identity);history=attempts_by_step.get(identity,[]);attempt=history[-1] if history else None
            native_id=bindings.get(identity) or (attempt['session_id'] if attempt else identity)
            session=by_id.get(native_id);config=json.loads(step['config_json']) if step else {}
            owner=ownership[identity]['owner_id'] if identity in ownership else (session or {}).get('parent_session_id')
            owner=alias.get(owner,owner);result=latest.get(native_id)
            release=releases.get(native_id)
            running=bool(session and session['running']);state=attempt['state'] if attempt else None
            mechanical='running' if running else 'ended' if state=='completed' else 'stopped'
            result_state=('completed' if result['outcome']=='pass' and result['kind']=='final' else 'in-progress' if result['outcome']=='pass' else result['outcome'].replace('fail','failed')) if result else 'in-progress' if state=='running' or running else 'cancelled' if state=='cancelled' else 'failed' if state=='failed' else 'unknown'
            attention=(session or {}).get('attention_state') or 'normal'
            ready=readiness.get(identity,{})
            workflow_state=policies.get(step['root_id']) if step else policies.get(identity)
            waiting=bool(step and workflow_state!='stopped' and step['decision']!='rejected' and state not in {'running','completed','failed','cancelled','unknown'})
            if step and not result and workflow_state=='stopped':result_state='cancelled'
            if step and not attempt and step['decision']=='rejected':result_state='cancelled'
            evidence_at=(result or {}).get('created_at') or (attempt or {}).get('updated_at') or ''
            reviewed_at=(session or {}).get('attention_updated_at') or ''
            unresolved_failure=result_state in {'failed','blocked'} and workflow_state!='stopped' and (not reviewed_at or reviewed_at<evidence_at)
            needs_attention=attention!='normal' or state=='unknown' or unresolved_failure or bool(ready.get('stale') and workflow_state!='stopped') or bool(step and step['decision']=='proposed' and workflow_state!='stopped')
            needs_attention=needs_attention or bool(release and release['state']=='unknown')
            activity=max(filter(None,[(session or {}).get('last_activity'),(session or {}).get('created_at'),(session or {}).get('attention_updated_at'),(result or {}).get('created_at'),(attempt or {}).get('updated_at'),(step or {}).get('created_at')]),default='')
            activity=max(activity,(release or {}).get('updated_at',''))
            nodes[identity]={
                'id':identity,'owner_id':owner,'native_id':native_id if session else None,'native_name':(session or {}).get('tmux_name'),
                'title':step['task'] if step else (session or {}).get('tmux_name') or ownership[identity]['purpose'],
                'task':step['task'] if step else (session or {}).get('initial_task') or '',
                'repository':config.get('repository') or (session or {}).get('repository'),
                'project_id':config.get('project_id') or (session or {}).get('project_id'),
                'profile':config.get('profile') or (session or {}).get('profile'),
                'tool':config.get('tool') or (session or {}).get('tool'),
                'model':config.get('model') or (session or {}).get('model'),
                'mechanical':mechanical,'attention':attention,'result_state':result_state,'result':result,
                'waiting':waiting,'needs_attention':needs_attention,'last_activity':activity,
                'decision':step['decision'] if step else None,'attempt_state':state,'attempts':history,
                'readiness':ready,'error':(step or {}).get('error') or (attempt or {}).get('error') or '',
                'workflow_state':workflow_state,
                'release':release,
            }
        for node in nodes.values():
            node['project_name']=projects.get(node['project_id'])
            if node['owner_id'] not in nodes:node['owner_id']=None
            visited={node['id']};owner=node['owner_id'];root=node['id']
            while owner and owner not in visited:
                root=owner;visited.add(owner);owner=nodes[owner]['owner_id']
            # Corrupt legacy parentage must not make navigation disappear/loop.
            if owner:node['owner_id']=None;root=node['id']
            node['root_id']=root
        groups=[]
        for node in nodes.values():
            if node['owner_id']:continue
            members=[n for n in nodes.values() if n['root_id']==node['id']]
            priority=0 if any(n['needs_attention'] for n in members) else 1 if any(n['mechanical']=='running' for n in members) else 2 if any(n['waiting'] for n in members) else 3
            children=[n for n in members if n['id']!=node['id']]
            groups.append({'root_id':node['id'],'priority':priority,'last_activity':max(n['last_activity'] for n in members),
                           'children_total':len(children),'children_complete':sum(n['result_state']=='completed' and not n['readiness'].get('stale') for n in children),
                           'member_ids':[n['id'] for n in members]})
        groups.sort(key=lambda g:g['last_activity'],reverse=True);groups.sort(key=lambda g:g['priority'])
        return {'sessions':sessions,'nodes':list(nodes.values()),'groups':groups,'aliases':alias,'readiness':self.readiness()}

    def history(self, identity, before=2147483647, audit_before=2147483647):
        # Reading operator history must also work for imported/unmanaged and
        # integration records. The interactive-only gate belongs on mutations.
        with self.manager.database.connect() as db:
            session=db.execute('SELECT id,tmux_name FROM sessions WHERE id=? OR tmux_name=?',(identity,identity)).fetchone()
        if session is None:raise KeyError('session not found')
        svc=WorkflowService(self.manager);store=svc.store;graph=svc.graph()
        with store.connect() as db:
            logical=graph._logical(db,session['id'])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_attempts'").fetchone():
                old=db.execute('SELECT step_id FROM workflow_attempts WHERE session_id=?',(session['id'],)).fetchone()
                if old:logical=old[0]
            work=graph._graph(db,logical)
            work_id=work['id'] if work else None
            selection='target IN (?,?) OR workflow_id=? OR target IN (SELECT id FROM results WHERE session_id=?) OR target IN (SELECT id FROM inbox WHERE target_session_id=?) OR target IN (SELECT session_id FROM work_nodes WHERE graph_id=?)'
            parameters=[session['id'],logical,work_id,session['id'],session['id'],work_id]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='release_grants'").fetchone():
                selection+=' OR target IN (SELECT id FROM release_grants WHERE source_session_id=?)';parameters.append(session['id'])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_attempts'").fetchone():
                selection+=' OR target IN (SELECT a.id FROM workflow_attempts a JOIN work_nodes n ON n.session_id=a.step_id WHERE n.graph_id=?)';parameters.append(work_id)
            rows=db.execute('SELECT * FROM workflow_events WHERE sequence<? AND ('+selection+') ORDER BY sequence DESC LIMIT 50',(before,*parameters)).fetchall()
            events=[{'sequence':r['sequence'],'at':r['at'],'action':r['kind'],'actor':r['actor'],'target':r['target']} for r in rows]
        with self.manager.database.connect() as db:
            audit=[dict(r) for r in db.execute('SELECT id,created_at AS at,action,actor,outcome FROM audit_events WHERE id<? AND target IN (?,?) ORDER BY id DESC LIMIT 50',(audit_before,session['id'],session['tmux_name']))]
        return {'events':events,'audit':audit,'before':events[-1]['sequence'] if events else before,'audit_before':audit[-1]['id'] if audit else audit_before,'notice':'Workflow events include this connected work. Session audit entries are attributed to this session identity/name.'}


def workbench_routes(manager,require_identity):
    router=APIRouter(dependencies=[Depends(require_identity)]);state=WorkbenchState(manager)
    @router.get('/api/workbench')
    def snapshot():return state.snapshot()
    @router.get('/api/workbench/readiness')
    def readiness():return state.readiness(refresh=True)
    @router.get('/api/workbench/sessions/{identity}/history')
    def history(identity:str,before:int=2147483647,audit_before:int=2147483647):
        if before<1 or audit_before<1:raise ValueError('invalid history cursor')
        return state.history(identity,before,audit_before)
    return router
