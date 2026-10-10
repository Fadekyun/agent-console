#!/usr/bin/env node
// Capture shipped UI with synthetic API/WebSocket fixtures; never contact a live Console.
import { chromium } from '@playwright/test';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = join(root, 'docs/media');
const temp = await mkdtemp(join(tmpdir(), 'console-readme-'));
const python = process.env.MEDIA_PYTHON || 'python3';
const port = Number(process.env.AGCONSOLE_MEDIA_PORT || 4186);
const base = `http://127.0.0.1:${port}`;
const errors = [], failures = [], inputs = [];
const timestamp = '2026-10-10T06:00:00Z';
const task = 'Improve the search empty state and check the keyboard navigation.';
const profiles = [
  ['coder', 'Coder', 'write', 'Implement a task in an isolated worktree and check the result.'],
  ['reviewer', 'Reviewer', 'read_only', 'Review changes and report concrete findings.'],
  ['orchestrator', 'Orchestrator', 'write', 'Coordinate related sessions and check their results.'],
].map(([name, display_name, read_write_capability, description]) => ({name, display_name, read_write_capability, description, status:'active'}));
function session(id, name, profile, parent = null, attention = 'normal') {
  return {id, tmux_name:name, tool:'codex-pro', profile, repository:'/workspace/demo-app', project_id:'demo-project', project_name:'Demo app', initial_task:task, parent_session_id:parent, running:true, managed:true, status:'attached', attention_state:attention, actions:['attach','interrupt','kill'], created_at:timestamp, last_activity:timestamp};
}
const sessions = [
  session('demo-root', 'search-improvements', 'orchestrator'),
  session('demo-coder', 'search-coder', 'coder', 'demo-root'),
  {...session('demo-reviewer', 'keyboard-review', 'reviewer', 'demo-root', 'ready_for_review'), initial_task:'Review keyboard navigation and report any problems.'},
  {...session('demo-docs', 'getting-started-guide', 'coder'), initial_task:'Write a short getting started guide with clear examples.'},
];
function workbench() {
  const nodes = sessions.map(s => ({id:s.id, owner_id:s.parent_session_id, root_id:s.parent_session_id || s.id, native_id:s.id, native_name:s.tmux_name, title:s.tmux_name, task:s.initial_task, repository:s.repository, profile:s.profile, tool:s.tool, project_id:s.project_id, project_name:s.project_name, hidden:false, reviewable:s.attention_state==='ready_for_review', mechanical:'running', attention:s.attention_state, result_state:'unknown', waiting:false, needs_attention:s.attention_state!=='normal', created_at:s.created_at, last_activity:s.last_activity, attempts:[], readiness:{}}));
  const groups = nodes.filter(n=>!n.owner_id).map(n=>{const members=nodes.filter(m=>m.root_id===n.id);return {root_id:n.id, member_ids:members.map(m=>m.id), priority:members.some(m=>m.needs_attention)?0:1,last_activity:timestamp,children_total:members.length-1,children_complete:0};});
  return {sessions,nodes,groups,aliases:{},readiness:{ready:true,warnings:[]}};
}
const entries = [{name:'APP_MODE',state:'enabled',overrides_host:false,overrides_global:false}];
function environment() {
  return {broker_enabled:true,protected_names:['N8N_MCP_TOKEN','DIRECTUS_MCP_TOKEN','BUSHI_MCP_TOKEN','OPENROUTER_API_KEY','CMD_API_KEY'],broker_revision:1,entries,effective:entries.map(e=>({name:e.name,source:'project',protected:!!e.protected})),host_names:[],sessions:sessions.slice(0,2).map(s=>({name:s.tmux_name,status:'running',refresh_required:false,revision:1}))};
}
const server = spawn('python3', ['tests/ui_server.py'], {cwd:root, env:{...process.env,AGCONSOLE_UI_TEST_PORT:String(port)},stdio:['ignore','ignore','pipe']});
let serverError=''; server.stderr.on('data',data=>{serverError+=data;});
let browser;
try {
  await mkdir(output,{recursive:true});
  for (let i=0;i<50;i++) {
    if(server.exitCode!==null)throw new Error(`Fixture server failed: ${serverError}`);
    try {if((await fetch(base+'/work')).ok)break;}catch{}
    await new Promise(done=>setTimeout(done,100));
    if(i===49)throw new Error('Fixture server did not start');
  }
  browser = await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const context = await browser.newContext({viewport:{width:1280,height:1100}, deviceScaleFactor:1, colorScheme:'light', locale:'en-GB', timezoneId:'UTC', reducedMotion:'reduce'});
  await context.addInitScript(()=>localStorage.setItem('agent-console-theme','light'));
  await context.route('**/*', async route => {
    const request=route.request(), url=new URL(request.url()), path=url.pathname;
    if(url.origin!==base){failures.push(`External request blocked: ${url.origin}`);return route.abort();}
    if(!path.startsWith('/api/'))return route.continue();
    let body;
    if(path==='/api/me') body={profiles, tool_status:[{name:'codex-pro',status:'ready'},{name:'pi',status:'ready'},{name:'shell',status:'ready'}],auth_contexts:[{tool:'codex-pro',name:'default',provider:'openai',status:'ready'},{tool:'pi',name:'default',provider:'commandcode',status:'ready'}],session_limits:{managed:12,children:0}};
    else if(path==='/api/interface') body={label:'Demo data',workspace:'/workspace/demo-app'};
    else if(path==='/api/workbench')body=workbench();
    else if(path==='/api/projects')body=[{id:'demo-project',name:'Demo app'}];
    else if(path==='/api/environment')body=environment();
    else if(path.startsWith('/api/environment/')&&request.method()==='PUT'){
      const value=request.postDataJSON();
      assert.equal(value.value,'demo-key-not-a-real-credential');
      entries.push({name:decodeURIComponent(path.split('/').at(-1)),state:'enabled',protected:true,immediate:true,overrides_host:false,overrides_global:false});
      body={ok:true,protected:true,immediate:true};
    }
    else if(path==='/api/sessions'){
      if(request.method()==='POST'){
        const value=request.postDataJSON();
        body={...session('demo-new','search-empty-state',value.profile),...value,initial_task:value.task};sessions.push(body);
      }else body=sessions;
    }
    else if(path.endsWith('/brief')){
      const name=decodeURIComponent(path.split('/')[3]);body={brief:sessions.find(s=>s.tmux_name===name)?.initial_task||task};
    }
    else if(path.endsWith('/workflow'))body={root_id:'demo-root',steps:[],policy:{state:'running',version:0,policy:{mode:'suggestions',repositories:[],actions:[],roles:[],harnesses:[],targets:[],max_concurrent:2,max_total:4,max_depth:2,max_reruns:3}},graph:{}};
    else if(path.endsWith('/review'))body={content:'Demo session ready. Review and send the task in the terminal.',alternate_screen:false};
    else if(path.endsWith('/results'))body={results:[]};
    else if(path.endsWith('/inbox'))body={items:[],notice:'Demo data'};
    else if(path==='/api/skills/effective')body={effective:[],issues:[]};
    else if(path==='/api/skill-registry/preview')body={validation:{valid:true,issues:[]},policies:[],notice:'Selected skills only.'};
    else {failures.push(`Unhandled API route: ${request.method()} ${path}`);return route.fulfill({status:404,json:{detail:'Unmocked demo request'}});}
    return route.fulfill({json:body});
  });
  await context.routeWebSocket('**/ws/sessions/**', socket=>{
    socket.send('\x1b[36mAgent Console demo\x1b[0m\r\n\r\nWorkspace: /workspace/demo-app\r\nSynthetic terminal output. No agent or upstream service is running.\r\n\r\nReview the task in Input, then choose Send + Enter.\r\n');
    socket.onMessage(data=>{
      if(Buffer.isBuffer(data)){inputs.push(data.toString());socket.send('\r\nTask received.\r\nI will inspect the search component and check keyboard navigation.\r\n');}
    });
  });
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400)failures.push(`${r.status()} ${new URL(r.url()).pathname}`);});
  page.on('requestfailed',r=>failures.push(`${r.failure()?.errorText} ${new URL(r.url()).pathname}`));
  async function capture(name){await page.screenshot({path:join(output,name),animations:'disabled'});}
  async function frame(list, duration=1200){const filename=join(temp,`${list.length}-${Math.random().toString(16).slice(2)}.png`);await page.screenshot({path:filename,animations:'disabled'});list.push({filename,duration});}
  async function gif(name,list){
    const manifest=join(temp,name+'.json');await writeFile(manifest,JSON.stringify(list));
    const result=spawnSync(python,[join(root,'scripts/encode-readme-gif.py'),manifest,join(output,name)],{encoding:'utf8'});
    if(result.status!==0)throw new Error(result.stderr||result.stdout);
    assert.ok((await stat(join(output,name))).size<2_000_000,`${name} exceeds 2 MB`);
  }
  await page.goto(base+'/work');
  await page.locator('#work-list article').first().waitFor();
  await capture('work-overview.png');
  const createFrames=[];
  await frame(createFrames);
  await page.locator('#new-session').click();
  await page.locator('[name=task]').fill(task);
  await capture('new-session.png');
  await frame(createFrames,1800);
  await page.locator('#create-form button[type=submit]').click();
  await page.locator('#session-title').filter({hasText:'search-empty-state'}).waitFor();
  const terminal=page.frameLocator('iframe:not([hidden])');
  await terminal.locator('#connection').filter({hasText:'Connected'}).waitFor({state:'attached'});
  if(await terminal.locator('#input-drawer').isHidden())await terminal.locator('#toggle-composer').click();
  await terminal.locator('#composer').filter({visible:true}).waitFor();
  assert.equal(inputs.length,0,'Creating a session must not send its stored task');
  assert.equal(await terminal.locator('#composer').inputValue(),task);
  await frame(createFrames,2000);
  await terminal.locator('#send-enter').click();
  await page.waitForTimeout(200);
  assert.ok(inputs.length>0,'Explicit send must write the task to the mock terminal');
  await frame(createFrames,1800);
  await gif('create-and-send.gif',createFrames);
  await page.goto(base+'/work#session/search-coder');
  await page.locator('#session-title').filter({hasText:'search-coder'}).waitFor();
  await page.frameLocator('iframe:not([hidden])').locator('#connection').filter({hasText:'Connected'}).waitFor({state:'attached'});
  await capture('session-tree.png');
  await page.goto(base+'/environment?project_id=demo-project');
  await page.locator('#environment-entries').filter({hasText:'APP_MODE'}).waitFor();
  const envFrames=[];
  await frame(envFrames);
  await page.locator('#environment-name').fill('CMD_API_KEY');
  await page.locator('#environment-value').fill('demo-key-not-a-real-credential');
  await frame(envFrames,1500);
  await page.getByRole('button',{name:'Save variable',exact:true}).click();
  await page.locator('#environment-entries').filter({hasText:'CMD_API_KEY'}).waitFor();
  assert.equal(await page.locator('#environment-value').inputValue(),'');
  assert.match(await page.locator('#environment-entries').innerText(),/CMD_API_KEY.*protected.*next request/);
  assert.match(await page.locator('#environment-message').innerText(),/next broker request/);
  assert.ok(!(await page.locator('body').innerText()).includes('demo-key-not-a-real-credential'));
  assert.ok(!JSON.stringify(environment()).includes('demo-key-not-a-real-credential'));
  await capture('environment.png');
  await frame(envFrames,2500);
  await gif('save-protected-variable.gif',envFrames);
  assert.deepEqual(errors,[],'Browser JavaScript errors');
  assert.deepEqual(failures,[],'Broken assets, unmocked API calls or external requests');
  console.log('Captured 4 PNGs and 2 GIFs using synthetic data; no page errors or failed assets.');
  for(const name of ['work-overview.png','new-session.png','session-tree.png','environment.png','create-and-send.gif','save-protected-variable.gif'])console.log(`${name}: ${(await stat(join(output,name))).size} bytes`);
} finally {
  await browser?.close(); server.kill(); await rm(temp,{recursive:true,force:true});
}
