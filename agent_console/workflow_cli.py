"""Agent-facing result and inbox commands; mutations bind to the native session."""
from pathlib import Path
import json
import os
import urllib.request
import urllib.error
from .workflow_service import WorkflowService


def add_commands(commands):
    root=commands.add_parser('workflow')
    sub=root.add_subparsers(dest='workflow_command',required=True)
    from .operator_workflow_cli import add_commands as add_owner_workflow_commands
    add_owner_workflow_commands(sub)
    proposal=sub.add_parser('propose');proposal.add_argument('--current',action='store_true',required=True)
    for key in ['task','reason','expected-output','tool','profile','request-key']:proposal.add_argument('--'+key,required=True)
    proposal.add_argument('--repository');proposal.add_argument('--action',choices=['read','write','test'])
    proposal.add_argument('--input',action='append',default=[]);proposal.add_argument('--readiness',choices=['after-final','after-ready','alongside'],default='after-final')
    publish=sub.add_parser('publish')
    publish.add_argument('--current',action='store_true',required=True)
    publish.add_argument('--kind',choices=['ready','final'],required=True)
    publish.add_argument('--outcome',choices=['pass','fail','blocked'],required=True)
    summary=publish.add_mutually_exclusive_group(required=True)
    summary.add_argument('--summary');summary.add_argument('--summary-file')
    publish.add_argument('--check',action='append',default=[])
    publish.add_argument('--file',action='append',default=[])
    publish.add_argument('--commit',action='append',default=[])
    publish.add_argument('--request-key',required=True)
    results=sub.add_parser('results');results.add_argument('session');results.add_argument('--before',type=int,default=2147483647)
    connections=sub.add_parser('connections');connections.add_argument('--current',action='store_true',required=True)
    result=sub.add_parser('result');result.add_argument('result_id')
    send=sub.add_parser('send');send.add_argument('result_id');send.add_argument('--to',required=True)
    send.add_argument('--note',default='');send.add_argument('--request-key',required=True)
    inbox=sub.add_parser('inbox');inbox.add_argument('--current',action='store_true',required=True);inbox.add_argument('--after',type=int,default=0)
    ack=sub.add_parser('ack');ack.add_argument('item_id');ack.add_argument('--state',choices=['delivered','consumed'],required=True)
    ack.add_argument('--current',action='store_true',required=True)


def run(args,manager=None):
    if args.workflow_command=='manage':
        from .operator_workflow_cli import run as run_owner
        return run_owner(args, manager_factory=(lambda: manager) if manager is not None else None)
    if manager is None and os.getenv('AGENT_CONSOLE_REPORTING_URL'):
        return remote_run(args)
    if manager is None:
        from .manager import SessionManager
        manager=SessionManager()
    svc=WorkflowService(manager);command=args.workflow_command
    if command=='results':return {'results':svc.store.results(svc.session(args.session)['id'],before=args.before)}
    if command=='result':return svc.store.result(args.result_id)
    session=svc.current();actor='session:'+session['id']
    if command=='propose':
        from .workflow_engine import WorkflowEngine
        return WorkflowEngine(manager).propose(session['id'],**proposal_payload(args,session['id']),actor=actor)
    if command=='connections':return svc.graph().inspect(session['id'])
    if command=='inbox':return svc.store.inbox(session['id'],after=args.after)
    if command=='ack':return svc.store.acknowledge(args.item_id,session['id'],state=args.state,actor=actor)
    if command=='send':return svc.send(args.result_id,args.to,request_key=args.request_key,note=args.note,actor=actor,source=session['id'])
    summary=args.summary
    if args.summary_file:
        with Path(args.summary_file).open(encoding='utf-8') as stream:summary=stream.read(32001)
    return svc.publish(session['id'],{'kind':args.kind,'outcome':args.outcome,'summary':summary,
         'checks':args.check,'artifacts':[{'kind':'file','path':p} for p in args.file]+[{'kind':'commit','sha':s} for s in args.commit],
         'request_key':args.request_key},actor)


def remote_run(args):
    command=args.workflow_command
    if command=='propose':payload=proposal_payload(args,os.getenv('AGENT_CONSOLE_SESSION_ID'))
    elif command=='publish':
        summary=args.summary
        if args.summary_file:
            with Path(args.summary_file).open(encoding='utf-8') as stream:summary=stream.read(32001)
        payload={'kind':args.kind,'outcome':args.outcome,'summary':summary,'checks':args.check,
                 'artifacts':[{'kind':'file','path':p} for p in args.file]+[{'kind':'commit','sha':s} for s in args.commit], 'request_key':args.request_key}
    elif command=='send':payload={'result_id':args.result_id,'target_session_id':args.to,'note':args.note,'request_key':args.request_key}
    elif command=='ack':payload={'item_id':args.item_id,'state':args.state}
    elif command=='result':payload={'result_id':args.result_id}
    elif command=='inbox':payload={'after':args.after}
    elif command=='results':payload={'before':args.before}
    else:payload={}
    if command=='results' and args.session not in {os.getenv('AGENT_CONSOLE_SESSION_ID'),os.getenv('AGENT_CONSOLE_SESSION_NAME')}:
        raise PermissionError('agent reporting can list its own results; use received result IDs for peer inputs')
    capability=os.getenv('AGENT_CONSOLE_EVIDENCE_CAPABILITY')
    session_id=os.getenv('AGENT_CONSOLE_SESSION_ID')
    if not capability or not session_id:raise PermissionError('managed session reporting capability required')
    request=urllib.request.Request(os.environ['AGENT_CONSOLE_REPORTING_URL'].rstrip('/')+'/api/agent-workflow',
            data=json.dumps({'command':command,'payload':payload}).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+capability,'X-Agent-Console-Session':session_id})
    try:
        with urllib.request.urlopen(request,timeout=45) as response:
            raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('reporting response too large')
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        try:detail=json.loads(error.read(4096)).get('detail','reporting request rejected')
        except (ValueError,UnicodeError):detail='reporting request rejected'
        raise ValueError(str(detail)) from None
    except urllib.error.URLError:
        raise RuntimeError('Console reporting endpoint unavailable; retry with the same request key after connectivity is restored') from None


def proposal_payload(args,session_id):
    config={'tool':args.tool,'profile':args.profile}
    if args.repository:config['repository']=args.repository
    if args.action:config['action']=args.action
    dependencies=[]
    for value in args.input:
        source,separator,readiness=value.rpartition(':')
        if not separator or readiness not in {'after-final','after-ready','alongside'}:raise ValueError('input must be DURABLE_ID:after-final, :after-ready or :alongside')
        dependencies.append({'source_id':source,'readiness':readiness})
    return {'task':args.task,'reason':args.reason,'expected_output':args.expected_output,'config':config,
            'dependencies':dependencies or [{'source_id':session_id,'readiness':args.readiness}], 'request_key':args.request_key}
