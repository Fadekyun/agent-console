"""Versioned input dependencies between existing sessions.

Ownership is stored separately from readiness edges. Connecting an already
running session does not restart it or send keystrokes. Inputs are explicitly
queued into the same durable inbox used by native agents.
"""
import hashlib
import json
from .database import utc_now
from .workflow_store import WorkflowStore, bounded_text, canonical, identifier


def content_identity(result):
    # Promoting an unchanged checkpoint to final should not stale its consumers.
    value={key:result[key] for key in ('outcome','summary','checks','artifacts')}
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class WorkflowGraph:
    def __init__(self, store: WorkflowStore):
        self.store=store
        with store.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS graph_schema(version INTEGER NOT NULL)')
            row=db.execute('SELECT version FROM graph_schema').fetchone()
            if row and row[0]!=1:raise ValueError('unsupported workflow graph schema')
            if not row:db.execute('INSERT INTO graph_schema VALUES(1)')
            for sql in [
                'CREATE TABLE IF NOT EXISTS work_graphs(id TEXT PRIMARY KEY,root_session_id TEXT UNIQUE NOT NULL,version INTEGER NOT NULL)',
                '''CREATE TABLE IF NOT EXISTS work_nodes(session_id TEXT PRIMARY KEY,graph_id TEXT NOT NULL REFERENCES work_graphs(id),
                   owner_id TEXT REFERENCES work_nodes(session_id),purpose TEXT NOT NULL)''',
                'CREATE TABLE IF NOT EXISTS work_node_bindings(node_id TEXT PRIMARY KEY REFERENCES work_nodes(session_id),session_id TEXT UNIQUE NOT NULL)',
                '''CREATE TABLE IF NOT EXISTS work_edges(graph_id TEXT NOT NULL REFERENCES work_graphs(id),
                   source_id TEXT NOT NULL REFERENCES work_nodes(session_id),target_id TEXT NOT NULL REFERENCES work_nodes(session_id),
                   readiness TEXT NOT NULL CHECK(readiness IN ('after-ready','after-final','alongside')),pinned_result_id TEXT REFERENCES results(id),
                   PRIMARY KEY(source_id,target_id))''',
                '''CREATE TABLE IF NOT EXISTS work_revisions(graph_id TEXT NOT NULL REFERENCES work_graphs(id),version INTEGER NOT NULL,
                   content_json TEXT NOT NULL,actor TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(graph_id,version))''',
                'CREATE TABLE IF NOT EXISTS graph_result_inputs(result_id TEXT PRIMARY KEY REFERENCES results(id),signature TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS work_active_deliveries(target_id TEXT PRIMARY KEY,delivery_id TEXT NOT NULL)',
                '''CREATE TABLE IF NOT EXISTS work_deliveries(id TEXT PRIMARY KEY,graph_id TEXT NOT NULL REFERENCES work_graphs(id),
                   target_id TEXT NOT NULL REFERENCES work_nodes(session_id),inputs_json TEXT NOT NULL,signature TEXT NOT NULL,
                   actor TEXT NOT NULL,at TEXT NOT NULL,UNIQUE(target_id,signature))''',
            ]:db.execute(sql)

    @staticmethod
    def _native(db,node_id):
        row=db.execute('SELECT session_id FROM work_node_bindings WHERE node_id=?',(node_id,)).fetchone()
        return row[0] if row else node_id

    @staticmethod
    def _logical(db,session_id):
        row=db.execute('SELECT node_id FROM work_node_bindings WHERE session_id=?',(session_id,)).fetchone()
        if not row and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_attempts'").fetchone():
            version=db.execute('SELECT version FROM dispatch_schema').fetchone()
            if not version or version[0]!=1:raise ValueError('unsupported workflow dispatch schema')
            row=db.execute('SELECT step_id FROM workflow_attempts WHERE session_id=?',(session_id,)).fetchone()
        return row[0] if row else session_id

    @staticmethod
    def _graph(db,session_id):
        session_id=WorkflowGraph._logical(db,session_id)
        row=db.execute('SELECT g.* FROM work_graphs g JOIN work_nodes n ON n.graph_id=g.id WHERE n.session_id=?',(session_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _content(db,graph):
        return {**graph,'nodes':[{**dict(r),'native_session_id':WorkflowGraph._native(db,r['session_id'])} for r in db.execute('SELECT * FROM work_nodes WHERE graph_id=? ORDER BY session_id',(graph['id'],))],
                'edges':[dict(r) for r in db.execute('SELECT * FROM work_edges WHERE graph_id=? ORDER BY target_id,source_id',(graph['id'],))]}

    def _revision(self,db,graph,actor):
        db.execute('UPDATE work_graphs SET version=version+1 WHERE id=?',(graph['id'],))
        graph={**graph,'version':graph['version']+1}
        content=self._content(db,graph)
        db.execute('INSERT INTO work_revisions VALUES(?,?,?,?,?)',(graph['id'],graph['version'],canonical(content),actor,utc_now()))
        self.store.event(db,'graph.revised',graph['id'],{'version':graph['version']},actor,graph['id'])
        return content

    @staticmethod
    def _no_cycles(nodes,edges):
        parents={node:[] for node in nodes}
        for edge in edges:parents[edge['target_id']].append(edge['source_id'])
        visiting=set();done=set()
        def visit(node):
            if node in visiting:raise ValueError('input dependencies cannot contain a cycle')
            if node in done:return
            visiting.add(node)
            for source in parents[node]:visit(source)
            visiting.remove(node);done.add(node)
        for node in nodes:visit(node)

    def attach(self,owner_id,target_id,*,purpose,dependencies,expected_version,actor):
        bounded_text(purpose,'session purpose',4000,True)
        if owner_id==target_id:raise ValueError('a session cannot own itself')
        with self.store.connect(write=True) as db:
            graph=self._graph(db,owner_id)
            if (graph['version'] if graph else 0)!=expected_version:raise ValueError('graph changed; reload before editing')
            if not graph:
                graph={'id':identifier('work'),'root_session_id':owner_id,'version':0}
                db.execute('INSERT INTO work_graphs VALUES(?,?,?)',tuple(graph.values()))
                db.execute('INSERT INTO work_nodes VALUES(?,?,NULL,?)',(owner_id,graph['id'],'Initial session'))
            if self._graph(db,target_id):raise ValueError('session already belongs to connected work; edit its dependencies instead')
            count=db.execute('SELECT COUNT(*) FROM work_nodes WHERE graph_id=?',(graph['id'],)).fetchone()[0]
            if count>=100:raise ValueError('connected work is limited to 100 sessions')
            db.execute('INSERT INTO work_nodes VALUES(?,?,?,?)',(target_id,graph['id'],owner_id,purpose))
            self._edges(db,graph,target_id,dependencies)
            return self._revision(db,graph,actor)

    def dependencies(self,target_id,*,dependencies,expected_version,actor):
        with self.store.connect(write=True) as db:
            target_id=self._logical(db,target_id)
            graph=self._graph(db,target_id)
            if not graph:raise KeyError('connected work not found')
            if graph['version']!=expected_version:raise ValueError('graph changed; reload before editing')
            self._edges(db,graph,target_id,dependencies)
            return self._revision(db,graph,actor)

    def _edges(self,db,graph,target_id,dependencies):
        if not isinstance(dependencies,list) or len(dependencies)>32:raise ValueError('select at most 32 required inputs')
        nodes={r['session_id'] for r in db.execute('SELECT session_id FROM work_nodes WHERE graph_id=?',(graph['id'],))}
        seen=set();edges=[]
        for item in dependencies:
            if not isinstance(item,dict) or set(item)!={'source_id','readiness'}:raise ValueError('invalid input dependency')
            source=self._logical(db,item['source_id']);readiness=item['readiness']
            if source not in nodes:raise ValueError('input source must belong to this connected work')
            if source in seen:raise ValueError('duplicate input source')
            if readiness not in {'after-ready','after-final','alongside'}:raise ValueError('invalid readiness condition')
            seen.add(source)
            # Alongside captures what exists now, including the absence of a result.
            # It never silently switches to a later upstream version.
            old=db.execute('SELECT pinned_result_id FROM work_edges WHERE source_id=? AND target_id=? AND readiness=?',(source,target_id,readiness)).fetchone()
            row=db.execute('SELECT id FROM results WHERE session_id=? ORDER BY version DESC LIMIT 1',(self._native(db,source),)).fetchone() if readiness=='alongside' else None
            pinned=old['pinned_result_id'] if old and readiness=='alongside' else (row['id'] if row else None)
            edges.append({'source_id':source,'target_id':target_id,'readiness':readiness,'pinned_result_id':pinned})
        others=[dict(r) for r in db.execute('SELECT * FROM work_edges WHERE graph_id=? AND target_id!=?',(graph['id'],target_id))]
        self._no_cycles(nodes,others+edges)
        db.execute('DELETE FROM work_edges WHERE target_id=?',(target_id,))
        for edge in edges:db.execute('INSERT INTO work_edges VALUES(?,?,?,?,?)',(graph['id'],edge['source_id'],target_id,edge['readiness'],edge['pinned_result_id']))

    def _readiness(self,db,content):
        nodes={n['session_id']:n for n in content['nodes']};edges=content['edges'];computed={}
        def evaluate(target):
            if target in computed:return computed[target]
            inputs=[];reasons=[]
            for edge in [e for e in edges if e['target_id']==target]:
                source=edge['source_id'];upstream=evaluate(source)
                if edge['readiness']=='alongside':
                    row=db.execute('SELECT * FROM results WHERE id=?',(edge['pinned_result_id'],)).fetchone()
                else:
                    row=db.execute('SELECT * FROM results WHERE session_id=? ORDER BY version DESC LIMIT 1',(self._native(db,source),)).fetchone()
                    if upstream['stale']:reasons.append(f'{source}: upstream inputs changed')
                    if upstream['blocked']:reasons.append(f'{source}: prerequisite inputs are not ready')
                if not row:
                    if edge['readiness']!='alongside':reasons.append(f'{source}: waiting for a result')
                    continue
                result=self.store.result_row(row)
                inputs.append({'source_id':source,'result_id':result['id'],'identity':content_identity(result),'readiness':edge['readiness']})
                if result['outcome']!='pass':reasons.append(f'{source}: {result["outcome"]}')
                if edge['readiness']=='after-final' and result['kind']!='final':reasons.append(f'{source}: waiting for a final result')
            signature=hashlib.sha256(canonical([{'source_id':i['source_id'],'identity':i['identity'],'readiness':i['readiness']} for i in inputs]).encode()).hexdigest()
            previous=db.execute('SELECT d.* FROM work_deliveries d JOIN work_active_deliveries a ON a.delivery_id=d.id WHERE a.target_id=?',(target,)).fetchone()
            # A graph edit can change an empty input set too; compare all edge rules.
            rules=[{key:e[key] for key in ('source_id','readiness','pinned_result_id')} for e in edges if e['target_id']==target]
            signature=hashlib.sha256((signature+canonical(rules)).encode()).hexdigest()
            latest=db.execute('SELECT id FROM results WHERE session_id=? ORDER BY version DESC LIMIT 1',(self._native(db,target),)).fetchone()
            binding=db.execute('SELECT signature FROM graph_result_inputs WHERE result_id=?',(latest['id'],)).fetchone() if latest else None
            result_stale=bool(latest and (rules or previous) and (not previous or not binding or binding['signature']!=signature))
            computed[target]={'session_id':target,'inputs':inputs,'signature':signature,'blocked':bool(reasons),
                              'reasons':reasons,'stale':bool(result_stale or (previous and (previous['signature']!=signature or reasons))),
                              'result_stale':result_stale,
                              'delivered':bool(previous),'last_delivery_id':previous['id'] if previous else None}
            return computed[target]
        for node in nodes:evaluate(node)
        return computed

    def inspect(self,session_id):
        with self.store.connect() as db:
            graph=self._graph(db,session_id)
            if not graph:return {'root_session_id':session_id,'version':0,'nodes':[],'edges':[],'readiness':{}}
            content=self._content(db,graph);content['readiness']=self._readiness(db,content)
            return content

    def deliver(self,target_id,*,expected_version,expected_signature,actor):
        """Atomically queue a complete join; an uncertain HTTP retry cannot duplicate it."""
        with self.store.connect(write=True) as db:
            target_id=self._logical(db,target_id)
            graph=self._graph(db,target_id)
            if not graph:raise KeyError('connected work not found')
            if graph['version']!=expected_version:raise ValueError('graph changed; reload before delivering')
            state=self._readiness(db,self._content(db,graph))[target_id]
            if state['signature']!=expected_signature:raise ValueError('inputs changed; reload before delivering')
            if state['blocked']:raise ValueError('required inputs are not ready')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_attempts'").fetchone():
                active=db.execute("SELECT input_signature FROM workflow_attempts WHERE step_id=? AND state IN ('starting','running')",(target_id,)).fetchone()
                if active and active['input_signature']!=state['signature']:
                    raise ValueError('active attempt must finish before its inputs are replaced')
            previous=db.execute('SELECT * FROM work_deliveries WHERE target_id=? AND signature=?',(target_id,state['signature'])).fetchone()
            if previous:
                self._queue_inputs(db,graph,target_id,previous['id'],json.loads(previous['inputs_json']),actor)
                db.execute('INSERT OR REPLACE INTO work_active_deliveries VALUES(?,?)',(target_id,previous['id']))
                return dict(previous)
            delivery_id=identifier('delivery')
            self._queue_inputs(db,graph,target_id,delivery_id,state['inputs'],actor)
            db.execute('INSERT INTO work_deliveries VALUES(?,?,?,?,?,?,?)',(delivery_id,graph['id'],target_id,canonical(state['inputs']),state['signature'],actor,utc_now()))
            db.execute('INSERT OR REPLACE INTO work_active_deliveries VALUES(?,?)',(target_id,delivery_id))
            self.store.event(db,'graph.inputs_queued',delivery_id,{'target':target_id,'signature':state['signature']},actor,graph['id'])
            return dict(db.execute('SELECT * FROM work_deliveries WHERE id=?',(delivery_id,)).fetchone())

    def _queue_inputs(self,db,graph,target_id,delivery_id,inputs,actor):
        native_target=self._native(db,target_id)
        for item in inputs:
            key=delivery_id+':'+item['result_id']
            if db.execute('SELECT 1 FROM inbox WHERE target_session_id=? AND request_key=?',(native_target,key)).fetchone():continue
            seq=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM inbox WHERE target_session_id=?',(native_target,)).fetchone()[0]
            input_id=identifier('input')
            source=self.store.result_row(db.execute('SELECT * FROM results WHERE id=?',(item['result_id'],)).fetchone())['session_id']
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?,?,?,?)',(input_id,native_target,seq,source,item['result_id'],'Connected input','queued',utc_now(),None,None,key))
            self.store.event(db,'input.queued',input_id,{'source':source,'target':native_target,'result_id':item['result_id'],'sequence':seq},actor,graph['id'])
