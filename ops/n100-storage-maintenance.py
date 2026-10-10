#!/usr/bin/env python3
"""Bound download caches and return guest free blocks; preserve project data."""
import argparse
import fcntl
import json
import shutil
from pathlib import Path
import subprocess
import sys

GUEST_CODE = r'''
import json,os,pathlib,pwd,re,subprocess,sys
limit=512*1048576
active=False
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 if int(p.name)==os.getpid():continue
 try:
  comm=(p/'comm').read_text().lower()
  command=(p/'cmdline').read_bytes().decode('utf8','replace').replace(chr(0),' ')
  if re.search(r'\b(?:npm|pnpm|yarn|pip3?|uv)\s+(?:install|ci|update|add|sync|rebuild|exec|dlx)\b',comm) or re.search(r'(?:npm|npm-cli\.js|pnpm|yarn|pip3?|uv)\s+(?:install|ci|update|add|sync|rebuild|exec|dlx)\b',command) or comm.strip()=='npx':active=True
 except OSError:pass
users=['fadekyun','ops'] if pathlib.Path('/home/fadekyun').exists() else ['agentstage']
rows=[]
for user in users:
 try:owner_home=pathlib.Path(pwd.getpwnam(user).pw_dir)
 except KeyError:continue
 if str(owner_home) not in ['/home/fadekyun','/home/ops','/home/agentstage']:continue
 for kind,cache in [('npm',owner_home/'.npm/_cacache'),('uv',owner_home/'.cache/uv'),('pip',owner_home/'.cache/pip')]:
  if not cache.exists():continue
  if cache.is_symlink() or cache.resolve()!=cache:raise RuntimeError('Refusing symlinked cache root')
  measured=subprocess.run(['du','-sx','-B1',str(cache)],capture_output=True,text=True,timeout=120)
  if measured.returncode:raise RuntimeError('Cannot measure cache')
  size=int(measured.stdout.split()[0]);action='within_budget'
  if size>limit:
   if active:action='deferred_installer_active'
   else:
    prefix=['runuser','-u',user,'--','env','HOME='+str(owner_home),'PATH=/home/'+user+'/.local/bin:/usr/local/bin:/usr/bin:/bin']
    if kind=='npm':args=prefix+['/bin/npm','cache','clean','--force','--cache',str(owner_home/'.npm')]
    elif kind=='uv' and (owner_home/'.local/bin/uv').exists():args=prefix+[str(owner_home/'.local/bin/uv'),'--no-config','cache','clean','--cache-dir',str(cache)]
    elif kind=='pip':args=prefix+['python3','-m','pip','--cache-dir',str(cache),'cache','purge']
    else:rows.append({'user':user,'kind':kind,'bytes':size,'action':'owner_cli_unavailable'});continue
    clean=subprocess.run(args,capture_output=True,text=True,cwd=owner_home,timeout=300)
    if clean.returncode:raise RuntimeError('Owner cache cleanup failed for '+user+'/'+kind)
    action='cleared_download_cache'
  rows.append({'user':user,'kind':kind,'bytes_before':size,'action':action})
print(json.dumps(rows))
'''

def run(args, timeout=600):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Maintenance step failed, exit '+str(result.returncode))
    return result.stdout.strip()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    lock = open('/run/lock/n100-storage-maintenance.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Maintenance already running');return
    warnings=[]
    for label, path, minimum in [('host_root','/',10),('host_tmp','/var/lib/agent-console-tmp',10)]:
        disk=shutil.disk_usage(path)
        low=disk.free<minimum*1073741824
        print(json.dumps({'host_storage':label,'free_bytes':disk.free,'minimum_free_GiB':minimum,'headroom_ok':not low}),flush=True)
        if low:warnings.append(label+' disk headroom low')
    for ct, minimum in [(106,20),(114,5),(115,4)]:
        state=run(['pct','status',str(ct)])
        if state != 'status: running':
            print(json.dumps({'ct':ct,'action':'skipped_not_running'}));continue
        if not args.check:
            caches=run(['pct','exec',str(ct),'--','nice','-n','19','ionice','-c','3',
                        'python3','-c',GUEST_CODE])
            print(json.dumps({'ct':ct,'cache_results':json.loads(caches)}),flush=True)
            trim=run(['pct','fstrim',str(ct)])
            print(json.dumps({'ct':ct,'trim_result':trim}),flush=True)
        disk=json.loads(run(['pct','exec',str(ct),'--','python3','-c',
            'import shutil,json;d=shutil.disk_usage("/");print(json.dumps({"total":d.total,"used":d.used,"free":d.free}))']))
        low=disk['free']<minimum*1073741824
        print(json.dumps({'ct':ct,'disk':disk,'minimum_free_GiB':minimum,'headroom_ok':not low}),flush=True)
        if low:warnings.append('CT'+str(ct)+' disk headroom low')
    pool=json.loads(run(['lvs','--reportformat','json','--units','b','--nosuffix','-o',
                         'lv_size,data_percent','pve/data']))['report'][0]['lv'][0]
    percent=float(pool['data_percent']);free=float(pool['lv_size'])*(1-percent/100)
    print(json.dumps({'pool_used_percent':percent,'pool_free_bytes_estimate':round(free)}),flush=True)
    if percent>=85:warnings.append('Shared thin pool usage at least 85 percent')
    if warnings:
        print(json.dumps({'warnings':warnings}),flush=True);raise SystemExit(2)

if __name__ == '__main__':
    main()
