"""Managed-session CLI transport; never opens or migrates Console databases."""
import errno
import json
import os
import time
import urllib.error
import urllib.request


NETWORK_PERMISSION_ERROR = ("Console session network access was denied by the sandbox. "
                            "Retry with authorized network access or network escalation; "
                            "no local writer fallback was attempted.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(command, payload):
    capability = os.getenv('AGENT_CONSOLE_EVIDENCE_CAPABILITY')
    identity = os.getenv('AGENT_CONSOLE_SESSION_ID')
    if not capability or not identity:
        raise PermissionError('managed session reporting capability required')
    url = os.environ['AGENT_CONSOLE_REPORTING_URL'].rstrip('/') + '/api/agent-sessions'
    req = urllib.request.Request(url, data=json.dumps({'command':command,'payload':payload}).encode(),
             headers={'Content-Type':'application/json', 'Authorization':'Bearer '+capability,
                      'X-Agent-Console-Session':identity})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=45) as response:
            raw = response.read(4*1024*1024+1)
            if len(raw) > 4*1024*1024:
                raise ValueError('session response too large')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Remote error bodies may contain submitted data or credentials.
        raise ValueError(f'Console session request rejected (HTTP {exc.code}); check session authorization and request constraints') from None
    except PermissionError:
        raise RuntimeError(NETWORK_PERMISSION_ERROR) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, PermissionError) or getattr(exc.reason, "errno", None) in {errno.EPERM, errno.EACCES}:
            raise RuntimeError(NETWORK_PERMISSION_ERROR) from None
        raise RuntimeError('Console session endpoint unavailable; no local writer fallback was attempted') from None


def current_ref(name):
    if name and name == os.getenv('AGENT_CONSOLE_SESSION_NAME'):
        return os.getenv('AGENT_CONSOLE_SESSION_ID') or name
    return name


def managed_read(args, route):
    payload = {'route':route, **{key:getattr(args,key) for key in
        ('name','current','relative','index','session_id','lines') if hasattr(args,key)}}
    if 'name' in payload:
        payload['name'] = current_ref(payload['name'])
    return request('read', payload)


def handles(args):
    return args.command == 'delegate' or (args.command == 'session' and args.session_command in
        {'attention','create','interrupt','restart-agent','kill','wait-for-children'})


def run(args):
    command = 'delegate' if args.command == 'delegate' else args.session_command
    if command in {'delegate','create'}:
        payload = {key:getattr(args,key) for key in ('profile','task','repository','tool','auth_context','agent_mode','model','provider')
                   if getattr(args,key,None) is not None}
        payload.update(name=current_ref(getattr(args,'parent',None)), child_name=args.name,
                       reasoning_effort=args.effort, plan_reasoning_effort=args.plan_effort)
        return request('delegate',payload)
    if command == 'attention':
        if args.current == bool(args.name):
            raise ValueError('provide exactly one of NAME or --current')
        return request(command, {'name':current_ref(args.name),'state':args.state,'note':args.note})
    if command == 'kill' and (not args.yes or args.allow_unmanaged):
        raise PermissionError('managed descendant kill requires --yes and cannot target unmanaged sessions')
    if command != 'wait-for-children':
        return request(command, {'name':current_ref(args.name)})
    timeout = args.timeout if args.timeout is not None else 300
    interval = args.poll_interval if args.poll_interval is not None else 10
    if timeout < 1 or interval < 1:
        raise ValueError('timeout and poll interval must be at least one second')
    deadline = time.monotonic() + timeout
    while True:
        children = request('children',{'name':current_ref(args.name)})['children']
        for child in children:
            attention = child.get('attention_state')
            child['wait_status'] = ('success' if attention == 'ready_for_review' else
                'intervention' if attention in {'blocked','needs_input'} else
                'completed' if child.get('running') is False else 'waiting')
        states = {child['wait_status'] for child in children}
        outcome, code = ('intervention',2) if 'intervention' in states else (
            ('failure',3) if 'completed' in states else ('waiting',1) if 'waiting' in states else ('success',0))
        if outcome != 'waiting' or time.monotonic() >= deadline:
            return {'children':children,'outcome':'timeout' if outcome == 'waiting' else outcome,'exit_code':code}
        time.sleep(min(interval, max(0,deadline-time.monotonic())))
