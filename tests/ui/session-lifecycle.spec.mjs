import {test,expect} from '@playwright/test';
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
  return {requests,sessions};
}


async function openAdd(page,name='session-one') {
  await page.evaluate(name=>location.hash='#session/'+name,name);
  await expect(page.locator('#session-title')).toHaveText(name);
  if(await page.locator('#terminal-panel').isVisible())await page.locator('#close-terminal').click();
  await page.locator('#session-tree .add-session').first().click();
}

test('late child creation does not close another parent draft or change its route',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;sessions.push({...sessions[0],id:'other',tmux_name:'other-parent'});
  let release;
  await page.route('**/api/sessions/session-one/children',async route=>{
    await new Promise(resolve=>release=resolve);
    const child={...sessions[0],id:'created',tmux_name:'created-child',parent_session_id:'root'};sessions.push(child);await route.fulfill({json:child});
  });
  await page.goto('/work#session/session-one');await openAdd(page);
  await page.locator('[name=task]').fill('First child request');await page.locator('#create-form button[type=submit]').click();
  await expect.poll(()=>typeof release).toBe('function');await page.locator('#cancel-create').click();
  await openAdd(page,'other-parent');await page.locator('[name=task]').fill('Second parent unsent draft');
  release();await expect(page.locator('#notice')).toContainText('created-child');
  await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('[name=task]')).toHaveValue('Second parent unsent draft');
  await expect(page).toHaveURL(/#session\/other-parent$/);await expect(page.locator('#create-form button[type=submit]')).toBeEnabled();
});

test('rename during polling follows the same stable session and preserves its terminal draft',async({page})=>{
  const {sessions}=await fixture(page);await page.goto('/work#session/session-one');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  const terminal=page.frameLocator('iframe:not([hidden])');await terminal.locator('#toggle-composer').click();await terminal.locator('#composer').fill('Draft before automatic rename');
  sessions[0].tmux_name='renamed-session';
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#session-title')).toHaveText('renamed-session');await expect(page).toHaveURL(/#session\/renamed-session$/);
  const updated=page.frameLocator('iframe:not([hidden])');await expect(updated.locator('#composer')).toHaveValue('Draft before automatic rename');
  await expect(page.locator('#notice')).toBeHidden();
});

test('late history pagination cannot append old session events to the new session panel',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;sessions.push({...sessions[0],id:'other',tmux_name:'other-parent'});
  let release;
  await page.route('**/api/workbench/sessions/*/history*',async route=>{
    const url=new URL(route.request().url());
    if(url.searchParams.has('before')){await new Promise(resolve=>release=resolve);await route.fulfill({json:{events:[{at:'old',action:'OLD SESSION PRIVATE EVENT'}],audit:[]}});}
    else await route.fulfill({json:{notice:'History',events:url.pathname.includes('/root/')?Array.from({length:50},()=>({at:'now',action:'root event'})):[{at:'now',action:'Other session event'}],audit:[],before:'cursor'}});
  });
  await page.goto('/work#session/session-one');await page.locator('#session-detail > summary').click();await page.locator('#show-history').click();
  await page.getByRole('button',{name:'Older workflow events',exact:true}).click();await expect.poll(()=>typeof release).toBe('function');
  await page.evaluate(()=>location.hash='#session/other-parent');await expect(page.locator('#session-title')).toHaveText('other-parent');
  await page.locator('#session-detail > summary').click();await page.locator('#show-history').click();await expect(page.locator('#session-history')).toContainText('Other session event');
  release();await page.waitForTimeout(200);await expect(page.locator('#session-history')).not.toContainText('OLD SESSION PRIVATE EVENT');
});

for(const fail of [false,true])test(`late child ${fail?'failure':'success'} leaves a newly opened draft usable`,async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;let release;
  await page.route('**/api/sessions/session-one/children',async route=>{
    await new Promise(resolve=>release=resolve);
    if(fail)await route.fulfill({status:409,json:{detail:'Parent changed while creating'}});
    else {const child={...sessions[0],id:'created',tmux_name:'created-child',parent_session_id:'root'};sessions.push(child);await route.fulfill({json:child});}
  });
  await page.goto('/work#session/session-one');await openAdd(page);
  await page.locator('[name=task]').fill('First request');await page.locator('#create-form button[type=submit]').click();
  await expect.poll(()=>typeof release).toBe('function');await page.keyboard.press('Escape');await openAdd(page);
  await page.locator('[name=task]').fill('A separate unsent draft');release();
  await expect(page.locator('#notice')).toContainText(fail?'Parent changed':'created-child');
  await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('#create-error')).toBeEmpty();
  await expect(page.locator('[name=task]')).toHaveValue('A separate unsent draft');await expect(page.locator('#create-form button[type=submit]')).toBeEnabled();
});

test('browser Back follows remembered session identity after its old name is reused',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;sessions.push({...sessions[0],id:'other',tmux_name:'other-parent'});
  await page.goto('/work#session/session-one');await expect(page.locator('#session-title')).toHaveText('session-one');
  await page.evaluate(()=>location.hash='#session/other-parent');await expect(page.locator('#session-title')).toHaveText('other-parent');
  sessions[0].tmux_name='renamed-root';sessions.push({...sessions[0],id:'fresh',tmux_name:'session-one',initial_task:'A different session now uses the old name'});
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.locator('#refresh')).toBeEnabled();
  await page.goBack();await expect(page.locator('#session-title')).toHaveText('renamed-root');await expect(page).toHaveURL(/#session\/renamed-root$/);
  await page.goForward();await expect(page.locator('#session-title')).toHaveText('other-parent');
  await page.evaluate(()=>location.hash='#work');await page.locator('#work-history > summary').click();await page.locator('[data-node=fresh]').getByRole('link',{name:'Open work',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-one');await expect(page.locator('#session-brief')).toHaveText('A different session now uses the old name');
});

test('rename moves the draft away from an old name that another session can reuse',async({page})=>{
  const {sessions}=await fixture(page);await page.goto('/work#session/session-one');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  const terminal=page.frameLocator('iframe:not([hidden])');await terminal.locator('#toggle-composer').click();await terminal.locator('#composer').fill('Private draft attached to original session');
  sessions[0].tmux_name='renamed-root';sessions.push({...sessions[0],id:'fresh',tmux_name:'session-one',initial_task:''});
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.locator('#session-title')).toHaveText('renamed-root');
  await expect(page.frameLocator('iframe:not([hidden])').locator('#composer')).toHaveValue('Private draft attached to original session');
  await page.evaluate(()=>location.hash='#session/session-one');await expect(page.locator('#session-title')).toHaveText('session-one');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  await expect(page.frameLocator('iframe:not([hidden])').locator('#composer')).toHaveValue('');
});

for(const phase of ['preview','launch'])test(`closing a recipe during pending ${phase} cannot redirect or close a new draft`,async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;let release,launches=0;
  const request={tool:'shell',profile:'coder',task:'Recipe task',worktree:false};
  await page.route('**/api/workbench/recipes',route=>route.fulfill({json:[{id:'recipe',title:'Reusable check',revision:1,request}]}));
  await page.route('**/api/workbench/launches/preview',async route=>{if(phase==='preview')await new Promise(resolve=>release=resolve);await route.fulfill({json:{hash:'a'.repeat(64),config:request,task:request.task,skills:[]}});});
  await page.route('**/api/workbench/launches',async route=>{
    launches++;if(phase==='launch')await new Promise(resolve=>release=resolve);
    const session={...sessions[0],id:'recipe-result',tmux_name:'recipe-result'};sessions.push(session);await route.fulfill({json:{state:'created',name:session.tmux_name,session_id:session.id}});
  });
  await page.goto('/work');await page.locator('#run-recipe').click();await page.getByRole('button',{name:'Use recipe',exact:true}).click();
  await page.getByRole('button',{name:'Start session',exact:true}).click();await expect.poll(()=>typeof release).toBe('function');await page.keyboard.press('Escape');
  await page.locator('#new-session').click();await page.locator('[name=task]').fill('Unrelated unsent draft');release();
  if(phase==='launch')await expect(page.locator('#notice')).toContainText('recipe-result');else await page.waitForTimeout(200);
  await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('[name=task]')).toHaveValue('Unrelated unsent draft');
  expect(launches).toBe(phase==='launch'?1:0);await expect(page.locator('#create-form button[type=submit]')).toBeEnabled();
});

test('late recipe save cannot attach its revision to a different draft',async({page})=>{
  await fixture(page);let release;
  await page.route('**/api/workbench/recipes',async route=>{
    const saved=route.request().postDataJSON();await new Promise(resolve=>release=resolve);
    await route.fulfill({json:{id:'old-recipe',revision:1,title:saved.title,request:saved.request}});
  });
  await page.goto('/work');await page.locator('#new-session').click();await page.locator('[name=task]').fill('Original recipe task');
  await page.locator('#recipe-save-panel > summary').click();await page.locator('[name=recipe_title]').fill('Original recipe');await page.locator('#save-recipe').click();
  await expect.poll(()=>typeof release).toBe('function');await page.locator('#cancel-create').click();await page.locator('#new-session').click();
  await page.locator('[name=task]').fill('Unrelated task');release();await expect(page.locator('#notice')).toContainText('Recipe saved');
  await expect(page.locator('#save-recipe')).toHaveText('Save recipe');await expect(page.locator('#create-form')).toHaveAttribute('data-recipe','');
});

test('late continuation does not replace a newer draft under the same parent',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;let release;
  await page.route('**/api/workbench/sessions/root/configuration',async route=>{
    await new Promise(resolve=>release=resolve);await route.fulfill({json:{latest:{config:{tool:'shell',profile:'coder',repository:'/tmp/repo'}}}});
  });
  await page.goto('/work#session/session-one');await page.locator('#session-detail > summary').click();await page.locator('#continue-session').click();
  await expect.poll(()=>typeof release).toBe('function');await openAdd(page);await page.locator('[name=task]').fill('New independent child draft');
  release();await expect(page.locator('#continue-session')).toBeEnabled();await expect(page.locator('#create-title')).toHaveText('Add session');await expect(page.locator('[name=task]')).toHaveValue('New independent child draft');
});


test('renaming a selected session does not reopen its manually closed terminal',async({page})=>{
  const {sessions}=await fixture(page);await page.goto('/work#session/session-one');
  await expect(page.locator('#session-title')).toHaveText('session-one');
  if(await page.locator('#terminal-panel').isVisible())await page.locator('#close-terminal').click();
  sessions[0].tmux_name='renamed-closed';await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#session-title')).toHaveText('renamed-closed');await expect(page.locator('#terminal-panel')).toBeHidden();
});

test('late child creation respects navigation to results for the same session',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].running=false;let release;
  await page.route('**/api/sessions/session-one/children',async route=>{
    await new Promise(resolve=>release=resolve);const child={...sessions[0],id:'created',tmux_name:'created-child',parent_session_id:'root'};sessions.push(child);await route.fulfill({json:child});
  });
  await page.goto('/work#session/session-one');await openAdd(page);await page.locator('[name=task]').fill('First request');await page.locator('#create-form button[type=submit]').click();await expect.poll(()=>typeof release).toBe('function');
  await page.evaluate(()=>location.hash='#results/session-one');await expect(page).toHaveURL(/#results\/session-one$/);
  release();await expect(page.locator('#notice')).toContainText('created-child');await expect(page).toHaveURL(/#results\/session-one$/);
});
