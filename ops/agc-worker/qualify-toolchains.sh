#!/bin/bash
# Harmless offline checks under the same RootDirectory and unprivileged job user.
set -euo pipefail
test "${WSL_DISTRO_NAME:-}" = AGC-Worker
test "$(id -u)" = 0
test -f /etc/agc-worker/hold
python3 - <<'PY'
import importlib.util,json,os,pathlib,pwd,shutil,socket,subprocess,uuid
spec=importlib.util.spec_from_file_location('worker','/usr/local/lib/agc-worker/worker.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
with socket.socket(socket.AF_UNIX) as client:
 client.settimeout(10);client.connect('/run/agc-worker/rpc.sock');client.sendall(b'{"protocol_version":1,"action":"status"}\n')
 with client.makefile('rb') as stream:status=json.loads(stream.readline(524288))
 assert status.get('ok') and status.get('active') is None,'Production worker must be drained'
base=pathlib.Path('/var/lib/agc-toolchain-qualification-'+uuid.uuid4().hex);base.mkdir(mode=0o755)
user=pwd.getpwnam('agcjob')
for name in ('workspace','output'):
 p=base/name;p.mkdir(mode=0o700);os.chown(p,user.pw_uid,user.pw_gid)
probe=base/'probe.py'
probe.write_text('''import json,os,platform,subprocess,sys
assert os.getuid()!=0 and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY')
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
print(json.dumps({'toolchain':expected,'node':node,'glibc':platform.libc_ver()[1],'uid':os.getuid(),'path':os.environ['PATH'],'passed':True}))
''');probe.chmod(0o644)
units=[];results=[]
try:
 for toolchain in ('debian12','debian13'):
  unit='agc-toolchain-qualification-'+uuid.uuid4().hex+'.service';units.append(unit)
  props={'Slice':'agc-worker-jobs.slice','MemoryMax':'1G','MemorySwapMax':'0','CPUQuota':'100%','CPUWeight':'10','IOWeight':'10','Nice':'10','TasksMax':'128','RuntimeMaxSec':'60','TimeoutStopSec':'5','KillMode':'control-group','OOMPolicy':'kill','NoNewPrivileges':'yes','PrivateTmp':'yes','PrivateDevices':'yes','ProtectSystem':'strict','ProtectHome':'yes','ReadWritePaths':str(base/'workspace')+' '+str(base/'output'),'WorkingDirectory':str(base/'workspace'),'Environment':'HOME='+str(base/'workspace')+' TMPDIR='+str(base/'workspace')+' PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/agc-worker-adapters/browsers','UnsetEnvironment':'DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK'}
  props.update(w.toolchain_properties({'root_directory':'/var/lib/agc-worker/toolchains/'+toolchain},base))
  command=['systemd-run','--wait','--pipe','--collect','--unit='+unit,'--uid=agcjob']+['--property='+k+'='+v for k,v in props.items()]+['--','/usr/bin/python3',str(probe),toolchain]
  result=subprocess.run(command,text=True,capture_output=True,timeout=85)
  print(result.stdout,flush=True)
  if result.returncode:raise RuntimeError('Toolchain qualification failed: '+toolchain+' '+result.stderr[-3000:])
  results.append({'toolchain':toolchain,'passed':True,'stdout':result.stdout})
 output=pathlib.Path('/var/lib/agc-worker-qualification-results');output.mkdir(mode=0o700,exist_ok=True)
 w.atomic_json(output/(base.name+'.json'),{'passed':True,'results':results,'source':'qualify-toolchains.sh'})
 print(json.dumps({'toolchain_qualification':'passed','evidence':str(output/(base.name+'.json'))}))
finally:
 for unit in units:subprocess.run(['systemctl','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
 if len(results)==2:shutil.rmtree(base)
PY
