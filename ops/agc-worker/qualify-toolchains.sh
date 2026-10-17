#!/bin/bash
# Harmless offline checks under the same RootDirectory and unprivileged job user.
set -euo pipefail
umask 0077
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /etc/agc-worker/hold
python3 - <<'PY'
import hashlib,importlib.util,json,os,pathlib,pwd,shutil,socket,subprocess,uuid
os.umask(0o077)
spec=importlib.util.spec_from_file_location('worker','/usr/local/lib/agc-worker/worker.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
with socket.socket(socket.AF_UNIX) as client:
 client.settimeout(10);client.connect('/run/agc-worker/rpc.sock');client.sendall(b'{"protocol_version":1,"action":"status"}\n')
 with client.makefile('rb') as stream:status=json.loads(stream.readline(524288))
 assert status.get('ok') and status.get('active') is None,'Production worker must be drained'
base=pathlib.Path('/var/lib/agc-toolchain-qualification-'+uuid.uuid4().hex);base.mkdir(mode=0o700)
user=pwd.getpwnam('agcjob')
job=base/'job';job.mkdir(mode=0o700);os.chown(job,0,user.pw_gid);job.chmod(0o710)
for name in ('workspace','output'):
 p=job/name;p.mkdir(mode=0o700);os.chown(p,user.pw_uid,user.pw_gid)
for name in ('.home','.tmp','.cache'):
 p=job/'workspace'/name;p.mkdir(mode=0o700);os.chown(p,user.pw_uid,user.pw_gid)
probe=job/'probe.py'
probe.write_text('''import json,os,platform,subprocess,sys
assert os.getuid()!=0 and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY')
assert os.getcwd()=='/run/agc-job/workspace' and sys.argv[0]=='/run/agc-job/probe.py'
assert os.environ['HOME']=='/run/agc-job/workspace/.home'
assert os.stat('/run/agc-job').st_mode&0o777==0o710
assert os.stat('/run/agc-job').st_uid==0 and os.stat('/run/agc-job').st_gid==os.getgid()
assert os.stat('/run/agc-job/probe.py').st_mode&0o777==0o640
assert any(' /run/agc-job ' in line for line in open('/proc/self/mountinfo'))
expected=sys.argv[1]
node=subprocess.check_output(['node','--version'],text=True).strip()
assert node=={'debian12':'v22.23.1','debian13':'v22.22.2'}[expected]
assert platform.libc_ver()==('glibc',{'debian12':'2.36','debian13':'2.41'}[expected])
if expected=='debian12':
 assert subprocess.check_output(['pnpm','--version'],text=True).strip()=='11.16.0'
else:
 import bs4,soupsieve
 assert bs4.BeautifulSoup('<div class="card-product">JPY 100</div>','html.parser').select('div.card-product')[0].get_text()=='JPY 100'
 print(json.dumps({'bs4':bs4.__version__,'soupsieve':soupsieve.__version__,'css_selector':True}))
 subprocess.run(['node','-e',"const {chromium}=require('/usr/local/lib/agc-worker-adapters/agc/scripts/compute/node_modules/playwright');(async()=>{const browser=await chromium.launch({headless:true});try{const page=await browser.newPage();await page.goto('data:text/html,<title>worker-headless-check</title>');if(await page.title()!=='worker-headless-check')throw Error('title');console.log(JSON.stringify({headless:true,browser:browser.version()}));}finally{await browser.close();}})().catch(()=>process.exit(1));"],check=True,timeout=40)
print(json.dumps({'toolchain':expected,'node':node,'glibc':platform.libc_ver()[1],'uid':os.getuid(),'path':os.environ['PATH'],'passed':True,
 'namespace':{'cwd':os.getcwd(),'uid':os.getuid(),'gid':os.getgid(),'job_root_mode':'0710','metadata_mode':'0640','private_mount':True}}))
''');os.chown(probe,0,user.pw_gid);probe.chmod(0o640)
units=[];results=[]
try:
 for toolchain in ('debian12','debian13'):
  unit='agc-toolchain-qualification-'+uuid.uuid4().hex+'.service';units.append(unit)
  props={'Slice':'agc-worker-jobs.slice','MemoryMax':'1G','MemorySwapMax':'0','CPUQuota':'100%','CPUWeight':'10','IOWeight':'10','Nice':'10','TasksMax':'128','RuntimeMaxSec':'60','TimeoutStopSec':'5','KillMode':'control-group','OOMPolicy':'kill','NoNewPrivileges':'yes','PrivateTmp':'yes','PrivateDevices':'yes','ProtectSystem':'strict','ProtectHome':'yes','UnsetEnvironment':'DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK'}
  props.update(w.toolchain_properties({'root_directory':'/var/lib/agc-worker/toolchains/'+toolchain},job))
  props['UMask']='0077'
  command=['systemd-run','--wait','--pipe','--collect','--unit='+unit,'--uid=agcjob','--gid='+str(user.pw_gid)]+['--property='+k+'='+v for k,v in props.items()]+['--','/usr/bin/python3','/run/agc-job/probe.py',toolchain]
  result=subprocess.run(command,text=True,capture_output=True,timeout=85)
  print(result.stdout,flush=True)
  if result.returncode:raise RuntimeError('Toolchain qualification failed: '+toolchain+' '+result.stderr[-3000:])
  proof=json.loads(result.stdout.strip().splitlines()[-1])
  assert proof['passed'] is True and proof['toolchain']==toolchain
  results.append({'toolchain':toolchain,'passed':True,'namespace':proof['namespace'],'stdout':result.stdout})
 output=pathlib.Path('/var/lib/agc-worker-qualification-results');output.mkdir(mode=0o700,exist_ok=True)
 w.atomic_json(output/(base.name+'.json'),{'version':1,'passed':True,'results':results,'source':'qualify-toolchains.sh',
  'worker_source_sha256':hashlib.sha256(pathlib.Path('/usr/local/lib/agc-worker/worker.py').read_bytes()).hexdigest(),'production_umask':'0077'})
 print(json.dumps({'toolchain_qualification':'passed','evidence':str(output/(base.name+'.json'))}))
finally:
 for unit in units:subprocess.run(['systemctl','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
 if len(results)==2:shutil.rmtree(base)
PY
