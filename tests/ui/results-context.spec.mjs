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


function result(sessionId,summary='Result for '+sessionId){return{id:'result-'+sessionId,session_id:sessionId,kind:'final',version:1,outcome:'pass',summary,created_at:'2026-10-03T00:00:00Z',checks:[],artifacts:[]};}
async function resultsFixture(page){
  const data=await fixture(page);data.sessions[0].running=false;data.sessions.push({...data.sessions[0],id:'other',tmux_name:'session-other'});
  await page.route('**/api/sessions/*/results',route=>route.fulfill({json:{results:[result(new URL(route.request().url()).pathname.split('/')[3])]}}));
  return data;
}
for(const action of ['publish','acknowledge'])test(`late ${action} cannot replace another session's Results panel`,async({page})=>{
  await resultsFixture(page);let release,completed=false;
  if(action==='publish')await page.route('**/api/sessions/root/results',async route=>{
    if(route.request().method()==='POST'){await new Promise(resolve=>release=resolve);await route.fulfill({json:result('root')});completed=true;}
    else await route.fulfill({json:{results:[result('root')]}});
  });
  else {
    await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{notice:'Inputs',items:[{id:'input',sequence:1,state:'queued',source_session_id:'source',result:result('source')} ]}}));
    await page.route('**/api/sessions/root/inbox/input/ack',async route=>{await new Promise(resolve=>release=resolve);await route.fulfill({json:{}});completed=true;});
  }
  await page.goto('/work#results/session-one');await expect(page.locator('#session-results')).toContainText('Result for root');
  if(action==='publish'){await page.getByText('Publish a result',{exact:true}).click();await page.getByRole('textbox',{name:'Summary',exact:true}).fill('A new root result');await page.getByRole('button',{name:'Publish result',exact:true}).click();}
  else await page.getByRole('button',{name:'Acknowledge delivery',exact:true}).click();
  await expect.poll(()=>typeof release).toBe('function');await page.evaluate(()=>location.hash='#results/session-other');
  await expect(page.locator('#session-results')).toContainText('Result for other');release();await expect.poll(()=>completed).toBe(true);
  await page.waitForTimeout(150);await expect(page.locator('#results-session')).toHaveText('session-other');await expect(page.locator('#session-results')).toContainText('Result for other');await expect(page.locator('#session-results')).not.toContainText('Result for root');
});

test('acknowledging an input preserves an unsent result draft and expanded fields',async({page})=>{
  await resultsFixture(page);let acknowledged=false;
  await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{notice:'Inputs',items:acknowledged?[]:[{id:'input',sequence:1,state:'queued',source_session_id:'source',result:result('source')}]}}));
  await page.route('**/api/sessions/root/inbox/input/ack',async route=>{acknowledged=true;await route.fulfill({json:{}});});
  await page.goto('/work#results/session-one');await page.getByText('Publish a result',{exact:true}).click();
  await page.getByRole('textbox',{name:'Summary',exact:true}).fill('My unfinished summary');await page.getByText('Checks & selected artifacts',{exact:true}).click();
  await page.getByRole('textbox',{name:'Checks performed (one per line)',exact:true}).fill('Check still being written');
  await page.getByRole('button',{name:'Acknowledge delivery',exact:true}).click();await expect(page.getByText('No handoffs yet.',{exact:true})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toHaveValue('My unfinished summary');await expect(page.getByRole('textbox',{name:'Checks performed (one per line)',exact:true})).toHaveValue('Check still being written');
});

test('publish failure stays beside the form and preserves its draft after a pending request',async({page})=>{
  await resultsFixture(page);let release;
  await page.route('**/api/sessions/root/results',async route=>{
    if(route.request().method()==='POST'){await new Promise(resolve=>release=resolve);await route.fulfill({status:409,json:{detail:'Selected artifact changed. Review and retry.'}});}
    else await route.fulfill({json:{results:[result('root')]}});
  });
  await page.goto('/work#results/session-one');await page.getByText('Publish a result',{exact:true}).click();await page.getByRole('textbox',{name:'Summary',exact:true}).fill('Keep my summary');
  await page.getByRole('button',{name:'Publish result',exact:true}).click();await expect.poll(()=>typeof release).toBe('function');await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toBeDisabled();
  release();await expect(page.locator('#session-results [role=alert]')).toContainText('Selected artifact changed');await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toHaveValue('Keep my summary');await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toBeEnabled();
});

test('connection skill inspection remains bound to the chosen target session',async({page})=>{
  const {sessions}=await resultsFixture(page);sessions.push({...sessions[1],id:'third',tmux_name:'session-third'});let release;
  await page.route('**/api/sessions/root/connections',route=>route.fulfill({json:{version:0,nodes:[{session_id:'root',owner_id:null}],edges:[],readiness:{root:{blocked:false,reasons:[]}}}}));
  await page.route('**/api/sessions/session-other/skills?session_id=other',async route=>{await new Promise(resolve=>release=resolve);await route.fulfill({json:{latest:{skills:[{name:'other-only-skill',hash:'a'.repeat(64)}]}}});});
  await page.route('**/api/sessions/session-third/skills?session_id=third',route=>route.fulfill({json:{latest:{skills:[{name:'third-only-skill',hash:'b'.repeat(64)}]}}}));
  await page.goto('/work#results/session-one');await page.getByText('Connected inputs',{exact:true}).click();await page.getByText('Attach an existing session',{exact:true}).click();
  await page.getByRole('button',{name:'Inspect existing skill snapshot',exact:true}).click();await expect.poll(()=>typeof release).toBe('function');await page.getByRole('combobox',{name:'Existing session',exact:true}).selectOption('third');
  release();await expect(page.getByRole('button',{name:'Inspect existing skill snapshot',exact:true})).toBeEnabled();await expect(page.locator('#session-results')).not.toContainText('other-only-skill');
  await page.getByRole('button',{name:'Inspect existing skill snapshot',exact:true}).click();await expect(page.locator('#session-results')).toContainText('third-only-skill');await page.getByRole('combobox',{name:'Existing session',exact:true}).selectOption('other');await expect(page.locator('#session-results')).not.toContainText('third-only-skill');
});

for(const success of [true,false])test(`pending publish defers inbox refresh and ${success?'clears the published':'keeps the failed'} draft`,async({page})=>{
  await resultsFixture(page);let release,acknowledged=false,publishes=0;const requestKeys=[];
  await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{notice:'Inputs',items:acknowledged?[]:[{id:'input',sequence:1,state:'queued',source_session_id:'source',result:result('source')}]}}));
  await page.route('**/api/sessions/root/inbox/input/ack',async route=>{acknowledged=true;await route.fulfill({json:{}});});
  await page.route('**/api/sessions/root/results',async route=>{
    if(route.request().method()==='POST'){publishes++;requestKeys.push(route.request().postDataJSON().request_key);await new Promise(resolve=>release=resolve);await route.fulfill(success?{json:result('root')}:{status:409,json:{detail:'Artifact unavailable'}});}
    else await route.fulfill({json:{results:[result('root')]}});
  });
  await page.goto('/work#results/session-one');await page.getByText('Publish a result',{exact:true}).click();await page.getByRole('textbox',{name:'Summary',exact:true}).fill('Pending publication');
  await page.getByRole('button',{name:'Publish result',exact:true}).click();await expect.poll(()=>typeof release).toBe('function');
  await page.getByRole('button',{name:'Acknowledge delivery',exact:true}).click();await expect(page.getByRole('button',{name:'Acknowledge delivery',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Publish result',exact:true})).toBeDisabled();await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toHaveValue('Pending publication');
  release();await expect(page.getByText('No handoffs yet.',{exact:true})).toBeVisible();
  if(success)await page.getByText('Publish a result',{exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toHaveValue(success?'':'Pending publication');await expect(page.getByRole('button',{name:'Publish result',exact:true})).toBeEnabled();
  expect(publishes).toBe(1);
  if(!success){
    await expect(page.locator('#session-results [role=alert]')).toContainText('Artifact unavailable');await page.getByRole('button',{name:'Publish result',exact:true}).click();await expect.poll(()=>publishes).toBe(2);expect(requestKeys[1]).toBe(requestKeys[0]);release();await expect(page.locator('#session-results [role=alert]')).toContainText('Artifact unavailable');
  }
});

test('retrying a failed input refresh retains the unpublished draft',async({page})=>{
  await resultsFixture(page);let failRefresh=false;
  await page.route('**/api/sessions/root/results',route=>route.fulfill(failRefresh?{status:503,json:{detail:'Results temporarily unavailable'}}:{json:{results:[result('root')]}}));
  await page.route('**/api/sessions/root/inbox',route=>route.fulfill({json:{notice:'Inputs',items:[{id:'input',sequence:1,state:'queued',source_session_id:'source',result:result('source')}]}}));
  await page.route('**/api/sessions/root/inbox/input/ack',route=>{failRefresh=true;return route.fulfill({json:{}});});
  await page.goto('/work#results/session-one');await page.getByText('Publish a result',{exact:true}).click();await page.getByRole('textbox',{name:'Summary',exact:true}).fill('Retain after failed refresh');
  await page.getByRole('button',{name:'Acknowledge delivery',exact:true}).click();await expect(page.locator('#session-results')).toContainText('Results temporarily unavailable');failRefresh=false;await page.getByRole('button',{name:'Retry loading results',exact:true}).click();await expect(page.getByRole('textbox',{name:'Summary',exact:true})).toHaveValue('Retain after failed refresh');
});
