import { test, expect } from '@playwright/test';
async function openInput(target){if(await target.locator('#input-drawer').isHidden())await target.locator('#toggle-composer').click();}
async function openMore(target){if(!await target.locator('#terminal-more').evaluate(e=>e.open))await target.locator('#terminal-more > summary').click();}
async function ensureTerminal(page){
  await expect(page.locator('#session-title')).toBeVisible();
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  await expect(page.locator('#terminal-panel')).toBeVisible();
}

function workbenchData(sessions,steps=[]) {
  const aliases=Object.fromEntries(steps.flatMap(step=>step.attempts.map(a=>[a.session_id,step.id])));
  const nodes=sessions.filter(s=>!aliases[s.id]).map(s=>({id:s.id,owner_id:aliases[s.parent_session_id]||s.parent_session_id||null,native_id:s.id,native_name:s.tmux_name,title:s.tmux_name,task:s.initial_task||'',repository:s.repository,profile:s.profile,tool:s.tool,model:s.model,project_id:s.project_id,project_name:s.project_name,hidden:!!s.hidden&&!s.running,reviewable:s.attention_state==='ready_for_review'||(!s.reviewed&&s.result?.outcome==='fail'&&s.attention_state==='normal'),mechanical:s.running?'running':'stopped',attention:s.attention_state||'normal',result_state:s.result?.outcome==='pass'?'completed':s.result?.outcome==='fail'?'failed':'unknown',result:s.result||null,waiting:false,needs_attention:!!s.attention_state&&s.attention_state!=='normal'||s.result?.outcome==='fail'&&!s.reviewed,created_at:s.created_at||'',last_activity:s.last_activity||'2026-10-02T00:00:00Z',attempts:[],readiness:{}}));
  for(const step of steps){const attempt=step.attempts.at(-1),native=sessions.find(s=>s.id===attempt?.session_id);nodes.push({id:step.id,owner_id:step.owner_id,native_id:native?.id||null,native_name:native?.tmux_name||null,title:step.task,task:step.task,...step.config,mechanical:native?.running?'running':'stopped',attention:'normal',result_state:'unknown',waiting:!attempt,needs_attention:false,created_at:step.created_at||'',last_activity:'2026-10-02T00:00:00Z',decision:step.decision,attempt_state:attempt?.state,attempts:step.attempts,readiness:{}});}
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
    else if(path==='/api/workflow/steps/step-fixture/preview')body={hash:'c'.repeat(64),config:{...steps[0].config,auth_context:'default'},skills:[],adapter:{version:'fixture'},native_permission_mode:'read-only'};
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
  return {requests,sessions,steps};
}

test('recipes save without launching and start with one explicit action',async({page})=>{
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
  await page.getByRole('button',{name:'Start session',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('recipe-run');
  await expect(page.locator('#notice')).toBeHidden();
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
  await page.goto('/work#session/session-one');await page.locator('#session-detail > summary').click();
  await page.locator('#show-configuration').click();await expect(page.locator('#session-configuration')).toContainText('/tmp/preserved-worktree');
  await page.locator('#continue-session').click();await page.locator('#preview-launch').click();
  await expect(page.locator('#confirm-launch')).toBeVisible();expect(previews[0].source_session_id).toBe('root');
  expect(previews[0].request.project_id).toBe('project-one');expect(previews[0].request.plan_reasoning_effort).toBe(null);
  await page.locator('[name=task]').fill('A revised next task');await expect(page.locator('#launch-preview')).toBeHidden();
  await page.locator('#preview-launch').click();await expect(page.locator('#launch-preview')).toContainText('A revised next task');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('pending status updates deduplicate through refresh and errors remain attributed',async({page},info)=>{
  const {sessions}=await fixture(page);sessions.push({...sessions[0],id:'other',tmux_name:'other-session'});
  let count=0,release;
  await page.route('**/api/sessions/session-one/attention',async route=>{
    count++;await new Promise(resolve=>{release=resolve;});
    await route.fulfill({status:409,json:{detail:'Status changed; reload and retry'}});
  });
  await page.goto('/work#session/session-one');
  await page.locator('#session-detail > summary').click();
  await page.locator('#attention-controls > summary').click();
  const submit=page.locator('#attention-form button[type=submit]');
  await submit.click();await expect(submit).toBeDisabled();
  await page.locator('#attention-controls').evaluate(e=>{e.open=true;});await page.locator('#attention-form').dispatchEvent('submit');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(submit).toBeDisabled();expect(count).toBe(1);
  await page.evaluate(()=>{location.hash='#session/other-session';});
  await expect(page.locator('#session-title')).toHaveText('other-session');
  await expect(submit).toBeEnabled();release();
  await expect(page.locator('#notice')).toContainText('session-one: Status changed');
  await expect(page.locator('#session-title')).toHaveText('other-session');
});

test('late continuation response cannot open a draft over another session',async({page},info)=>{
  const {sessions}=await fixture(page);sessions[0].running=false;
  sessions.push({...sessions[0],id:'other',tmux_name:'other-session'});
  let release;
  await page.route('**/api/workbench/sessions/root/configuration',async route=>{
    await new Promise(resolve=>{release=resolve;});
    await route.fulfill({json:{latest:{config:{tool:'shell',profile:'coder',repository:'/tmp/repo'}}}});
  });
  await page.goto('/work#session/session-one');
  await page.locator('#session-detail > summary').click();
  await page.locator('#continue-session').click();
  await expect(page.locator('#continue-session')).toBeDisabled();
  await expect.poll(()=>typeof release).toBe('function');
  await page.evaluate(()=>{location.hash='#session/other-session';});
  await expect(page.locator('#session-title')).toHaveText('other-session');release();
  await expect(page.locator('#continue-session')).toBeEnabled();
  await expect(page.locator('#create-dialog')).not.toBeVisible();
});

test('exact release preview authorizes once and uncertainty exposes observation instead of retry',async({page})=>{
  await fixture(page);const requests=[],grants=[];let statusReads=0;
  const result={id:'candidate',session_id:'root',version:1,kind:'final',outcome:'pass',summary:'Bounded change verified',checks:['Relevant tests passed'],artifacts:[{kind:'commit',sha:'a'.repeat(40),hash:'b'.repeat(64),label:'Candidate'}],created_at:'2026-10-02T00:00:00Z'};
  const view={candidate_sha:'a'.repeat(40),target:'test-stage',target_label:'Test staging',action:'deploy',evidence:[{id:'candidate'}],hash:'c'.repeat(64)};
  await page.route('**/api/sessions/root/results',route=>route.fulfill({json:{results:[result]}}));
  await page.route('**/api/workflow/release-targets',route=>route.fulfill({json:[{id:'test-stage',label:'Test staging',actions:['deploy']}]}));
  await page.route('**/api/sessions/root/releases',route=>{
    const attempt=grants[0]?.attempts.at(-1);
    if(attempt?.state==='queued'&&++statusReads>=2){attempt.state='unknown';attempt.active=false;}
    return route.fulfill({json:grants});
  });
  await page.route('**/api/workflow/releases/evidence/candidate',route=>route.fulfill({json:[{...result,eligible:true,reason:''}]}));
  await page.route('**/api/workflow/releases/preview',route=>route.fulfill({json:view}));
  await page.route('**/api/workflow/releases/authorize',route=>{requests.push(route.request().postDataJSON());const grant={id:'release-one',preview:view,attempts:[]};grants.push(grant);return route.fulfill({json:grant});});
  await page.route('**/api/workflow/releases/release-one/attempts',route=>{
    const request=route.request().postDataJSON();requests.push(request);
    const attempt={id:'attempt-'+requests.length,mode:request.mode,state:request.mode==='apply'?'queued':'applied',active:request.mode==='apply',created_at:'2026-10-02',summary:request.mode==='apply'?'Acknowledgment lost':'Observed exact candidate'};
    grants[0].attempts.push(attempt);return route.fulfill({json:attempt});
  });
  await page.goto('/work#results/session-one');await page.locator('#workflow-releases > summary').click();
  await page.getByRole('button',{name:'Preview release',exact:true}).click();
  await expect(page.locator('#workflow-releases')).toContainText('a'.repeat(40));expect(requests).toHaveLength(0);
  await page.getByRole('button',{name:'Authorize and run',exact:true}).click();
  await expect(page.locator('#workflow-releases')).toContainText('queued');
  await page.locator('#workflow-releases input[type=checkbox]').uncheck();
  await expect(page.getByRole('button',{name:'Check external outcome',exact:true})).toBeVisible();
  await expect(page.locator('#workflow-releases input[type=checkbox]')).not.toBeChecked();
  await expect(page.getByRole('button',{name:'Retry authorized release',exact:true})).toHaveCount(0);
  expect(requests).toHaveLength(2);expect(requests[0].expected_hash).toBe(view.hash);expect(requests[1].mode).toBe('apply');
  await page.getByRole('button',{name:'Check external outcome',exact:true}).click();
  await expect(page.locator('#workflow-releases')).toContainText('Observed exact candidate');expect(requests[2].mode).toBe('probe');
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
  await expect(page.locator('#scheduled-options')).toBeHidden();
  await page.getByRole('button',{name:'Add session',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-two');
  expect(requests).toHaveLength(1);expect(requests[0].profile).toBe('reviewer');expect(requests[0].worktree).toBe(false);
  const frame=page.frameLocator('iframe:not([hidden])');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await openInput(frame);await frame.locator('#composer').fill('unsent draft');
  const iframe=page.frames().find(f=>f.url().includes('/terminal?session=session-two'));
  await iframe.evaluate(()=>{for(let i=0;i<400;i++)window.__terminal.writeln(`SCROLL LINE ${i}`);});
  await expect.poll(()=>iframe.evaluate(()=>window.__terminal.buffer.active.baseY)).toBeGreaterThan(200);
  await iframe.evaluate(()=>window.__terminal.scrollLines(-100));
  const before=await iframe.evaluate(()=>window.__terminal.buffer.active.viewportY);
  await iframe.evaluate(()=>window.__terminal.writeln('more output'));
  await expect(frame.locator('#new-output')).toBeVisible();
  expect(await iframe.evaluate(()=>window.__terminal.buffer.active.viewportY)).toBe(before);
  await page.getByRole('button',{name:'Close terminal',exact:true}).click();
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
  await page.getByRole('button',{name:'Close terminal',exact:true}).click();
  await page.locator('#session-detail > summary').click();
  await page.locator('#attention-controls').evaluate(e=>{e.open=true;});await page.locator('#attention-form [name=note]').fill('draft status note');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#attention-form [name=note]')).toHaveValue('draft status note');
  expect(errors).toEqual([]);
});
test('terminal reconnect preserves reading position and composer survives reload',async({page})=>{
  await fixture(page);await page.goto('/terminal?session=session-one');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await openInput(page);await page.locator('#composer').fill('keep this draft');
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
  await ensureTerminal(page);
  const frame=page.frames().find(f=>f.url().includes('/terminal?'));
  await expect.poll(()=>frame.evaluate(()=>Boolean(window.__terminal))).toBe(true);
  await frame.evaluate(()=>{for(let i=0;i<400;i++)window.__terminal.writeln(`SCROLL ${i}`);});
  await expect.poll(()=>frame.evaluate(()=>window.__terminal.buffer.active.baseY)).toBeGreaterThan(250);
  await openMore(frame);await frame.getByRole('button',{name:'Type & scroll',exact:true}).click();
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
  await page.route('**/api/skills', route=>route.fulfill({json:{
    entries:[{name:item.name,assigned_to:[{profile:'coder'}]}],
    providers:[{tool:'codex',discovery:{status:'uncertain',uncertainty_reasons:['configured discovery sources are not inspected']}}],
    diagnostics:[{skill:item.name,tool:'codex',linked:true,state:'present',version_state:'verified',installed_version:'0.159.2',materialized_path:'/fixture/native/skills/bounded-coding'}]
  }}));
  await page.route('**/api/skill-registry/bounded-coding/approve',route=>{approved=route.request().postDataJSON();return route.fulfill({json:{hash:item.hash}});});
  await page.goto('/work#session/missing-fixture');
  await expect(page.getByText('This session is no longer available. Return to Work.',{exact:true})).toBeVisible();
  await page.getByRole('link',{name:'Settings',exact:true}).click();
  await page.getByRole('link',{name:'Manage skills',exact:true}).click();
  await expect(page.locator('#notice')).toBeHidden();
  await expect(page.getByRole('heading',{name:'Skills',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Inspect',exact:true}).click();
  await expect(page.getByText(item.hash,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Check harness delivery',exact:true}).click();
  await expect(page.getByText('codex: synced · discovery uncertain',{exact:true})).toBeVisible();
  await expect(page.getByText('Current role assignments: coder',{exact:true})).toBeVisible();
  await page.getByText('codex diagnostics',{exact:true}).click();
  await expect(page.getByText('Version: 0.159.2 (verified)',{exact:true})).toBeVisible();
  await expect(page.getByText('Discovery: configured discovery sources are not inspected',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Approve revision for role'}).click();
  await expect.poll(()=>approved).toEqual({profile:'coder',expected_hash:item.hash});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.goto('/work');await page.getByRole('button',{name:'New session',exact:true}).click();
  await page.locator('#create-skills-panel summary').click();
  await expect(page.getByText('No Console-selected skills.',{exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('Git skill import stages an exact provenance before separate activation', async({page}) => {
  await fixture(page);
  const sha='f'.repeat(40), hash='a'.repeat(64), source='https://example.invalid/skills.git';
  const provenance={kind:'git',source,revision:sha,requested_revision:'main',subdirectory:'guides/portable-guide'};
  const record={id:'import-git-fixture',name:'portable-guide',hash,status:'imported-unreviewed',provenance};
  const item={name:'portable-guide',description:'Imported guide',hash,revision:sha,source,scope:'global',risk:'low',approval:'allow',compatible_profiles:[],compatible_harnesses:[],files:['SKILL.md'],required_services:[],issues:[],warnings:[],provenance,provenance_content_matches:true,declared_revision:'claimed-version',declared_source:'https://declared.invalid/repo'};
  let submitted=null, activated=null;
  await page.route('**/api/skill-registry',route=>route.fulfill({json:{entries:[],imports:submitted?[record]:[]}}));
  await page.route('**/api/skill-registry/imports',route=>{submitted=route.request().postDataJSON();return route.fulfill({json:record});});
  await page.route('**/api/skill-registry/imports/import-git-fixture',route=>route.fulfill({json:{...record,staged_path:'/state/imports/fixture',package:item}}));
  await page.route('**/api/skill-registry/imports/import-git-fixture/activate',route=>{activated=route.request().postDataJSON();return route.fulfill({json:{hash}});});
  await page.goto('/work');
  await page.getByRole('link',{name:'Settings',exact:true}).click();
  await page.getByRole('link',{name:'Manage skills',exact:true}).click();
  await page.getByLabel('Skill import source').fill(source);
  await page.getByText('Git revision and package directory',{exact:true}).click();
  await page.getByLabel('Git revision',{exact:true}).fill('main');
  await page.getByLabel('Git package directory').fill('guides/portable-guide');
  await page.getByRole('button',{name:'Stage import',exact:true}).click();
  await expect.poll(()=>submitted).toEqual({source,revision:'main',subdirectory:'guides/portable-guide'});
  expect(activated).toBeNull();
  await page.getByRole('button',{name:'Inspect staged revision'}).click();
  await expect(page.getByText(sha,{exact:true})).toBeVisible();
  await expect(page.getByText('Package-declared revision: claimed-version',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(activated).toBeNull();
  await page.getByRole('button',{name:'Review & activate this revision'}).click();
  await expect.poll(()=>activated).toEqual({expected_hash:hash,decision:'reviewed',services_verified:false});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('one-session result and durable handoff acknowledge distinct states',async({page},info)=>{
  await fixture(page);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let versions=[],published=null,inputState='queued';
  const source={id:'root',tmux_name:'session-one',tool:'shell',profile:'coder',repository:'/tmp/repo',initial_task:'Small bounded change',running:true,managed:true,attention_state:'normal',actions:['attach','interrupt','kill']};
  const target={...source,id:'target',tmux_name:'session-target',parent_session_id:'root'};
  await page.route('**/api/sessions',route=>route.fulfill({json:[source,target]}));
  await page.route('**/api/workbench?*',route=>route.fulfill({json:workbenchData([source,target])}));
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
  await page.locator('#session-detail > summary').click();
  if(!await page.locator('#session-detail').evaluate(e=>e.open))await page.locator('#session-detail > summary').click();
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
  await page.locator('#session-detail > summary').click();
  if(!await page.locator('#session-detail').evaluate(e=>e.open))await page.locator('#session-detail > summary').click();
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
  await page.route('**/api/workbench?*',route=>route.fulfill({json:workbenchData([source,target])}));
  await page.route('**/api/sessions/root/results',route=>route.fulfill({json:{results:[]}}));
  await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{items:[],notice:'Peer data is untrusted.'}}));
  await page.route('**/api/sessions/root/connections',route=>route.fulfill({json:{version,nodes:version?[{session_id:'root',owner_id:null},{session_id:'target',owner_id:'root',purpose:attached.purpose}]:[],edges:version?[{source_id:'root',target_id:'target',readiness:'after-ready'}]:[],readiness:version?{root:{blocked:false,stale:false,delivered:false,reasons:[],signature:'b'.repeat(64)},target:{blocked:false,stale:false,delivered:false,reasons:[],signature:'a'.repeat(64)}}:{}}}));
  await page.route('**/api/sessions/root/connections/attach',route=>{attached=route.request().postDataJSON();version=1;return route.fulfill({json:{version}});});
  await page.route('**/api/sessions/target/connections/deliver',route=>{queued=route.request().postDataJSON();return route.fulfill({json:{id:'delivery-test'}});});
  await page.goto('/work#session/session-one');
  if(!await page.locator('#session-detail').evaluate(e=>e.open))await page.locator('#session-detail > summary').click();
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
  await page.route('**/api/workbench?*',route=>{const data=workbenchData(sessions);data.readiness={ready:false,warnings:['Selected harness needs account setup']};return route.fulfill({json:data});});
  await page.goto('/work');
  await expect(page.locator('#work-list .card').first()).toContainText('attention-work');
  await expect(page.locator('#attention-strip')).toContainText('2 sessions need attention');
  await expect(page.locator('#attention-strip')).toContainText('1 readiness warning');
  await expect(page.getByText('1/1 children complete',{exact:true})).toBeVisible();
  await expect(page.locator('#work-history')).not.toHaveAttribute('open','');
  await page.locator('#work-history > summary').click();
  await expect(page.locator('#work-list').getByText('Terminal: stopped',{exact:true}).first()).toBeVisible();
  await expect(page.getByText('The targeted check failed.',{exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:'Review failure',exact:true})).toBeVisible();
  await page.getByText('More filters',{exact:true}).click();
  await page.getByRole('combobox',{name:'Result',exact:true}).selectOption('failed');
  await expect(page.locator('#work-list .card')).toHaveCount(1);
  await page.reload();await expect(page.locator('#work-list .card')).toHaveCount(1);await expect(page.locator('#work-list .card')).toContainText('failed-work');
  await page.getByText('More filters',{exact:true}).click();await page.getByRole('button',{name:'Reset filters',exact:true}).click();
  await expect(page.locator('#work-list .card')).toHaveCount(2);
  await page.locator('#work-history > summary').click();
  await expect(page.locator('#work-list .card')).toHaveCount(4);
  await page.getByRole('combobox',{name:'Show sessions',exact:true}).selectOption('all');
  await page.locator('#work-history > summary').click();
  await expect(page.locator('#work-list .card')).toHaveCount(5);
  await page.locator('#state-filter').selectOption('attention');await expect(page.locator('#work-list .card')).toHaveCount(2);
  await page.getByRole('button',{name:'Reset filters',exact:true}).click();
  await page.getByRole('link',{name:'1 readiness warning',exact:true}).click();await expect(page.getByText('Selected harness needs account setup',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('empty work and keyboard tree focus survive refresh without touching the terminal',async({page},info)=>{
  await fixture(page);let sessions=[];
  const root={id:'root',tmux_name:'session-one',tool:'shell',profile:'coder',repository:'/tmp/repo',running:true,managed:true,attention_state:'normal',actions:['attach','kill']};
  await page.route('**/api/workbench?*',route=>route.fulfill({json:workbenchData(sessions)}));
  await page.goto('/work');await expect(page.getByText('Your workspace is ready. Start a session to begin.',{exact:true})).toBeVisible();
  await expect(page.locator('#attention-strip')).toBeHidden();
  sessions=[root,{...root,id:'child',tmux_name:'session-child',parent_session_id:'root'}];await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await page.getByRole('link',{name:'Open work',exact:true}).click();
  const rootLink=page.locator('#session-tree').getByRole('link',{name:'session-one',exact:true});await rootLink.focus();await page.keyboard.press('ArrowDown');
  await expect(page.locator('#session-tree button').first()).toBeFocused();
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.locator('#session-tree button').first()).toBeFocused();
  if(info.project.name!=='desktop')await page.getByRole('button',{name:'Open terminal',exact:true}).click();
  const frame=page.frameLocator('iframe:not([hidden])');await openInput(frame);await frame.locator('#composer').fill('Preserved during work updates');await openInput(frame);await frame.locator('#composer').focus();
  const frameHandle=page.frames().find(f=>f.url().includes('/terminal?'));
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(frame.locator('#composer')).toBeFocused();await expect(frame.locator('#composer')).toHaveValue('Preserved during work updates');
  expect(page.frames()).toContain(frameHandle);
});


test('scheduled follow-ups remain an explicit reviewed choice',async({page})=>{
  const {requests}=await fixture(page);
  await page.goto('/work#session/session-one');
  await page.getByRole('button',{name:'+ Add session',exact:true}).click();
  await page.locator('[name=task]').fill('Check the selected result after completion');
  await page.locator('#schedule-step').check();
  await expect(page.locator('#scheduled-options')).toBeVisible();
  await page.getByRole('button',{name:'Review scheduled step',exact:true}).click();
  await expect(page.getByRole('button',{name:'Preview launch',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Preview launch',exact:true}).click();
  await expect(page.getByText('Native permissions: read-only',{exact:true})).toBeVisible();
  expect(requests).toHaveLength(1);
  await page.getByRole('button',{name:'Accept next step',exact:true}).click();
  await expect(page.getByRole('button',{name:'Open session',exact:true})).toBeVisible();
});

test('one-click recipe checks an uncertain launch using the same receipt',async({page})=>{
  const {sessions}=await fixture(page),requests=[];
  await page.route('**/api/workbench/recipes',route=>route.fulfill({json:[{id:'r',revision:1,title:'Small fix',request:{tool:'shell',profile:'coder',repository:'/tmp/repo',task:'Fix the layout',worktree:true}}]}));
  await page.route('**/api/workbench/launches/preview',route=>route.fulfill({json:{hash:'a'.repeat(64),config:route.request().postDataJSON().request,skills:[]}}));
  await page.route('**/api/workbench/launches',route=>{
    requests.push(route.request().postDataJSON());
    if(requests.length===1)return route.fulfill({json:{state:'uncertain',error:'Launch response pending'}});
    sessions.push({...sessions[0],id:'created',tmux_name:'created-once'});
    return route.fulfill({json:{state:'created',name:'created-once'}});
  });
  await page.goto('/work');await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();
  await page.getByRole('button',{name:'Start session',exact:true}).click();
  await expect(page.locator('#launch-preview')).toContainText('Launch response pending');
  await page.locator('#create-form button[type=submit]').click();
  await expect(page.locator('#session-title')).toHaveText('created-once');
  expect(requests).toHaveLength(2);expect(requests[0]).toEqual(requests[1]);
});

test('closing and switching terminals detaches hidden clients and stop stays accessible',async({page},info)=>{
  const {sessions}=await fixture(page);let opened=0,closed=0,kills=0;
  sessions.push({...sessions[0],id:'other',tmux_name:'session-other'});
  await page.routeWebSocket('**/ws/sessions/**',ws=>{opened++;ws.onClose(()=>{closed++;});ws.send('live\r\n');});
  await page.route('**/api/sessions/session-one/kill',async route=>{kills++;sessions[0].running=false;sessions[0].actions=[];await route.fulfill({json:sessions[0]});});
  await page.goto('/work#session/session-one');await ensureTerminal(page);
  await expect.poll(()=>opened).toBe(1);
  await openInput(page.frameLocator('iframe:not([hidden])'));await page.frameLocator('iframe:not([hidden])').locator('#composer').fill('keep this draft');
  await expect(page.locator('#stop-terminal-session')).toBeVisible();
  await page.locator('#close-terminal').click();await expect.poll(()=>closed).toBe(1);expect(kills).toBe(0);
  await expect(page.locator('#stop-session')).toBeVisible();
  await page.locator('#open-terminal').click();await expect.poll(()=>opened).toBe(2);
  await expect(page.frameLocator('iframe:not([hidden])').locator('#composer')).toHaveValue('keep this draft');
  await page.evaluate(()=>{location.hash='#session/session-other';});await expect(page.locator('#session-title')).toHaveText('session-other');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  await expect.poll(()=>closed).toBe(2);await expect.poll(()=>opened).toBe(3);
  await page.evaluate(()=>{location.hash='#session/session-one';});await expect(page.locator('#session-title')).toHaveText('session-one');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  await expect.poll(()=>closed).toBe(3);await expect.poll(()=>opened).toBe(4);
  page.once('dialog',d=>d.accept());await page.locator('#stop-terminal-session').click();
  await expect.poll(()=>kills).toBe(1);await expect(page.locator('#terminal-panel')).toBeHidden();
  await expect(page.locator('#notice')).toContainText('Stopped session-one');
});

test('type and scroll work together and typing exits history without sending scroll keys',async({page})=>{
  await fixture(page);const inputs=[];
  await page.route('**/api/interface',route=>route.fulfill({json:{label:'Test',terminal_scroll:'tmux'}}));
  await page.routeWebSocket('**/ws/sessions/**',ws=>{ws.onMessage(message=>inputs.push(Buffer.isBuffer(message)?message.toString():message));ws.send('ready\r\n');});
  await page.goto('/work#session/session-one');await ensureTerminal(page);
  const frame=page.frameLocator('iframe:not([hidden])');await expect(frame.locator('#connection')).toHaveText('Connected');
  await expect(frame.locator('[data-mode=type]')).toHaveAttribute('aria-pressed','true');
  // The server history capability arrives independently of the WebSocket.
  await page.waitForTimeout(100);
  await frame.locator('#terminal').dispatchEvent('wheel',{deltaY:-100,deltaMode:0});
  await expect.poll(()=>inputs.some(x=>x.includes('"type":"scroll"')&&x.includes('-5'))).toBe(true);
  await frame.locator('.xterm-helper-textarea').focus();await page.keyboard.type('hello');
  await expect.poll(()=>inputs.filter(x=>!x.startsWith('{')).join('')).toContain('hello');
  const leave=inputs.findIndex(x=>x==='{"type":"scroll","lines":0}'),typed=inputs.findIndex(x=>x==='h');
  expect(leave).toBeGreaterThan(-1);expect(typed).toBeGreaterThan(leave);
  await expect(frame.locator('[data-mode=type]')).toHaveAttribute('aria-pressed','true');
});

test('stop closes the terminal even while an earlier refresh is pending',async({page})=>{
  const {sessions}=await fixture(page);
  const headingActions=page.locator('#session-view > .heading > .session-actions');
  await page.goto('/work#session/session-one');await ensureTerminal(page);
  await expect(page.frameLocator('iframe:not([hidden])').locator('#connection')).toHaveText('Connected');
  await expect(headingActions).toBeHidden();await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(1);
  let pending;const stale=structuredClone(workbenchData(sessions));
  await page.route('**/api/workbench?*',async route=>{pending=route;});
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect.poll(()=>Boolean(pending)).toBe(true);
  await page.route('**/api/sessions/session-one/kill',async route=>{sessions[0].running=false;sessions[0].actions=[];await route.fulfill({json:sessions[0]});});
  page.once('dialog',d=>d.accept());await page.locator('#stop-terminal-session').click();
  await expect(page.locator('#terminal-panel')).toBeHidden();await expect(page.locator('iframe')).toHaveCount(0);
  await expect(headingActions).toBeVisible();await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(1);
  await page.unroute('**/api/workbench?*');await pending.fulfill({json:stale});
  // The stale response still reports the old running session; the terminal must stay closed.
  // The follow-up refresh reports the stopped session, so the heading set remains but its
  // kill action (and the duplicate terminal-header Stop) must not reappear.
  await expect(page.locator('#terminal-panel')).toBeHidden();await expect(headingActions).toBeVisible();
  await expect(page.locator('#open-terminal')).toBeDisabled();
  await expect(page.locator('#stop-terminal-session')).toBeHidden();
  await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(0);
});

test('native mouse scrolling reaches a full-screen app without entering tmux copy mode',async({page},info)=>{
  await fixture(page);const inputs=[];
  await page.route('**/api/interface',route=>route.fulfill({json:{label:'Native mouse',terminal_scroll:'tmux'}}));
  await page.routeWebSocket('**/ws/sessions/**',ws=>{
    ws.onMessage(message=>inputs.push(Buffer.isBuffer(message)?message.toString('latin1'):message));
    ws.send('\x1b[?1049h\x1b[?1000h\x1b[?1006hNative full-screen app\r\n');
  });
  await page.goto('/work#session/session-one');await ensureTerminal(page);
  const frame=page.frameLocator('iframe:not([hidden])');await expect(frame.locator('#connection')).toHaveText('Connected');
  const native=page.frames().find(f=>f.url().includes('/terminal?'));
  await expect.poll(()=>native.evaluate(()=>window.__terminal.modes.mouseTrackingMode)).not.toBe('none');
  const box=await frame.locator('.xterm-screen').boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-120);
  await expect.poll(()=>inputs.some(x=>/^\x1b\[<64;/.test(x))).toBe(true);
  // Trackpads send small pixel deltas: accumulate them through xterm, not one
  // forced tmux line per fractional event.
  await native.evaluate(()=>{const screen=document.querySelector('.xterm-screen'),box=screen.getBoundingClientRect();for(let i=0;i<100;i++)screen.dispatchEvent(new WheelEvent('wheel',{deltaY:-.4,clientX:box.x+30,clientY:box.y+30,bubbles:true,cancelable:true}));});
  expect(inputs.filter(x=>x.startsWith('{')).map(x=>JSON.parse(x)).filter(x=>x.type==='scroll'&&x.lines!==0)).toEqual([]);
  if(info.project.name!=='desktop'){
    const before=inputs.filter(x=>/^\x1b\[<64;/.test(x)).length;
    const cdp=await page.context().newCDPSession(page),x=box.x+box.width/2,y=box.y+40;
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y+120}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp.detach();
    await expect.poll(()=>inputs.filter(x=>/^\x1b\[<64;/.test(x)).length).toBeGreaterThan(before);
  }
  await frame.locator('.xterm-helper-textarea').focus();await page.keyboard.type('hello');
  await expect.poll(()=>inputs.filter(x=>!x.startsWith('{')&&!x.startsWith('\x1b')).join('')).toContain('hello');
  expect(inputs.some(x=>x==='{"type":"scroll","lines":0}')).toBe(true);
});

test('legacy binary mouse reports preserve bytes instead of UTF-8 encoding',async({page})=>{
  await fixture(page);const binary=[];
  await page.routeWebSocket('**/ws/sessions/**',ws=>{ws.onMessage(message=>{if(Buffer.isBuffer(message))binary.push([...message]);});ws.send('\x1b[?1000h');});
  await page.goto('/work#session/session-one');await ensureTerminal(page);
  const native=page.frames().find(f=>f.url().includes('/terminal?'));
  await expect.poll(()=>native.evaluate(()=>window.__terminal?.modes.mouseTrackingMode)).toBe('vt200');
  await native.evaluate(()=>{const screen=document.querySelector('.xterm-screen'),box=screen.getBoundingClientRect();screen.dispatchEvent(new WheelEvent('wheel',{deltaY:-80,clientX:box.x+box.width-20,clientY:box.y+30,bubbles:true,cancelable:true}));});
  await expect.poll(()=>binary.some(bytes=>bytes[0]===27&&bytes[1]===91&&bytes[2]===77)).toBe(true);
});


test('optional input reclaims output space and retains an unsent task across collapse and reload',async({page})=>{
  await fixture(page);const sent=[];
  await page.route('**/api/sessions/session-one/brief?session_id=root',route=>route.fulfill({json:{brief:'Review this task before sending'}}));
  await page.routeWebSocket('**/ws/sessions/**',ws=>ws.onMessage(value=>sent.push(value)));
  await page.goto('/terminal?session=session-one');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await expect(page.locator('#toggle-composer')).toHaveText('Input · draft');
  await expect(page.locator('#input-drawer')).toBeHidden();
  await expect(page.locator('#paste-device')).toBeHidden();
  await expect(page.locator('#send-enter')).toBeHidden();
  const closedHeight=(await page.locator('.terminal-frame').boundingBox()).height;
  await openInput(page);await expect(page.locator('#composer')).toHaveValue('Review this task before sending');
  const openHeight=(await page.locator('.terminal-frame').boundingBox()).height;
  expect(closedHeight-openHeight).toBeGreaterThan(55);
  await page.locator('#composer').fill('Keep this unsent draft');
  await page.locator('#toggle-composer').click();
  await expect(page.locator('#input-drawer')).toBeHidden();
  await page.reload();await expect(page.locator('#input-drawer')).toBeHidden();
  await expect(page.locator('#toggle-composer')).toHaveText('Input · draft');
  await openInput(page);await expect(page.locator('#composer')).toHaveValue('Keep this unsent draft');
  expect(sent.some(value=>typeof value!=='string'||!value.startsWith('{'))).toBe(false);
  await page.locator('#toggle-composer').click();
  const beforeMenu=(await page.locator('.terminal-frame').boundingBox()).height;
  await openMore(page);expect((await page.locator('.terminal-frame').boundingBox()).height).toBe(beforeMenu);
  await page.keyboard.press('Escape');await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
});

test('collapsible branches and full-screen tree navigate every layer without losing drafts',async({page},info)=>{
  const {sessions}=await fixture(page);
  sessions.push({...sessions[0],id:'child',tmux_name:'session-child',parent_session_id:'root'});
  sessions.push({...sessions[0],id:'leaf',tmux_name:'session-leaf',parent_session_id:'child'});
  await page.goto('/work#session/session-one');
  const branch=page.getByRole('button',{name:'Toggle children of session-one',exact:true});
  await branch.click();await expect(page.locator('#session-tree')).not.toContainText('session-child');
  await expect(branch).toBeFocused();
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#session-tree')).not.toContainText('session-child');
  const before=(await page.locator('.session-main').boundingBox()).width;
  await page.locator('#tree-panel > summary').click();
  if(info.project.name==='desktop')expect((await page.locator('.session-main').boundingBox()).width-before).toBeGreaterThan(100);
  await ensureTerminal(page);
  const frame=page.frameLocator('iframe:not([hidden])');await openInput(frame);await frame.locator('#composer').fill('Root draft');
  await frame.locator('#toggle-composer').click();
  if(info.project.name==='desktop')await page.locator('#expand-terminal').click();
  await page.locator('#terminal-sessions').click();await expect(page.locator('#sessions-dialog')).toBeVisible();
  await branch.click();await page.locator('#sessions-dialog').getByRole('link',{name:'session-leaf',exact:true}).click();
  await expect(page.locator('#sessions-dialog')).toBeHidden();
  await expect(page.locator('#session-title')).toHaveText('session-leaf');
  await expect(page.frameLocator('iframe:not([hidden])').locator('#connection')).toHaveText('Connected');
  await page.locator('#terminal-sessions').click();
  await page.locator('#sessions-dialog .node.active .add-session').click();
  await expect(page.locator('#create-dialog')).toBeVisible();
  await expect(page.locator('#create-form [name=parent]')).toHaveValue('leaf');
  await expect(page.locator('#schedule-step')).not.toBeChecked();await page.locator('#cancel-create').click();
  await expect(page.locator('#sessions-dialog')).toBeVisible();
  await page.locator('#sessions-dialog').getByRole('link',{name:'session-one',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-one');
  await openInput(page.frameLocator('iframe:not([hidden])'));
  await expect(page.frameLocator('iframe:not([hidden])').locator('#composer')).toHaveValue('Root draft');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});


test('failed direct input exposes the fallback without erasing its saved draft',async({page})=>{
  await fixture(page);let socket;
  await page.routeWebSocket('**/ws/sessions/**',ws=>{socket=ws;});
  await page.goto('/terminal?session=session-one');await expect(page.locator('#connection')).toHaveText('Connected');
  await openInput(page);await page.locator('#composer').fill('Recoverable draft');await page.locator('#toggle-composer').click();
  socket.close({code:4000,reason:'Detached'});
  await expect(page.locator('#reconnect')).toBeVisible();
  await page.locator('.xterm-helper-textarea').focus();await page.keyboard.type('x');
  await expect(page.locator('#input-drawer')).toBeVisible();
  await expect(page.locator('#composer')).toHaveValue('Recoverable draft');
  await expect(page.locator('#connection')).toHaveText('Terminal is disconnected');
});


test('short desktop session keeps terminal and details within the viewport', async ({page}, info) => {
  test.skip(info.project.name !== 'desktop');
  await page.setViewportSize({width:1024,height:600});
  const {sessions}=await fixture(page);
  sessions[0].tmux_name='session-'+'long-name-'.repeat(18);
  sessions[0].repository='/workspace/'+'long-path/'.repeat(20);
  for(let i=0;i<25;i++)sessions.push({...sessions[0],id:`child-${i}`,tmux_name:`child-${i}`,parent_session_id:'root'});
  await page.goto('/work#session/'+encodeURIComponent(sessions[0].tmux_name));
  const frame=page.frameLocator('iframe:not([hidden])');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  const box=await page.locator('#terminal-panel').boundingBox();
  expect(box.y+box.height).toBeLessThanOrEqual(600);
  expect(box.height).toBeGreaterThan(200);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1024);
  const stop=await page.locator('#stop-terminal-session').boundingBox();
  expect(stop.x+stop.width).toBeLessThanOrEqual(1024);
  await page.screenshot({path:info.outputPath('short-window-after.png')});
  expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThanOrEqual(600);
  await page.locator('#session-detail > summary').click();
  await page.locator('#show-output').click();
  expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThanOrEqual(600);
  await frame.locator('#terminal').dispatchEvent('wheel',{deltaY:500});
  expect(await page.evaluate(()=>window.scrollY)).toBe(0);
  await page.setViewportSize({width:800,height:500});
  await expect.poll(async()=>{const box=await page.locator('#terminal-panel').boundingBox();return box.y+box.height;}).toBeLessThanOrEqual(500);
  await page.locator('#session-detail > summary').click();
  await page.locator('#expand-terminal').click();
  await expect(page.locator('#terminal-panel')).toHaveClass(/expanded/);
  await page.locator('#expand-terminal').click();
  await page.locator('#close-terminal').click();
  await expect(page.locator('#terminal-panel')).toBeHidden();
  await page.locator('#open-terminal').click();
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await page.evaluate(()=>{location.hash='#work';});
  await expect(page.locator('body')).not.toHaveClass(/session-terminal/);
});

test('embedded terminal delegates full screen to its containing view', async ({page}) => {
  await fixture(page);
  await page.goto('/work#session/session-one');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  const frame=page.frameLocator('iframe:not([hidden])');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await openMore(frame);
  await expect(frame.locator('#fullscreen')).toBeHidden();
});

test('one lifecycle action set is available while the terminal is open and restored when closed',async({page})=>{
  const {sessions}=await fixture(page);
  sessions.push({...sessions[0],id:'other',tmux_name:'session-other'});
  sessions.push({...sessions[0],id:'stopped',tmux_name:'session-stopped',running:false,actions:[]});
  const headingActions=page.locator('#session-view > .heading > .session-actions');
  await page.goto('/work#session/session-one');
  // Desktop opens the terminal on route; mobile starts closed. Normalise to an open terminal.
  await ensureTerminal(page);
  await expect(headingActions).toBeHidden();
  await expect(page.locator('#open-terminal')).toBeHidden();
  await expect(page.locator('#stop-session')).toBeHidden();
  await expect(page.locator('#stop-terminal-session')).toBeVisible();
  await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(1);
  // Close restores the heading set and a completed refresh must keep the terminal closed.
  await page.locator('#close-terminal').click();
  await expect(page.locator('#terminal-panel')).toBeHidden();
  const refreshed=page.waitForResponse(response=>response.url().includes('/api/workbench')&&response.ok());
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await refreshed;
  await expect(page.locator('#refresh')).toBeEnabled();
  await expect(page.locator('#terminal-panel')).toBeHidden();
  await expect(headingActions).toBeVisible();
  await expect(page.locator('#open-terminal')).toBeVisible();
  await expect(page.locator('#stop-session')).toBeVisible();
  await expect(page.locator('#stop-terminal-session')).toBeHidden();
  await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(1);
  await page.locator('#open-terminal').click();
  await expect(headingActions).toBeHidden();
  await page.evaluate(()=>{location.hash='#session/session-other';});
  await expect(page.locator('#session-title')).toHaveText('session-other');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  await expect(page.locator('#terminal-panel')).toBeVisible();
  await expect(headingActions).toBeHidden();
  await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(1);
  await page.evaluate(()=>{location.hash='#session/session-stopped';});
  await expect(page.locator('#session-title')).toHaveText('session-stopped');
  await expect(page.locator('#terminal-panel')).toBeHidden();
  await expect(headingActions).toBeVisible();
  await expect(page.locator('#open-terminal')).toBeDisabled();
  await expect(page.locator('#stop-session')).toBeHidden();
  await expect(page.getByRole('button',{name:'Stop session',exact:true})).toHaveCount(0);
});

test('phone terminal header keeps one row of usable lifecycle controls',async({page},info)=>{
  test.skip(info.project.name==='desktop');
  await fixture(page);
  await page.goto('/work#session/session-one');
  await ensureTerminal(page);
  const assertRow=async()=>{
    const viewport=page.viewportSize();
    const header=await page.locator('#terminal-panel > header').boundingBox();
    const boxes=await Promise.all(['#terminal-sessions','#stop-terminal-session','#close-terminal'].map(selector=>page.locator(selector).boundingBox()));
    const centers=boxes.map(box=>box.y+box.height/2);
    expect(Math.max(...centers)-Math.min(...centers)).toBeLessThanOrEqual(6);
    expect(header.height).toBeLessThanOrEqual(60);
    for(const box of boxes){
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x+box.width).toBeLessThanOrEqual(viewport.width+0.5);
      expect(box.y+box.height).toBeLessThanOrEqual(viewport.height+0.5);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    expect(await page.evaluate(()=>window.scrollY)).toBe(0);
  };
  const status=page.locator('#terminal-status');
  await expect(status).toBeAttached();
  expect((await status.textContent()).trim().length).toBeGreaterThan(0);
  expect(await status.evaluate(el=>getComputedStyle(el).display)).not.toBe('none');
  await expect(page.locator('#expand-terminal')).toBeHidden();
  await expect(page.locator('#terminal-connection-label')).toBeVisible();
  await expect(page.locator('#terminal-connection-label')).toHaveText('Connected');
  await page.frameLocator('iframe:not([hidden])').locator('body').evaluate(()=>parent.postMessage({type:'agent-console:terminal-status',status:'Disconnected'},location.origin));
  await expect(page.locator('#terminal-connection-label')).toHaveText('Disconnected');
  await assertRow();
  // Keyboard and orientation changes shrink or reflow the phone viewport.
  for(const size of [{width:360,height:420},{width:700,height:360},{width:390,height:844}]){
    await page.setViewportSize(size);
    await expect(page.locator('#terminal-panel')).toBeVisible();
    await assertRow();
  }
});

test('desktop terminal controls fit normal and 200% zoom equivalent viewports',async({page},info)=>{
  test.skip(info.project.name!=='desktop');
  await fixture(page);
  await page.goto('/work#session/session-one');
  await ensureTerminal(page);
  const fits=async(width,height)=>{
    const boxes=await Promise.all(['#terminal-sessions','#stop-terminal-session','#close-terminal'].map(selector=>page.locator(selector).boundingBox()));
    for(const box of boxes){
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x+box.width).toBeLessThanOrEqual(width+0.5);
      expect(box.y+box.height).toBeLessThanOrEqual(height+0.5);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  };
  await expect(page.locator('#terminal-status')).toBeVisible();
  await expect(page.locator('#expand-terminal')).toBeVisible();
  await fits(1280,800);
  const panel=await page.locator('#terminal-panel').boundingBox();
  expect(panel.y+panel.height).toBeLessThanOrEqual(800);
  await page.setViewportSize({width:640,height:400});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(640);
  await expect(page.locator('#terminal-panel')).toBeVisible();
  await fits(640,400);
  expect(await page.evaluate(()=>window.scrollY)).toBe(0);
});

test('refresh notice clears after recovery and preserves unrelated notices',async({page},info)=>{
  const {sessions}=await fixture(page);
  let fail=true;
  const routePromise=page.route('**/api/workbench?*',route=>{
    if(fail){return route.fulfill({status:503,json:{detail:'Temporary failure'}});}
    return route.fulfill({json:workbenchData(sessions)});
  });
  await routePromise;
  await page.goto('/work');
  await expect(page.locator('#notice')).toContainText('Could not refresh');
  fail=false;
  if(info.project.name==='desktop')await page.locator('#refresh').click();
  else await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#notice')).toBeHidden();
  await page.evaluate(()=>{
    const notice=document.querySelector('#notice');
    notice.dataset.kind='general';
    notice.textContent='Action notice';
    notice.hidden=false;
  });
  await expect(page.locator('#notice')).toContainText('Action notice');
  fail=false;
  if(info.project.name==='desktop')await page.locator('#refresh').click();
  else await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#notice')).toContainText('Action notice');
});

test('stopped history is bounded and hide restore preserves a running child',async({page})=>{
  const {sessions}=await fixture(page);
  sessions[0].running=false;sessions[0].actions=[];
  sessions.push({...sessions[0],id:'live-child',tmux_name:'live-child',parent_session_id:'root',running:true,actions:['attach','kill']});
  for(let i=0;i<45;i++)sessions.push({...sessions[0],id:'old-'+i,tmux_name:'old-'+i});
  await page.route('**/api/workbench/sessions/*/visibility',route=>{
    expect(route.request().method()).toBe('PATCH');const id=new URL(route.request().url()).pathname.split('/').at(-2),session=sessions.find(s=>s.id===id);
    session.hidden=route.request().postDataJSON().hidden;return route.fulfill({json:{id,hidden:session.hidden}});
  });
  await page.goto('/work');
  await expect(page.locator('#work-list .card')).toHaveCount(1);
  await expect(page.locator('#work-history > summary')).toContainText('45 work groups');
  await page.locator('[data-node=root]').getByRole('button',{name:'Hide',exact:true}).click();
  await expect(page.locator('[data-node=root]')).toContainText('Hidden session');
  await expect(page.locator('[data-node=root]')).toContainText('child running');
  await expect(page.locator('[data-node=root]').getByRole('link',{name:'Open work',exact:true})).toHaveAttribute('href','#session/session-one');
  await page.locator('#work-history > summary').click();
  await expect(page.locator('#work-history .card')).toHaveCount(20);
  await page.getByRole('button',{name:/Show more history/}).click();
  await expect(page.locator('#work-history .card')).toHaveCount(40);
  await page.locator('#search').fill('old-44');await expect(page.locator('#work-list .card')).toHaveCount(1);
  await page.locator('[data-node=old-44]').getByRole('button',{name:'Hide',exact:true}).click();
  await expect(page.locator('#work-list .card')).toHaveCount(0);
  await page.locator('#include-hidden').check();await expect(page.locator('[data-node=old-44]')).toBeVisible();
  await page.locator('[data-node=old-44]').getByRole('button',{name:'Restore',exact:true}).click();
  await page.locator('#include-hidden').uncheck();await expect(page.locator('[data-node=old-44]')).toBeVisible();
  await page.reload();await expect(page.locator('[data-node=old-44]')).toBeVisible();
  expect(sessions).toHaveLength(47);expect(sessions.find(s=>s.id==='live-child').running).toBe(true);
});

test('child task search has direct links and unchanged refresh keeps cards',async({page})=>{
  const {sessions}=await fixture(page);sessions.push({...sessions[0],id:'child',tmux_name:'different-child',parent_session_id:'root',initial_task:'Prefix '.repeat(100)+'UniqueTailKeyword'}, {...sessions[0],id:'unrelated',tmux_name:'unrelated-work'});
  await page.route('**/api/workbench?*',route=>{
    const data=workbenchData(sessions);data.sessions=data.sessions.map(({initial_task,...summary})=>summary);
    return route.fulfill({json:data});
  });
  await page.goto('/work');
  await expect(page.locator('#work-list [data-node=root]')).toBeVisible();
  await page.evaluate(()=>{window.savedCard=document.querySelector('#work-list [data-node=root]');});
  sessions.find(s=>s.id==='unrelated').initial_task='Changed unrelated task';
  await expect(page.locator('#refresh')).toBeEnabled();
  const response=page.waitForResponse(r=>r.url().includes('/api/workbench?')&&r.ok());
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await response;await expect(page.locator('#refresh')).toBeEnabled();
  await expect.poll(()=>page.evaluate(()=>window.savedCard===document.querySelector('#work-list [data-node=root]'))).toBe(true);
  await page.locator('#search').fill('UniqueTailKeyword');
  await expect(page.locator('.matched-children')).toContainText('1 matching child session');
  await expect(page.locator('.matched-children a')).toHaveAttribute('href','#session/different-child');
  await page.locator('.matched-children a').click();await expect(page.locator('#session-title')).toHaveText('different-child');
  await page.locator('#session-detail > summary').click();await expect(page.locator('#session-brief')).toContainText('UniqueTailKeyword');
});

test('mark reviewed preserves failed outcome and blocked attention has no shortcut',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;sessions[0].actions=[];sessions[0].attention_state='ready_for_review';
  sessions[0].result={id:'failure',session_id:'root',kind:'final',outcome:'fail',summary:'Actual failed check',created_at:'2026-10-02T00:00:00Z',artifacts:[]};
  await page.route('**/api/sessions/session-one/attention',route=>{expect(route.request().postDataJSON().state).toBe('normal');sessions[0].attention_state='normal';sessions[0].reviewed=true;return route.fulfill({json:sessions[0]});});
  await page.goto('/work');await page.locator('#work-history > summary').click();
  await page.getByRole('button',{name:'Mark reviewed',exact:true}).click();
  await expect(page.locator('#work-list')).toContainText('Result: failed');
  await expect(page.locator('#work-list').getByRole('button',{name:'Mark reviewed',exact:true})).toHaveCount(0);
  expect(sessions[0].result.outcome).toBe('fail');
  sessions[0].attention_state='blocked';await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#work-list')).toContainText('Attention: Blocked');
  await expect(page.locator('#work-list').getByRole('button',{name:'Mark reviewed',exact:true})).toHaveCount(0);
});


test('Add session stays visible without shifting the tree on hover or focus',async({page})=>{
  const {sessions}=await fixture(page);
  sessions.push({...sessions[0],id:'child',tmux_name:'session-child',parent_session_id:'root'});
  await page.goto('/work#session/session-one');
  await page.mouse.move(0,0);
  const row=page.locator('#session-tree .node[data-node="child"]');
  const add=row.getByRole('button',{name:'+ Add session',exact:true});
  await expect(add).toBeVisible();
  const before=await row.boundingBox();
  expect((await add.boundingBox()).height).toBeGreaterThanOrEqual(44);
  await row.hover();
  expect(await row.boundingBox()).toEqual(before);
  await page.mouse.move(0,0);await add.focus();
  await expect(add).toBeFocused();
  expect(await row.boundingBox()).toEqual(before);
  await page.keyboard.press('Enter');
  await expect(page.locator('#create-dialog')).toBeVisible();
  await expect(page.locator('#create-dialog')).toContainText('session-child');
});


test('default order ignores polling activity and attention, and Open work selects the root',async({page})=>{
  const {sessions}=await fixture(page);
  sessions[0].created_at='2026-10-01T00:00:00Z';
  sessions.push({...sessions[0],id:'older',tmux_name:'older-work',created_at:'2026-09-01T00:00:00Z'},
    {...sessions[0],id:'child',tmux_name:'attention-child',parent_session_id:'root',attention_state:'blocked'});
  await page.goto('/work');
  const cards=page.locator('#work-list .card');
  await expect(cards).toHaveCount(2);
  const order=await cards.evaluateAll(nodes=>nodes.map(n=>n.dataset.node));
  sessions.find(s=>s.id==='older').last_activity='2099-01-01T00:00:00Z';
  sessions.find(s=>s.id==='older').attention_state='needs_input';sessions.reverse();
  await expect(page.locator('#refresh')).toBeEnabled();
  const response=page.waitForResponse(r=>r.url().includes('/api/workbench?')&&r.ok());
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await response;await expect(page.locator('#refresh')).toBeEnabled();
  await expect.poll(()=>cards.evaluateAll(nodes=>nodes.map(n=>n.dataset.node))).toEqual(order);
  await page.locator('#work-list [data-node=root]').getByRole('link',{name:'Open work',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-one');
  await page.locator('#session-tree [data-node=child] a').click();
  await expect(page.locator('#session-title')).toHaveText('attention-child');
});

test('tree polling retains Add session identity, focus, position and scroll',async({page},info)=>{
  const {sessions}=await fixture(page);
  for(let i=0;i<18;i++)sessions.push({...sessions[0],id:'child-'+String(i).padStart(2,'0'),tmux_name:'session-child-'+i,parent_session_id:'root',created_at:'2026-10-02T00:00:00Z'});
  await page.goto('/work#session/session-one');
  if(info.project.name!=='desktop'){
    await page.locator('#open-terminal').click();await page.locator('#terminal-sessions').click();
  }
  const add=page.locator('#session-tree [data-node=child-08] .add-session');
  await add.focus();await expect(add).toBeFocused();
  const before=await add.boundingBox();
  const scroll=await page.evaluate(()=>{window.savedAdd=document.querySelector('#session-tree [data-node=child-08] .add-session');return [window.scrollY,...['tree-panel','session-tree','sessions-dialog','tree-drawer'].map(id=>document.getElementById(id).scrollTop)];});
  await page.mouse.move(before.x+before.width/2,before.y+before.height/2);await page.mouse.down();
  sessions.reverse();sessions.find(s=>s.id==='child-08').attention_state='blocked';
  sessions.find(s=>s.id==='child-17').last_activity='2099-01-01T00:00:00Z';
  await expect(page.locator('#refresh')).toBeEnabled();
  const response=page.waitForResponse(r=>r.url().includes('/api/workbench?')&&r.ok());
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await response;await expect(page.locator('#refresh')).toBeEnabled();
  await expect(page.locator('#session-state')).toHaveText('Working');
  await expect(add).toBeFocused();
  expect(await page.evaluate(()=>window.savedAdd===document.querySelector('#session-tree [data-node=child-08] .add-session'))).toBe(true);
  expect(await add.boundingBox()).toEqual(before);
  expect(await page.evaluate(()=>[window.scrollY,...['tree-panel','session-tree','sessions-dialog','tree-drawer'].map(id=>document.getElementById(id).scrollTop)])).toEqual(scroll);
  await page.mouse.up();await expect(page.locator('#create-help')).toContainText('session-child-8');
  await page.locator('#cancel-create').click();await expect(add).toBeFocused();
  if(info.project.name!=='desktop')await expect(page.locator('#sessions-dialog')).toBeVisible();
  expect(await add.boundingBox()).toEqual(before);
});


test('explicit terminal open focuses Type without polling stealing focus or overriding Scroll and Select',async({page})=>{
  const {sessions}=await fixture(page);sessions.push({...sessions[0],id:'child',tmux_name:'focus-child',parent_session_id:'root'});
  await page.goto('/work#session/session-one');await ensureTerminal(page);
  const frame=page.frameLocator('iframe:not([hidden])');
  await expect(frame.locator('.xterm-helper-textarea')).toBeFocused();
  await openMore(frame);
  await frame.locator('[data-mode=scroll]').click();
  await page.locator('#close-terminal').click();await page.locator('#open-terminal').click();
  await expect(frame.locator('[data-mode=scroll]')).toHaveAttribute('aria-pressed','true');
  await expect(frame.locator('.xterm-helper-textarea')).not.toBeFocused();
  await openMore(frame);await frame.locator('[data-mode=select]').click();
  await page.locator('#close-terminal').click();await page.locator('#open-terminal').click();
  await expect(frame.locator('[data-mode=select]')).toHaveAttribute('aria-pressed','true');
  await expect(frame.locator('.xterm-helper-textarea')).not.toBeFocused();
  await page.locator('#terminal-sessions').click();
  const add=page.locator('#session-tree [data-node=root] .add-session');await add.focus();
  await expect(page.locator('#refresh')).toBeEnabled();
  const response=page.waitForResponse(r=>r.url().includes('/api/workbench?')&&r.ok());
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await response;await expect(page.locator('#refresh')).toBeEnabled();
  await expect(add).toBeFocused();
});


test('root entry and explicit historical attempt links retain logical aliases',async({page})=>{
  const {sessions}=await fixture(page);
  sessions[0].running=false;sessions[0].actions=[];
  sessions.push({...sessions[0],id:'old-attempt',tmux_name:'old-attempt',parent_session_id:'root'},
    {...sessions[0],id:'current-attempt',tmux_name:'current-attempt',parent_session_id:'root',running:true,attention_state:'needs_input'});
  const step={id:'logical-step',owner_id:'root',root_id:'root',task:'Logical task',config:{tool:'shell',profile:'coder'},decision:'accepted',attempts:[
    {id:'attempt-1',session_id:'old-attempt',name:'old-attempt',generation:1,state:'completed'},
    {id:'attempt-2',session_id:'current-attempt',name:'current-attempt',generation:2,state:'running'}]};
  await page.route('**/api/workbench?*',route=>route.fulfill({json:workbenchData(sessions,[step])}));
  await page.goto('/work');
  await page.locator('#work-list [data-node=root]').getByRole('link',{name:'Open work',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-one');
  const row=page.locator('#session-tree [data-node=logical-step]');
  await expect(row.locator('.node-heading a')).toHaveAttribute('href','#session/current-attempt');
  await row.locator('summary').click();await row.getByRole('link',{name:'Attempt 1 · completed',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('old-attempt');
  await expect(page.locator('#session-statuses')).toContainText('Historical attempt 1');
  await expect(row.locator('.node-heading a')).toHaveAttribute('aria-current','page');
  await row.locator('.node-heading a').click();await expect(page.locator('#session-title')).toHaveText('current-attempt');
});

test('mobile work filters keep Refresh visible and keyboard usable at narrow widths',async({page})=>{
  await fixture(page);await page.goto('/work');
  const refresh=page.getByRole('button',{name:'Refresh',exact:true});
  for(const width of [320,360,390]){
    await page.setViewportSize({width,height:844});
    await expect(refresh).toBeVisible();
    await expect(refresh).toHaveAttribute('data-icon','refresh');
    const box=await refresh.boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(44);expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
    const filter=await page.locator('#state-filter').boundingBox();
    expect(filter.x+filter.width).toBeLessThanOrEqual(box.x);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await refresh.focus();await expect(refresh).toBeFocused();
    const reloaded=page.waitForResponse(response=>response.url().includes('/api/workbench?')&&response.ok());
    await page.keyboard.press('Enter');await reloaded;await expect(refresh).toBeEnabled();
  }
});

for(const endpoint of ['me','interface'])test(`audit recovery retries failed ${endpoint} bootstrap on Refresh`,async({page})=>{
  await fixture(page);let attempts=0;
  await page.route(`**/api/${endpoint}`,async route=>{
    if(++attempts===1)return route.fulfill({status:503,json:{detail:'Bootstrap temporarily unavailable'}});
    return route.fallback();
  });
  await page.goto('/work');await expect(page.locator('#notice')).toContainText('Could not load settings');
  await page.locator('#new-session').click();await expect(page.locator('#create-dialog')).not.toBeVisible();
  await page.locator('#refresh').click();await expect(page.locator('#notice')).toBeHidden();
  await page.locator('#new-session').click();await expect(page.locator('#create-dialog')).toBeVisible();
  await expect(page.locator('[name=profile]')).toHaveValue('coder');expect(attempts).toBe(2);
});
for(const opened of [false,true])test(`audit older result becomes release candidate with panel initially ${opened?'open':'closed'}`,async({page})=>{
  await fixture(page);const evidence=[];
  const base={session_id:'root',kind:'final',outcome:'pass',summary:'Recent result',checks:['Test passed'],artifacts:[],created_at:'2026-10-02'};
  const older={...base,id:'older-candidate',version:1,summary:'Older deployable result',artifacts:[{kind:'commit',sha:'a'.repeat(40),hash:'b'.repeat(64),label:'Candidate'}]};
  await page.route('**/api/sessions/root/results**',route=>route.fulfill({json:{results:new URL(route.request().url()).searchParams.has('before')?[older]:Array.from({length:5},(_,i)=>({...base,id:`recent-${i}`,version:6-i}))}}));
  await page.route('**/api/workflow/release-targets',route=>route.fulfill({json:[{id:'stage',label:'Stage',actions:['deploy']}]}));
  await page.route('**/api/sessions/root/releases',route=>route.fulfill({json:[]}));
  await page.route('**/api/workflow/releases/evidence/*',route=>{evidence.push(route.request().url());return route.fulfill({json:[{...older,eligible:true}]});});
  await page.goto('/work#results/session-one');
  if(opened){await page.locator('#workflow-releases > summary').click();await expect(page.locator('#workflow-releases')).toContainText('Publish a passing final result');}
  await page.getByRole('button',{name:'Older results',exact:true}).click();
  if(!opened)await page.locator('#workflow-releases > summary').click();
  await expect(page.getByRole('combobox',{name:'Candidate',exact:true})).toHaveValue('older-candidate');
  await expect(page.locator('#workflow-releases')).toContainText('Older deployable result');
  await expect.poll(()=>evidence.length).toBeGreaterThan(0);expect(evidence.at(-1)).toContain('/older-candidate');
});
test('audit skill actions report structured failure diagnostics',async({page})=>{
  await fixture(page);
  await page.route('**/api/skill-registry',route=>route.fulfill({json:{entries:[],imports:[]}}));
  await page.route('**/api/skills/sync',route=>route.fulfill({json:{ok:false,problems:['Managed path collision'],skipped:[{tool:'shell',reason:'Unknown version'}],warnings:['Review discovery']}}));
  await page.route('**/api/skills/doctor',route=>route.fulfill({json:{ok:false,problems:['Missing required skill'],skipped:[],warnings:[]}}));
  await page.goto('/work#skills');await page.getByRole('button',{name:'Sync unrestricted skills'}).click();
  await expect(page.locator('#notice')).toContainText('Sync needs attention: Managed path collision; shell: Unknown version; Review discovery');
  await expect(page.locator('#notice')).not.toContainText('Skills updated');
  await page.getByRole('button',{name:'Validate discovery'}).click();
  await expect(page.locator('#notice')).toHaveText('Doctor: Missing required skill');
});

for(const children of [0,4])test(`Settings explains configured child capacity ${children}`,async({page})=>{
  await fixture(page);
  await page.route('**/api/me',route=>route.fulfill({json:{profiles:[],tool_status:[],auth_contexts:[],session_limits:{managed:24,children}}}));
  await page.goto('/work#settings');
  await expect(page.locator('#session-capacity')).toContainText('24 running or reserved sessions in total');
  await expect(page.locator('#session-capacity')).toContainText(children?'4 active children per parent':'No per-parent child limit');
  await expect(page.locator('#session-capacity')).toContainText('Stopped and archived sessions do not use a slot');
});

// Remaining-audit regressions: drafts and asynchronous session inspection.
test('remaining audit keeps root and child drafts separate across close and Escape',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;
  sessions.push({...sessions[0],id:'second-root',tmux_name:'second-root'});
  await page.goto('/work');await page.locator('#new-session').click();
  await page.locator('[name=task]').fill('Root draft');await page.getByText('Model & session options',{exact:true}).click();await page.locator('[name=model]').fill('saved-model');
  await page.locator('[name=worktree]').uncheck();await page.keyboard.press('Escape');
  await expect(page.locator('#create-dialog')).toBeHidden();await page.locator('#new-session').click();
  await expect(page.locator('[name=task]')).toHaveValue('Root draft');await expect(page.locator('[name=model]')).toHaveValue('saved-model');await expect(page.locator('[name=worktree]')).not.toBeChecked();
  await page.locator('#cancel-create').click();
  for(const [name,id,task] of [['session-one','root','First child draft'],['second-root','second-root','Second child draft']]){
    await page.evaluate(name=>location.hash='#session/'+name,name);await expect(page.locator('#session-title')).toHaveText(name);
    await page.locator(`#session-tree [data-node="${id}"] .add-session`).click();await expect(page.locator('[name=task]')).toHaveValue('');
    await page.locator('[name=task]').fill(task);await page.locator('#cancel-create').click();
  }
  await page.evaluate(()=>location.hash='#session/session-one');await expect(page.locator('#session-title')).toHaveText('session-one');
  await page.locator('#session-tree [data-node=root] .add-session').click();await expect(page.locator('[name=task]')).toHaveValue('First child draft');
  await page.locator('#schedule-step').check();await page.getByText('Purpose & output',{exact:true}).click();await page.locator('[name=reason]').fill('Wait for this result');await page.locator('[name=readiness]').selectOption('after-ready');await page.keyboard.press('Escape');
  await expect(page.locator('#create-dialog')).toBeHidden();await page.locator('#session-tree [data-node=root] .add-session').click();
  await expect(page.locator('#schedule-step')).toBeChecked();await expect(page.locator('[name=reason]')).toHaveValue('Wait for this result');await expect(page.locator('[name=readiness]')).toHaveValue('after-ready');
  await page.locator('#cancel-create').click();await page.evaluate(()=>location.hash='#work');await page.locator('#new-session').click();await expect(page.locator('[name=task]')).toHaveValue('Root draft');
  page.once('dialog',dialog=>dialog.accept());await page.locator('#discard-create').click();await expect(page.locator('[name=task]')).toHaveValue('');await expect(page.locator('[name=model]')).toHaveValue('');
});

test('remaining audit recipe drafts do not overwrite root drafts',async({page})=>{
  await fixture(page);const recipe={id:'saved',revision:1,title:'Saved recipe',request:{tool:'shell',profile:'coder',task:'Recipe task',repository:'/tmp/recipe',worktree:false,auth_context:'default'}};
  await page.route('**/api/workbench/recipes',route=>route.fulfill({json:[recipe]}));
  await page.goto('/work');await page.locator('#new-session').click();await page.locator('[name=task]').fill('Root task');await page.locator('#cancel-create').click();
  await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await expect(page.locator('[name=task]')).toHaveValue('Recipe task');await page.locator('[name=task]').fill('Edited recipe draft');await page.locator('#cancel-create').click();
  await page.locator('#new-session').click();await expect(page.locator('[name=task]')).toHaveValue('Root task');await page.locator('#cancel-create').click();
  recipe.revision++;recipe.request.task='Externally revised recipe';await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await expect(page.locator('[name=task]')).toHaveValue('Edited recipe draft');await expect(page.getByRole('button',{name:'Start session',exact:true})).toBeVisible();
});

test('remaining audit completed creation clears only its submitted draft',async({page})=>{
  const {sessions}=await fixture(page);let pending;
  await page.route('**/api/sessions',route=>{if(route.request().method()==='POST'){pending=route;return;}return route.fallback();});
  await page.goto('/work');await page.locator('#new-session').click();await page.locator('[name=task]').fill('Submitted draft');await page.getByRole('button',{name:'Create session',exact:true}).click();await expect.poll(()=>!!pending).toBe(true);
  await page.locator('#cancel-create').click();await page.locator('#new-session').click();await page.locator('[name=task]').fill('Newer unsent draft');
  const created={...sessions[0],id:'created',tmux_name:'created',initial_task:'Submitted draft'};sessions.push(created);await pending.fulfill({json:created});
  await expect(page.locator('#notice')).toContainText('Created created');await expect(page.locator('[name=task]')).toHaveValue('Newer unsent draft');await page.locator('#cancel-create').click();await page.locator('#new-session').click();await expect(page.locator('[name=task]')).toHaveValue('Newer unsent draft');
  await page.getByRole('button',{name:'Create session',exact:true}).click();await expect.poll(()=>pending.request().postDataJSON().task).toBe('Newer unsent draft');
  const next={...created,id:'created-next',tmux_name:'created-next'};sessions.push(next);await pending.fulfill({json:next});await expect(page.locator('#session-title')).toHaveText('created-next');
  if(await page.locator('#terminal-panel').isVisible())await page.locator('#close-terminal').click();await page.evaluate(()=>location.hash='#work');await page.locator('#new-session').click();await expect(page.locator('[name=task]')).toHaveValue('');
});

test('remaining audit reopened uncertain recipe launch reuses its request key',async({page})=>{
  const {sessions}=await fixture(page);const requests=[];
  await page.route('**/api/workbench/recipes',route=>route.fulfill({json:[{id:'retry',revision:1,title:'Retry recipe',request:{tool:'shell',profile:'coder',task:'Keep one launch'}}]}));
  await page.route('**/api/workbench/launches/preview',route=>route.fulfill({json:{hash:'f'.repeat(64),config:route.request().postDataJSON().request,skills:[]}}));
  await page.route('**/api/workbench/launches',route=>{
    requests.push(route.request().postDataJSON());
    if(requests.length===1)return route.fulfill({status:503,json:{detail:'Response interrupted'}});
    sessions.push({...sessions[0],id:'one-launch',tmux_name:'one-launch'});return route.fulfill({json:{state:'created',session_id:'one-launch',name:'one-launch'}});
  });
  await page.goto('/work');await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await page.getByRole('button',{name:'Start session',exact:true}).click();await expect(page.locator('#launch-preview')).toContainText('Response interrupted');
  await page.locator('#cancel-create').click();await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await page.getByRole('button',{name:'Check launch',exact:true}).click();await expect(page.locator('#session-title')).toHaveText('one-launch');
  expect(requests).toHaveLength(2);expect(requests[1].request_key).toBe(requests[0].request_key);
});

for(const kind of ['output','skills']){
  test(`remaining audit ${kind} sends stable identity and ignores stale errors after navigation`,async({page})=>{
    const {sessions}=await fixture(page);sessions[0].running=false;sessions.push({...sessions[0],id:'other',tmux_name:'other'});let pending;
    const endpoint=kind==='output'?'review':'skills';
    await page.route(`**/api/sessions/session-one/${endpoint}**`,route=>{pending=route;});
    await page.goto('/work#session/session-one');await page.locator('#session-detail > summary').click();await page.locator('#show-'+kind).click();await expect.poll(()=>!!pending).toBe(true);
    expect(new URL(pending.request().url()).searchParams.get('session_id')).toBe('root');
    await page.evaluate(()=>location.hash='#session/other');await expect(page.locator('#session-title')).toHaveText('other');await pending.fulfill({status:409,json:{detail:'Old session identity changed'}});
    await expect(page.locator('#notice')).not.toContainText('Old session identity changed');await expect(page.locator('#session-'+kind)).toBeHidden();
  });
  test(`remaining audit ${kind} rejects name reuse instead of showing replacement content`,async({page})=>{
    await fixture(page);const endpoint=kind==='output'?'review':'skills';
    await page.route(`**/api/sessions/session-one/${endpoint}**`,route=>new URL(route.request().url()).searchParams.get('session_id')==='root'?route.fulfill({status:409,json:{detail:'session identity changed'}}):route.fulfill({json:{content:'Replacement session content',notice:'Replacement session content',latest:null}}));
    await page.goto('/work#session/session-one');await page.locator('#session-detail > summary').click();await page.locator('#show-'+kind).click();await expect(page.locator('#notice')).toHaveText('session identity changed');await expect(page.locator('#session-'+kind)).toBeHidden();await expect(page.locator('#main')).not.toContainText('Replacement session content');
  });
}


test('remaining audit pending creation preserves edits made without closing',async({page})=>{
  const {sessions}=await fixture(page);let pending;
  await page.route('**/api/sessions',route=>{if(route.request().method()==='POST'){pending=route;return;}return route.fallback();});
  await page.goto('/work');await page.locator('#new-session').click();await page.locator('[name=task]').fill('Submitted');await page.getByRole('button',{name:'Create session',exact:true}).click();await expect.poll(()=>!!pending).toBe(true);
  await page.locator('[name=task]').fill('Unsent changes while waiting');const created={...sessions[0],id:'created',tmux_name:'created'};sessions.push(created);await pending.fulfill({json:created});
  await expect(page.locator('#notice')).toContainText('Created created');await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('[name=task]')).toHaveValue('Unsent changes while waiting');
});

for(const kind of ['output','skills'])test(`remaining audit ${kind} ignores reversed responses and a round trip`,async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;sessions.push({...sessions[0],id:'other',tmux_name:'other'});const pending=[];
  const endpoint=kind==='output'?'review':'skills',panel=page.locator('#session-'+kind);
  await page.route(`**/api/sessions/session-one/${endpoint}**`,route=>{pending.push(route);});
  await page.goto('/work#session/session-one');await page.locator('#session-detail > summary').click();
  await page.locator('#show-'+kind).click();await expect.poll(()=>pending.length).toBe(1);await page.locator('#show-'+kind).click();await expect.poll(()=>pending.length).toBe(2);
  const data=text=>kind==='output'?{content:text}:{notice:text,latest:null};
  await pending[1].fulfill({json:data('Newest response')});await expect(panel).toContainText('Newest response');
  await pending[0].fulfill({json:data('Outdated response')});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await expect(panel).toContainText('Newest response');
  await page.locator('#show-'+kind).click();await expect.poll(()=>pending.length).toBe(3);
  await page.evaluate(()=>location.hash='#session/other');await expect(page.locator('#session-title')).toHaveText('other');await page.evaluate(()=>location.hash='#session/session-one');await expect(page.locator('#session-title')).toHaveText('session-one');
  await pending[2].fulfill({json:data('Before navigation')});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await expect(panel).toBeHidden();
});


test('remaining audit edits and preview retain an uncertain launch and the newer draft',async({page})=>{
  const {sessions}=await fixture(page),requests=[];let previews=0;
  await page.route('**/api/workbench/recipes',route=>route.fulfill({json:[{id:'retry-edit',revision:1,title:'Retry editable',request:{tool:'shell',profile:'coder',task:'Original launch'}}]}));
  await page.route('**/api/workbench/launches/preview',route=>{previews++;return route.fulfill({json:{hash:'f'.repeat(64),config:route.request().postDataJSON().request,skills:[]}});});
  await page.route('**/api/workbench/launches',route=>{
    requests.push(route.request().postDataJSON());if(requests.length===1)return route.fulfill({status:503,json:{detail:'Uncertain response'}});
    sessions.push({...sessions[0],id:'original-launch',tmux_name:'original-launch'});return route.fulfill({json:{state:'created',session_id:'original-launch',name:'original-launch'}});
  });
  await page.goto('/work');await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await page.getByRole('button',{name:'Start session',exact:true}).click();await expect(page.locator('#launch-preview')).toContainText('Uncertain response');
  await page.locator('[name=task]').fill('Newer unsent edits');await page.locator('#cancel-create').click();await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();
  await expect(page.locator('[name=task]')).toHaveValue('Newer unsent edits');await page.locator('#preview-launch').click();await expect(page.locator('#notice')).toContainText('Created original-launch');
  expect(previews).toBe(1);expect(requests).toHaveLength(2);expect(requests[1]).toEqual(requests[0]);await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('[name=task]')).toHaveValue('Newer unsent edits');await expect(page.getByRole('button',{name:'Start session',exact:true})).toBeEnabled();
  await page.locator('#cancel-create').click();await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await expect(page.getByRole('button',{name:'Start session',exact:true})).toBeVisible();
});


test('remaining audit step revision conflicts preserve the original draft and version',async({page})=>{
  const {sessions,steps}=await fixture(page);sessions[0].running=false;
  const step={id:'step-fixture',version:1,root_id:'root',owner_id:'root',task:'Original step',reason:'Original purpose',expected_output:'Report',config:{tool:'shell',profile:'coder',worktree:false},dependencies:[{source_id:'root',readiness:'after-final'}],decision:'proposed',attempts:[]};steps.push(step);const edits=[];
  await page.route('**/api/workflow/steps/step-fixture/edit',route=>{edits.push(route.request().postDataJSON());return route.fulfill({status:409,json:{detail:'Step changed; reload before editing'}});});
  await page.goto('/work#step/step-fixture');await page.getByRole('button',{name:'Edit next step',exact:true}).click();await page.locator('[name=task]').fill('Unsent step edits');await page.locator('#cancel-create').click();
  step.version=2;step.task='Externally changed step';
  await page.evaluate(()=>location.hash='#work');await expect(page.locator('#work-view')).toBeVisible();await page.evaluate(()=>location.hash='#step/step-fixture');await page.getByRole('button',{name:'Edit next step',exact:true}).click();
  await expect(page.locator('[name=task]')).toHaveValue('Unsent step edits');await page.getByRole('button',{name:'Review scheduled step',exact:true}).click();await expect(page.locator('#create-error')).toHaveText('Step changed; reload before editing');expect(edits[0].expected_version).toBe(1);
  await expect(page.locator('[name=task]')).toHaveValue('Unsent step edits');
  page.once('dialog',dialog=>dialog.accept());await page.locator('#discard-create').click();await expect(page.locator('[name=task]')).toHaveValue('Externally changed step');
});

test('remaining audit discard keeps the uncertain launch receipt',async({page})=>{
  const {sessions}=await fixture(page),requests=[];
  await page.route('**/api/workbench/recipes',route=>route.fulfill({json:[{id:'discard-retry',revision:1,title:'Discard retry',request:{tool:'shell',profile:'coder',task:'Original launch'}}]}));
  await page.route('**/api/workbench/launches/preview',route=>route.fulfill({json:{hash:'f'.repeat(64),config:route.request().postDataJSON().request,skills:[]}}));
  await page.route('**/api/workbench/launches',route=>{requests.push(route.request().postDataJSON());if(requests.length===1)return route.fulfill({status:503,json:{detail:'Uncertain launch'}});sessions.push({...sessions[0],id:'discard-launch',tmux_name:'discard-launch'});return route.fulfill({json:{state:'created',name:'discard-launch'}});});
  await page.goto('/work');await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();await page.getByRole('button',{name:'Start session',exact:true}).click();await expect(page.locator('#launch-preview')).toContainText('Uncertain launch');await page.locator('[name=task]').fill('Temporary edits');
  page.once('dialog',dialog=>dialog.accept());await page.locator('#discard-create').click();await expect(page.locator('[name=task]')).toHaveValue('Original launch');await page.getByRole('button',{name:'Check launch',exact:true}).click();await expect(page.locator('#session-title')).toHaveText('discard-launch');expect(requests).toHaveLength(2);expect(requests[1]).toEqual(requests[0]);
});

for(const rejected of [false,true])test(`remaining audit root receipt recovers after ${rejected?'definitive rejection':'discard with invalid fields'}`,async({page})=>{
  const {sessions}=await fixture(page),requests=[];let previews=0;
  await page.route('**/api/workbench/launches/preview',route=>{previews++;return route.fulfill({json:{hash:'f'.repeat(64),config:route.request().postDataJSON().request,skills:[]}});});
  await page.route('**/api/workbench/launches/*',route=>new URL(route.request().url()).pathname.endsWith('/preview')?route.fallback():route.fulfill({status:404,json:{detail:'Launch request not found'}}));
  await page.route('**/api/workbench/launches',route=>{requests.push(route.request().postDataJSON());if(requests.length===1)return route.fulfill({status:rejected?400:503,json:{detail:rejected?'Configuration changed':'Uncertain launch'}});sessions.push({...sessions[0],id:'root-retry',tmux_name:'root-retry'});return route.fulfill({json:{state:'created',name:'root-retry'}});});
  await page.goto('/work');await page.locator('#new-session').click();await page.locator('[name=task]').fill('Root launch');await page.locator('#preview-launch').click();await page.locator('#confirm-launch').click();await expect(page.locator('#launch-preview')).toContainText(rejected?'Review the configuration':'Uncertain launch');
  if(rejected){await page.locator('#preview-launch').click();await page.locator('#confirm-launch').click();await expect(page.locator('#session-title')).toHaveText('root-retry');expect(previews).toBe(2);expect(requests[1].request_key).not.toBe(requests[0].request_key);}
  else{page.once('dialog',dialog=>dialog.accept());await page.locator('#discard-create').click();await expect(page.locator('[name=task]')).toHaveValue('');await page.getByRole('button',{name:'Check launch',exact:true}).click();await expect(page.locator('#notice')).toContainText('Created root-retry');await expect(page.locator('#create-dialog')).toBeVisible();expect(previews).toBe(1);expect(requests[1]).toEqual(requests[0]);}
});


test('remaining audit later rejection cannot erase a previously uncertain launch',async({page})=>{
  await fixture(page);const requests=[];let statusReads=0;
  await page.route('**/api/workbench/launches/preview',route=>route.fulfill({json:{hash:'f'.repeat(64),config:route.request().postDataJSON().request,skills:[]}}));
  await page.route('**/api/workbench/launches/*',route=>{if(new URL(route.request().url()).pathname.endsWith('/preview'))return route.fallback();statusReads++;return route.fulfill({status:404,json:{detail:'Launch request not found'}});});
  await page.route('**/api/workbench/launches',route=>{requests.push(route.request().postDataJSON());return route.fulfill({status:requests.length===1?503:400,json:{detail:requests.length===1?'Response lost':'Configuration changed'}});});
  await page.goto('/work');await page.locator('#new-session').click();await page.locator('[name=task]').fill('Original in flight');await page.locator('#preview-launch').click();await page.locator('#confirm-launch').click();await expect(page.locator('#launch-preview')).toContainText('Response lost');await page.locator('#create-form button[type=submit]').click();await expect(page.locator('#launch-preview')).toContainText('Configuration changed');
  expect(statusReads).toBe(0);expect(requests).toHaveLength(2);expect(requests[1]).toEqual(requests[0]);await expect(page.locator('#create-form button[type=submit]')).toHaveText('Check launch');
});
