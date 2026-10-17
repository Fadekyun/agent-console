#!/usr/bin/env python3
"""Install fixed, isolated queue identities. Private keys never leave their host."""
import argparse, base64, grp, hashlib, json, os
from pathlib import Path
import pwd, re, secrets, socket, subprocess, tempfile

HOST_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF3QaErt25xxjNRD8gG5esZBWP6QAGasmspNG2YDnOrq'
# Source CIDRs are independently enforced by authorized_keys, not HTTP headers.
SPECS={
 'yuyutei':('192.168.1.71','yuyutei:', ['yuyutei.collect','yuyutei.parse'], ['yuyutei-archive','yuyutei-prices'],50,False,[]),
 'site':('192.168.1.71','agc:', ['agc.build','agc.test'], ['agc-release'],40,False,[]),
 'points':('192.168.1.71','points:', ['points.weekly','points.player_events'], ['points:weekly','points:player_events'],20,False,[]),
 'bushi':('192.168.1.67','bushi:', ['bushi.run'], ['bushi-snapshots','bushi-applications'],90,True,['agc:bushi-series-apply','agc:bushi-series-modify']),
 'tournament':('192.168.1.71','tournament:', ['bushi.run','bushi.emergency'], ['agc-tournament-state'],100,True,['agc:paid-participation']),
 'bushi-mcp':('192.168.1.67','bushi-mcp:', ['bushi.run'], ['bushi-applications'],90,True,['agc:bushi-series-apply','agc:bushi-series-modify']),
 'tv':('127.0.0.1','tv:', ['tv.build'], ['tv-release'],40,False,[]),
}

def run(*args):return subprocess.check_output(args,stderr=subprocess.DEVNULL,text=True).strip()
def atomic(path,data,mode=0o640,gid=0):
 path=Path(path);fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.provision-')
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,mode);os.chown(tmp,0,gid);os.replace(tmp,path)
  fd=os.open(path.parent,os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def directory(path,mode=0o755,gid=0):
 path=Path(path)
 if path.exists() and (path.is_symlink() or not path.is_dir() or path.stat().st_uid!=0 or path.stat().st_mode&0o022):raise ValueError('unsafe existing directory')
 path.mkdir(parents=True,exist_ok=True);os.chown(path,0,gid);os.chmod(path,mode)
def protected(path):
 path=Path(path)
 for p in (path,*path.parents):
  s=p.lstat()
  if p.is_symlink() or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('unprotected path')
 return path

def client(name,helper):
 source=SPECS[name][0]
 expected='agent-console-staging' if name=='tv' else 'bushi-automation' if name in ('bushi','bushi-mcp') else 'codex-remote'
 if socket.gethostname()!=expected:raise ValueError('wrong client host')
 group=0 if name=='tv' else grp.getgrnam('bushi' if name in ('bushi','bushi-mcp') else 'fadekyun').gr_gid
 directory('/etc/agc-compute');directory('/etc/agc-compute/producers')
 root=Path('/etc/agc-compute/producers')/name;directory(root,0o750,group)
 key=root/'id_ed25519'
 if not key.exists():run('/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','agc-compute-'+name,'-f',str(key))
 protected(key);os.chown(key,0,group);os.chmod(key,0o640 if name in ('bushi','bushi-mcp','tournament') else 0o600)
 pin=root/'known_hosts';atomic(pin,('192.168.1.115,127.0.0.1 '+HOST_KEY+'\n').encode(),0o640,group)
 host='127.0.0.1' if name=='tv' else '192.168.1.115'
 config={'transport':'ssh-stdio','ssh_host':'agc-cp-'+name+'@'+host,'ssh_identity_file':str(key),'ssh_known_hosts_file':str(pin)}
 atomic(root/'client.json',json.dumps(config).encode(),0o600 if name in ('tv','points') else 0o640,group)
 directory('/opt/agent-console-producer')
 destination=Path('/opt/agent-console-producer/compute_producer_bridge.py')
 if destination.exists() and destination.read_bytes()!=helper:raise ValueError('existing helper differs; reviewed update required')
 atomic(destination,helper,0o644)
 return {'name':name,'public_key':key.with_suffix('.pub').read_text().strip(),'config':str(root/'client.json'),'helper_sha256':hashlib.sha256(helper).hexdigest()}

def server(name,public_key,helper):
 if socket.gethostname()!='agent-console-staging':raise ValueError('wrong server')
 if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?',public_key):raise ValueError('invalid public key')
 user='agc-cp-'+name
 try:account=pwd.getpwnam(user)
 except KeyError:
  run('/usr/sbin/useradd','--system','--user-group','--home-dir','/nonexistent','--shell','/bin/sh','--comment','AGC fixed queue producer '+name,user)
  run('/usr/sbin/usermod','-p','*',user);account=pwd.getpwnam(user)
 if account.pw_dir!='/nonexistent' or account.pw_shell!='/bin/sh' or account.pw_uid==0:raise ValueError('unmanaged account')
 if any(g.gr_name in ('sudo','wheel') and user in g.gr_mem for g in grp.getgrall()):raise ValueError('privileged producer')
 directory('/opt/agent-console-producer');dest=Path('/opt/agent-console-producer/compute_producer_bridge.py')
 if dest.exists() and dest.read_bytes()!=helper:raise ValueError('existing helper differs')
 atomic(dest,helper,0o644)
 directory('/etc/agent-console-producers');directory('/etc/agent-console-producers/keys')
 cfg=Path('/etc/agent-console-producers')/(name+'.json')
 if cfg.exists():
  prior=json.loads(protected(cfg).read_text())
  if prior.get('producer')!=name or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}',prior.get('token','')):raise ValueError('existing identity differs')
  token=prior['token']
 else:token=secrets.token_urlsafe(48)
 atomic(cfg,json.dumps({'producer':name,'token':token}).encode(),0o640,account.pw_gid)
 command='/usr/bin/python3 -I /opt/agent-console-producer/compute_producer_bridge.py bridge --config '+str(cfg)
 source,prefix,kinds,targets,priority,writes,namespaces=SPECS[name]
 atomic('/etc/agent-console-producers/keys/'+user,(f'restrict,from="{source}",command="{command}" '+public_key+'\n').encode(),0o644)
 match=f'''# Dedicated queue identity; no change to other SSH users.
Match User {user}
    AuthorizedKeysFile /etc/agent-console-producers/keys/{user}
    ForceCommand {command}
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    DisableForwarding yes
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
Match all
'''
 sshfile=Path('/etc/ssh/sshd_config.d')/('60-agc-compute-'+name+'.conf')
 old=sshfile.read_bytes() if sshfile.exists() else None
 atomic(sshfile,match.encode(),0o644)
 try:run('/usr/sbin/sshd','-t')
 except BaseException:
  if old is None:sshfile.unlink()
  else:atomic(sshfile,old,0o644)
  raise
 policies=Path('/etc/agent-console/compute/producers.json')
 directory(policies.parent,0o750,grp.getgrnam('agentstage').gr_gid)
 value=json.loads(protected(policies).read_text()) if policies.exists() else {'producers':{}}
 policy={'enabled':True,'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'source_cidrs':['127.0.0.1/32'],
  'kinds':kinds,'request_prefix':prefix,'max_priority':priority,'publish_targets':targets,
  'allow_production_writes':writes,'allow_emergency':name=='tournament','business_key_namespaces':namespaces}
 if name in value['producers'] and value['producers'][name]!=policy:raise ValueError('existing producer policy differs')
 value['producers'][name]=policy;atomic(policies,json.dumps(value).encode(),0o640,grp.getgrnam('agentstage').gr_gid)
 # CT115 uses ssh.socket. SIGHUP drops inherited listening descriptors;
 # restart the listener (KillMode=process preserves established sessions).
 run('/usr/bin/systemctl','restart','ssh.service')
 if run('/usr/bin/systemctl','is-active','ssh.service')!='active':raise ValueError('SSH listener recovery failed')
 return {'name':name,'account':user,'source_cidr':source,'scope':kinds,'config':str(policies),'state':'installed_compute_dispatch_unchanged'}

def main():
 if os.geteuid()!=0:raise SystemExit('root required')
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['client','server']);p.add_argument('name',choices=SPECS)
 a=p.parse_args();value=json.load(__import__('sys').stdin)
 helper=base64.b64decode(value['helper_base64'],validate=True)
 if hashlib.sha256(helper).hexdigest()!=value['helper_sha256']:raise ValueError('helper digest mismatch')
 result=client(a.name,helper) if a.mode=='client' else server(a.name,value['public_key'],helper)
 print(json.dumps(result))
if __name__=='__main__':main()
