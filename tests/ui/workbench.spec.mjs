import { test, expect } from '@playwright/test';
function workbenchData(sessions,steps=[]) {
  const aliases=Object.fromEntries(steps.flatMap(step=>step.attempts.map(a=>[a.session_id,step.id])));
  const nodes=sessions.filter(s=>!aliases[s.id]).map(s=>({id:s.id,owner_id:aliases[s.parent_session_id]||s.parent_session_id||null,native_id:s.id,native_name:s.tmux_name,title:s.tmux_name,task:s.initial_task||'',repository:s.repository,profile:s.profile,tool:s.tool,model:s.model,project_id:s.project_id,project_name:s.project_name,mechanical:s.running?'running':'stopped',attention:s.attention_state||'normal',result_state:s.result?.outcome==='pass'?'completed':s.result?.outcome==='fail'?'failed':'unknown',result:s.result||null,waiting:false,needs_attention:!!s.attention_state&&s.attention_state!=='normal'||s.result?.outcome==='fail',last_activity:s.last_activity||'2026-10-02T00:00:00Z',attempts:[],readiness:{}}));
  for(const step of steps){const attempt=step.attempts.at(-1),native=sessions.find(s=>s.id===attempt?.session_id);nodes.push({id:step.id,owner_id:step.owner_id,native_id:native?.id||null,native_name:native?.tmux_name||null,title:step.task,task:step.task,...step.config,mechanical:native?.running?'running':'stopped',attention:'normal',result_state:'unknown',waiting:!attempt,needs_attention:false,last_activity:'2026-10-02T00:00:00Z',decision:step.decision,attempt_state:attempt?.state,attempts:step.attempts,readiness:{}});}
  for(const node of nodes){let root=node;while(root.owner_id)root=nodes.find(n=>n.id===root.owner_id);node.root_id=root.id;}
  const groups=nodes.filter(n=>!n.owner_id).map(n=>{const members=nodes.filter(m=>m.root_id===n.id),children=members.filter(m=>m.id!==n.id);return{root_id:n.id,member_ids:members.map(m=>m.id),priority:members.some(m=>m.needs_attention)?0:members.some(m=>m.mechanical==='running')?1:members.some(m=>m.waiting)?2:3,last_activity:n.last_activity,children_total:children.length,children_complete:children.filter(c=>c.result_state==='completed').length};}).sort((a,b)=>a.priority-b.priority);
  return {sessions,nodes,groups,aliases,readiness:{ready:true,warnings:[]}};
}
async function fixture(page) {
  const sessions = [{id:'root',tmux_name:'session-one',tool:'shell',profile:'coder',repository:'/tmp/repo',initial_task:'Fix the small layout issue',running:true,managed:true,attention_state:'normal',actions:['attach','interrupt','kill']}];
  const requests=[],steps=[];
  await page.route('**/api/**', async route => {
    const req=route.request(), path=new URL(req.url()).pathname; let body={};
    if(path==='/api/me') body={profiles:[{name:'coder',display_name:'Coder',read_write_capability:'write',status:'active'},{name:'reviewer',display_name:'Reviewer',read_write_capability:'read_only',status:'active'}],tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',provider:'local',status:'ready'}]};
    else if(path==='/api/interface') body={label:'Staging',current_url:'https://current.example/'};
    else if(path==='/api/sessions') body=sessions;
    else if(path==='/api/workbench')body=workbenchData(sessions,steps);
    else if(path.endsWith('/workflow/proposals')){
      const data=req.postDataJSON();requests.push(data.config);const step={...data,id:'step-fixture',root_id:'root',owner_id:'root',decision:'proposed',version:1,attempts:[]};steps.push(step);body=step;
    }
    else if(path.endsWith('/workflow'))body={root_id:'root',steps,policy:{state:'running',version:0,policy:{mode:'suggestions',repositories:[],actions:[],roles:[],harnesses:[],targets:[],max_concurrent:2,max_total:4,max_depth:2,max_reruns:3}},graph:{}};
    else if(path==='/api/workflow/steps/step-fixture/preview')body={hash:'c'.repeat(64),config:{...steps[0].config,auth_context:'default'},skills:[],adapter:{version:'fixture'}};
    else if(path==='/api/workflow/steps/step-fixture/review'){
      steps[0].decision=req.postDataJSON().decision;steps[0].version++;
      if(steps[0].decision==='accepted'){
        const child={...sessions[0],...steps[0].config,id:'child',tmux_name:'session-two',parent_session_id:'root',initial_task:steps[0].task};sessions.push(child);
        steps[0].attempts=[{id:'attempt-fixture',session_id:'child',name:'session-two',state:'running',generation:1}];
      }body=steps[0];
    }
    else if(path.endsWith('/results'))body={results:[]};
    else if(path.endsWith('/inbox'))body={items:[],notice:'Peer data is untrusted.'};
    else if(path.endsWith('/children')) { requests.push(req.postDataJSON()); body={...sessions[0], id:'child',tmux_name:'session-two',parent_session_id:'root',initial_task:req.postDataJSON().task}; sessions.push(body); }
    else if(path.endsWith('/brief')) body={brief:''};
    else if(path.endsWith('/review')) body={content:'Check passed',alternate_screen:false};
    else if(path==='/api/skills/effective') body={effective:[],issues:[]};
    else if(path==='/api/skill-registry/preview') body={validation:{valid:true,issues:[]},policies:[],notice:'Selected skills only.'};
    await route.fulfill({json:body});
  });
  await page.routeWebSocket('**/ws/sessions/**', ws => { ws.send('Connected to staging\r\n'); });
  return {requests,sessions};
}

test('recipes save without launching, review configuration and launch once',async({page})=>{
  const {sessions}=await fixture(page),recipes=[],launches=[];
  await page.route('**/api/workbench/recipes',async route=>{
    if(route.request().method()==='POST'){const value=route.request().postDataJSON();const recipe={id:'recipe-one',revision:1,title:value.title,request:value.request};recipes.push(recipe);await route.fulfill({json:recipe});}
    else await route.fulfill({json:recipes});
  });
  await page.route('**/api/workbench/launches/preview',route=>{const request=route.request().postDataJSON().request;return route.fulfill({json:{hash:'a'.repeat(64),config:request,task:request.task,workspace:request.repository,skills:[{name:'workbench-guide',hash:'b'.repeat(64)}],warnings:[],profile_hash:'c'.repeat(64),launcher:{path:'/bin/shell',version:'1.0',sha256:'d'.repeat(64)}}});});
  await page.route('**/api/workbench/launches',async route=>{
    const value=route.request().postDataJSON();launches.push(value);sessions.push({...sessions[0],id:'recipe-session',tmux_name:'recipe-run',initial_task:value.request.task});
    await route.fulfill({json:{state:'created',session_id:'recipe-session',name:'recipe-run'}});
  });
  await page.goto('/work');await page.locator('#new-session').click();
  await page.locator('[name=task]').fill('Run the bounded repository check');
  await page.locator('#recipe-save-panel > summary').click();await page.locator('[name=recipe_title]').fill('Repository check');await page.locator('#save-recipe').click();
  await expect(page.locator('#save-recipe')).toHaveText('Update recipe');expect(launches).toHaveLength(0);
  await page.locator('#cancel-create').click();await page.locator('#run-recipe').click();
  await page.getByRole('button',{name:'Use recipe',exact:true}).click();
  await expect(page.locator('[name=task]')).toHaveValue('Run the bounded repository check');
  await page.getByRole('button',{name:'Review launch',exact:true}).click();
  await expect(page.locator('#launch-preview')).toContainText('Repository');expect(launches).toHaveLength(0);
  await page.locator('#confirm-launch').click();await expect(page.locator('#session-title')).toHaveText('recipe-run');
  expect(launches).toHaveLength(1);expect(launches[0].expected_hash).toBe('a'.repeat(64));
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const frame=page.frameLocator('iframe:not([hidden])');await expect(frame.locator('#connection')).toHaveText('Connected');
});

test('configuration and continuation preserve settings and invalidate an edited preview',async({page},info)=>{
  const {sessions}=await fixture(page);sessions[0].running=false;sessions[0].actions=[];
  const config={tool:'shell',profile:'coder',repository:'/tmp/repo',worktree:true,auth_context:'default',model:null,reasoning_effort:null,plan_reasoning_effort:null,project_id:'project-one',agent_mode:null,provider:'local'};
  await page.route('**/api/workbench/sessions/root/configuration',route=>route.fulfill({json:{notice:'Recorded Console settings.',receipts:[{}],latest:{config,skills:[],created_at:'2026-10-02T00:00:00Z',workspace:'/tmp/preserved-worktree'}}}));
  const previews=[];
  await page.route('**/api/workbench/launches/preview',route=>{
    const body=route.request().postDataJSON();previews.push(body);return route.fulfill({json:{hash:'e'.repeat(64),config:body.request,task:body.request.task,workspace:'/tmp/preserved-worktree',skills:[],warnings:['New conversation in the preserved workspace.']}});
  });
  await page.goto('/work#session/session-one');if(info.project.name!=='desktop')await page.locator('#session-detail > summary').click();
  await page.locator('#show-configuration').click();await expect(page.locator('#session-configuration')).toContainText('/tmp/preserved-worktree');
  await page.locator('#continue-session').click();await page.getByRole('button',{name:'Review continuation',exact:true}).click();
  await expect(page.locator('#confirm-launch')).toBeVisible();expect(previews[0].source_session_id).toBe('root');
  expect(previews[0].request.project_id).toBe('project-one');expect(previews[0].request.plan_reasoning_effort).toBe(null);
  await page.locator('[name=task]').fill('A revised next task');await expect(page.locator('#launch-preview')).toBeHidden();
  await page.getByRole('button',{name:'Review continuation',exact:true}).click();await expect(page.locator('#launch-preview')).toContainText('A revised next task');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('work, manual child, drafts and mobile terminal use the real components',async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));const {requests}=await fixture(page);
  await page.goto('/work'); await expect(page.getByRole('heading',{name:'Work in progress'})).toBeVisible();
  await page.getByRole('link',{name:'Open work'}).click();
  await page.getByRole('button',{name:'+ Add session',exact:true}).click();
  const task='Review only the changed layout. Check the mobile navigation, the terminal scroll position while reading output, and whether a long task remains readable without making the session card occupy the entire phone screen.';
  await page.locator('[name=task]').fill(task);
  await page.locator('[name=profile]').selectOption('reviewer');
  await page.getByRole('button',{name:'Review next step',exact:true}).click();
  await expect(page.locator('#workflow-next-steps h3')).toHaveText(task.slice(0,77).trimEnd()+'…');
  await expect(page.getByText(task,{exact:true})).toBeHidden();
  await page.getByText('Task & configuration',{exact:true}).click();
  await expect(page.getByText(task,{exact:true})).toBeVisible();
  await page.getByText('Task & configuration',{exact:true}).click();
  await page.getByRole('button',{name:'Preview launch',exact:true}).click();
  await page.getByRole('button',{name:'Accept next step',exact:true}).click();
  await page.getByRole('button',{name:'Open session',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-two');
  if(info.project.name!=='desktop')await page.getByRole('button',{name:'Open terminal',exact:true}).click();
  expect(requests).toHaveLength(1);expect(requests[0].profile).toBe('reviewer');expect(requests[0].worktree).toBe(false);
  const frame=page.frameLocator('iframe:not([hidden])');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await frame.locator('#composer').fill('unsent draft');
  const iframe=page.frames().find(f=>f.url().includes('/terminal?session=session-two'));
  await iframe.evaluate(()=>{for(let i=0;i<400;i++)window.__terminal.writeln(`SCROLL LINE ${i}`);});
  await expect.poll(()=>iframe.evaluate(()=>window.__terminal.buffer.active.baseY)).toBeGreaterThan(200);
  await iframe.evaluate(()=>window.__terminal.scrollLines(-100));
  const before=await iframe.evaluate(()=>window.__terminal.buffer.active.viewportY);
  await iframe.evaluate(()=>window.__terminal.writeln('more output'));
  await expect(frame.locator('#new-output')).toBeVisible();
  expect(await iframe.evaluate(()=>window.__terminal.buffer.active.viewportY)).toBe(before);
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'Open terminal',exact:true}).click();
  await expect(frame.locator('#composer')).toHaveValue('unsent draft');
  await frame.locator('#new-output').click();
  await expect.poll(()=>iframe.evaluate(()=>window.__terminal.buffer.active.viewportY===window.__terminal.buffer.active.baseY)).toBe(true);
  if(info.project.name==='desktop'){
    await page.getByRole('button',{name:'Full screen',exact:true}).click();
    await page.getByRole('button',{name:'Restore',exact:true}).click();
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await iframe.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole('button',{name:'Close',exact:true}).click();
  if (info.project.name !== 'desktop') await page.locator('#session-detail > summary').click();
  await page.locator('#attention-form [name=note]').fill('draft status note');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#attention-form [name=note]')).toHaveValue('draft status note');
  expect(errors).toEqual([]);
});
test('terminal reconnect preserves reading position and composer survives reload',async({page})=>{
  await fixture(page);await page.goto('/terminal?session=session-one');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await page.locator('#composer').fill('keep this draft');
  await page.evaluate(()=>{for(let i=0;i<350;i++)window.__terminal.writeln(`LINE ${i}`);});
  await expect.poll(()=>page.evaluate(()=>window.__terminal.buffer.active.baseY)).toBeGreaterThan(200);
  await page.evaluate(()=>window.__terminal.scrollLines(-80));
  const before=await page.evaluate(()=>window.__terminal.buffer.active.viewportY);
  await page.locator('#reconnect').evaluate(el=>{el.disabled=false;el.click();});
  await expect(page.locator('#connection')).toHaveText('Connected');
  await expect.poll(()=>page.evaluate(()=>window.__terminal.buffer.active.viewportY)).toBe(before);
  await page.reload();await expect(page.locator('#composer')).toHaveValue('keep this draft');
});
test('nested terminal owns wheel and touch scrolling without moving the work page', async({page},info)=>{
  await fixture(page); await page.goto('/work#session/session-one');
  await page.getByRole('button',{name:'Open terminal',exact:true}).click();
  const frame=page.frames().find(f=>f.url().includes('/terminal?'));
  await expect.poll(()=>frame.evaluate(()=>Boolean(window.__terminal))).toBe(true);
  await frame.evaluate(()=>{for(let i=0;i<400;i++)window.__terminal.writeln(`SCROLL ${i}`);});
  await expect.poll(()=>frame.evaluate(()=>window.__terminal.buffer.active.baseY)).toBeGreaterThan(250);
  await frame.getByRole('button',{name:'Scroll',exact:true}).click();
  const start=await frame.evaluate(()=>window.__terminal.buffer.active.viewportY), pageY=await page.evaluate(()=>scrollY);
  const box=await page.frameLocator('iframe').locator('.xterm-screen').boundingBox();
  if(info.project.name==='desktop'){
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-480);
  }else{
    const cdp=await page.context().newCDPSession(page);
    const x=box.x+box.width/2,y=box.y+50;
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
    for(let i=1;i<=6;i++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y+i*24}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await cdp.detach();
  }
  await expect.poll(()=>frame.evaluate(()=>window.__terminal.buffer.active.viewportY)).toBeLessThan(start);
  expect(await page.evaluate(()=>scrollY)).toBe(pageY);
});

test('skills inspection and exact revision approval work on desktop and phone', async({page}) => {
  const errors=[];page.on('pageerror', e=>errors.push(e.message));
  await fixture(page);
  const item={name:'bounded-coding',description:'Apply a small fix',hash:'a'.repeat(64),revision:'rev-1',source:'/workspace/skills/bounded-coding',scope:'global',risk:'low',approval:'ask',compatible_profiles:[],compatible_harnesses:['codex'],files:['SKILL.md'],required_services:[],issues:[],warnings:[],trust:'local-trusted',validation:'valid'};
  let approved=null;
  await page.route('**/api/skill-registry', route=>route.fulfill({json:{entries:[item],imports:[]}}));
  await page.route('**/api/skill-registry/bounded-coding/approve',route=>{approved=route.request().postDataJSON();return route.fulfill({json:{hash:item.hash}});});
  await page.goto('/work#session/missing-fixture');
  await expect(page.getByText('This session is no longer available. Return to Work.',{exact:true})).toBeVisible();
  await page.getByRole('link',{name:'Settings',exact:true}).click();
  await page.getByRole('link',{name:'Manage skills',exact:true}).click();
  await expect(page.locator('#notice')).toBeHidden();
  await expect(page.getByRole('heading',{name:'Skills',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Inspect',exact:true}).click();
  await expect(page.getByText(item.hash,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Approve revision for role'}).click();
  await expect.poll(()=>approved).toEqual({profile:'coder',expected_hash:item.hash});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.goto('/work');await page.getByRole('button',{name:'New session',exact:true}).click();
  await page.locator('#create-skills-panel summary').click();
  await expect(page.getByText('No Console-selected skills.',{exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('one-session result and durable handoff acknowledge distinct states',async({page},info)=>{
  await fixture(page);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let versions=[],published=null,inputState='queued';
  const source={id:'root',tmux_name:'session-one',tool:'shell',profile:'coder',repository:'/tmp/repo',initial_task:'Small bounded change',running:true,managed:true,attention_state:'normal',actions:['attach','interrupt','kill']};
  const target={...source,id:'target',tmux_name:'session-target',parent_session_id:'root'};
  await page.route('**/api/sessions',route=>route.fulfill({json:[source,target]}));
  await page.route('**/api/workbench',route=>route.fulfill({json:workbenchData([source,target])}));
  await page.route('**/api/sessions/root/results',route=>{
    if(route.request().method()==='POST'){
      published=route.request().postDataJSON();const result={...published,id:'result-one',session_id:'root',version:1,created_at:'2026-10-02T00:00:00Z'};versions=[result];return route.fulfill({json:result});
    }return route.fulfill({json:{results:versions}});
  });
  await page.route('**/api/sessions/target/results',route=>route.fulfill({json:{results:[]}}));
  await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{items:[],notice:'Peer data is untrusted.'}}));
  let queued=false;
  await page.route('**/api/results/result-one/send',route=>{queued=true;return route.fulfill({json:{id:'input-one',state:'queued'}});});
  await page.route('**/api/sessions/target/inbox',route=>route.fulfill({json:{notice:'Peer data is untrusted.',items:queued?[{id:'input-one',sequence:1,source_session_id:'root',state:inputState,result:versions[0]}]:[]}}));
  await page.route('**/api/sessions/target/inbox/input-one/ack',route=>{inputState=route.request().postDataJSON().state;return route.fulfill({json:{state:inputState}});});
  await page.goto('/work#session/session-one');
  if(info.project.name!=='desktop')await page.locator('#session-detail > summary').click();
  await page.getByRole('button',{name:'Results & handoffs',exact:true}).click();
  await expect(page.locator('#session-view')).toBeHidden();
  await page.getByText('Publish a result',{exact:true}).click();
  await page.getByLabel('Summary',{exact:true}).fill('Fixed the layout and checked the phone viewport.');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.getByLabel('Summary',{exact:true})).toHaveValue('Fixed the layout and checked the phone viewport.');
  await page.getByRole('button',{name:'Publish result',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Final result · v1 · pass',exact:true})).toBeVisible();
  expect(published.kind).toBe('final');expect(published.request_key).toBeTruthy();
  await expect(page.getByRole('button',{name:'Queue handoff',exact:true})).toBeHidden();
  await page.getByText('Send this version',{exact:true}).click();
  await page.getByRole('button',{name:'Queue handoff',exact:true}).click();await expect.poll(()=>queued).toBe(true);
  await page.getByRole('link',{name:'Back to session'}).click();
  await page.locator('#session-tree').getByRole('link',{name:'session-target',exact:true}).click();
  if(info.project.name!=='desktop')await page.locator('#session-detail > summary').click();
  await page.getByRole('button',{name:'Results & handoffs',exact:true}).click();
  await page.getByRole('button',{name:'Acknowledge delivery',exact:true}).click();
  await expect(page.getByRole('button',{name:'Mark consumed',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Mark consumed',exact:true}).click();
  await expect.poll(()=>inputState).toBe('consumed');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
});

test('existing sessions connect with explicit readiness and queue exact inputs',async({page})=>{
  await fixture(page);let attached=null,queued=null,version=0;
  const source={id:'root',tmux_name:'session-one',tool:'shell',profile:'general',running:true,managed:true,attention_state:'normal',actions:[]};
  const target={...source,id:'target',tmux_name:'session-existing'};
  await page.route('**/api/sessions',route=>route.fulfill({json:[source,target]}));
  await page.route('**/api/workbench',route=>route.fulfill({json:workbenchData([source,target])}));
  await page.route('**/api/sessions/root/results',route=>route.fulfill({json:{results:[]}}));
  await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{items:[],notice:'Peer data is untrusted.'}}));
  await page.route('**/api/sessions/root/connections',route=>route.fulfill({json:{version,nodes:version?[{session_id:'root',owner_id:null},{session_id:'target',owner_id:'root',purpose:attached.purpose}]:[],edges:version?[{source_id:'root',target_id:'target',readiness:'after-ready'}]:[],readiness:version?{root:{blocked:false,stale:false,delivered:false,reasons:[],signature:'b'.repeat(64)},target:{blocked:false,stale:false,delivered:false,reasons:[],signature:'a'.repeat(64)}}:{}}}));
  await page.route('**/api/sessions/root/connections/attach',route=>{attached=route.request().postDataJSON();version=1;return route.fulfill({json:{version}});});
  await page.route('**/api/sessions/target/connections/deliver',route=>{queued=route.request().postDataJSON();return route.fulfill({json:{id:'delivery-test'}});});
  await page.goto('/work#session/session-one');
  await page.getByRole('button',{name:'Results & handoffs',exact:true}).click();
  await page.getByText('Connected inputs',{exact:true}).click();
  await page.getByText('Attach an existing session',{exact:true}).click();
  await page.getByLabel('Purpose',{exact:true}).fill('Check this explicit candidate');
  await page.getByLabel('Use this session’s result',{exact:true}).selectOption('after-ready');
  await page.getByRole('button',{name:'Attach existing session',exact:true}).click();
  await expect.poll(()=>attached).toEqual({session_id:'target',purpose:'Check this explicit candidate',expected_version:0,dependencies:[{source_id:'root',readiness:'after-ready'}]});
  await expect(page.getByText('Owned by session-one',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Queue ready inputs',exact:true}).click();
  await expect.poll(()=>queued).toEqual({expected_version:1,expected_signature:'a'.repeat(64)});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('attention, results, readiness and persisted filters lead the work view',async({page})=>{
  await fixture(page);
  const base={tool:'shell',profile:'coder',repository:'/tmp/repo',managed:true,attention_state:'normal',actions:['attach','kill'],last_activity:'2026-10-02T00:00:00Z'};
  const result={id:'final-one',session_id:'done',kind:'final',outcome:'pass',summary:'The requested fix passed its checks.',created_at:'2026-10-02T00:00:00Z',artifacts:[]};
  const sessions=[{...base,id:'running',tmux_name:'running-work',running:true},{...base,id:'needs-input',tmux_name:'attention-work',running:true,attention_state:'needs_input'},
    {...base,id:'done',tmux_name:'completed-work',result}, {...base,id:'child',tmux_name:'completed-child',parent_session_id:'running',result:{...result,session_id:'child'}},
    {...base,id:'failed',tmux_name:'failed-work',result:{...result,outcome:'fail',summary:'The targeted check failed.'}}];
  await page.route('**/api/workbench',route=>{const data=workbenchData(sessions);data.readiness={ready:false,warnings:['Selected harness needs account setup']};return route.fulfill({json:data});});
  await page.goto('/work');
  await expect(page.locator('#work-list .card').first()).toContainText('attention-work');
  await expect(page.locator('#attention-strip')).toContainText('2 sessions need attention');
  await expect(page.locator('#attention-strip')).toContainText('1 readiness warning');
  await expect(page.getByText('1/1 children complete',{exact:true})).toBeVisible();
  await expect(page.locator('#work-list').getByText('Terminal: stopped',{exact:true}).first()).toBeVisible();
  await expect(page.getByText('The targeted check failed.',{exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:'Review failure',exact:true})).toBeVisible();
  await page.getByText('More filters',{exact:true}).click();
  await page.getByRole('combobox',{name:'Result',exact:true}).selectOption('failed');
  await expect(page.locator('#work-list .card')).toHaveCount(1);
  await page.reload();await expect(page.locator('#work-list .card')).toHaveCount(1);await expect(page.locator('#work-list .card')).toContainText('failed-work');
  await page.getByText('More filters',{exact:true}).click();await page.getByRole('button',{name:'Reset filters',exact:true}).click();
  await expect(page.locator('#work-list .card')).toHaveCount(4);
  await page.getByRole('combobox',{name:'Show sessions',exact:true}).selectOption('all');
  await expect(page.locator('#work-list .card')).toHaveCount(5);
  await page.locator('#state-filter').selectOption('attention');await expect(page.locator('#work-list .card')).toHaveCount(2);
  await page.getByRole('button',{name:'Reset filters',exact:true}).click();
  await page.getByRole('link',{name:'1 readiness warning',exact:true}).click();await expect(page.getByText('Selected harness needs account setup',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('empty work and keyboard tree focus survive refresh without touching the terminal',async({page},info)=>{
  await fixture(page);let sessions=[];
  const root={id:'root',tmux_name:'session-one',tool:'shell',profile:'coder',repository:'/tmp/repo',running:true,managed:true,attention_state:'normal',actions:['attach','kill']};
  await page.route('**/api/workbench',route=>route.fulfill({json:workbenchData(sessions)}));
  await page.goto('/work');await expect(page.getByText('Your staging workspace is ready. Start a session to begin.',{exact:true})).toBeVisible();
  await expect(page.locator('#attention-strip')).toBeHidden();
  sessions=[root,{...root,id:'child',tmux_name:'session-child',parent_session_id:'root'}];await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await page.getByRole('link',{name:'Open work',exact:true}).click();
  const rootLink=page.locator('#session-tree').getByRole('link',{name:'session-one',exact:true});await rootLink.focus();await page.keyboard.press('ArrowDown');
  await expect(page.locator('#session-tree button').first()).toBeFocused();
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.locator('#session-tree button').first()).toBeFocused();
  if(info.project.name!=='desktop')await page.getByRole('button',{name:'Open terminal',exact:true}).click();
  const frame=page.frameLocator('iframe:not([hidden])');await frame.locator('#composer').fill('Preserved during work updates');await frame.locator('#composer').focus();
  const frameHandle=page.frames().find(f=>f.url().includes('/terminal?'));
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(frame.locator('#composer')).toBeFocused();await expect(frame.locator('#composer')).toHaveValue('Preserved during work updates');
  expect(page.frames()).toContain(frameHandle);
});
