import {test,expect} from '@playwright/test';

// API order and activity are intentionally independent of creation/tree order.
async function fixture(page,{stoppedRoot=false}={}) {
  const make=(id,name,parent=null,day=1)=>({id,tmux_name:name,parent_session_id:parent,
    created_at:`2026-10-0${day}T00:00:00Z`,last_activity:`2026-10-0${day}T01:00:00Z`,
    tool:'shell',profile:'coder',repository:'/tmp/navigation-fixture',initial_task:'Independent UI verification',
    running:true,managed:true,attention_state:'normal',actions:['attach','interrupt','kill']});
  const root=make('root','root-first'),child=make('child','middle-child','root',2),leaf=make('leaf','leaf-child','child',3);
  const sibling=make('sibling','later-child','root',4),other=make('other','other-root',null,2);
  root.running=!stoppedRoot;root.actions=stoppedRoot?[]:root.actions;
  child.attention_state='blocked';
  const sessions=[child,sibling,leaf,root,other];
  let reads=0,sockets=0;
  function response(){
    const nodes=sessions.map(s=>({id:s.id,owner_id:s.parent_session_id,native_id:s.id,native_name:s.tmux_name,
      root_id:s.id==='other'?'other':'root',title:s.tmux_name,task:s.initial_task,repository:s.repository,
      created_at:s.created_at,last_activity:s.last_activity,tool:s.tool,profile:s.profile,
      mechanical:s.running?'running':'stopped',attention:s.attention_state,result_state:'unknown',
      needs_attention:s.attention_state!=='normal',waiting:false,attempts:[],readiness:{},hidden:false}));
    const groups=['root','other'].map(id=>({root_id:id,member_ids:nodes.filter(n=>n.root_id===id).map(n=>n.id),
      priority:nodes.some(n=>n.root_id===id&&n.needs_attention)?0:1,
      last_activity:sessions.find(s=>s.id===id).last_activity,children_total:id==='root'?3:0,children_complete:0}));
    return {sessions,nodes,groups,aliases:{},readiness:{ready:true,warnings:[]}};
  }
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;let body={};
    if(path==='/api/me')body={profiles:[{name:'coder',display_name:'Coder',read_write_capability:'write',status:'active'}],tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',provider:'local',status:'ready'}]};
    else if(path==='/api/interface')body={label:'Independent fixture'};
    else if(path==='/api/workbench'){reads++;body=response();}
    else if(path==='/api/sessions')body=sessions;
    else if(path.endsWith('/brief'))body={brief:''};
    else if(path.endsWith('/review'))body={content:'Fixture output'};
    else if(path==='/api/skills/effective')body={effective:[],issues:[]};
    await route.fulfill({json:body});
  });
  await page.routeWebSocket('**/ws/sessions/**',ws=>{sockets++;ws.send('Fixture connected\r\n');});
  return {sessions,get reads(){return reads;},get sockets(){return sockets;}};
}
async function refresh(page,fixture){
  await expect(page.locator('#refresh')).toBeEnabled();
  const before=fixture.reads;
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/workbench');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await response;
  await expect.poll(()=>fixture.reads).toBeGreaterThan(before);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function tree(page){
  if(await page.locator('#terminal-panel').isVisible())await page.locator('#terminal-sessions').click();
  return page.locator('#session-tree');
}

test('Add session does not move adjacent rows on hover or keyboard focus',async({page})=>{
  const f=await fixture(page);
  await page.goto('/work#session/root-first');
  const panel=await tree(page),add=panel.locator('[data-node=child] > .add-session');
  const sibling=panel.locator('[data-node=sibling]');
  const before=await sibling.boundingBox();
  await add.hover();await expect(add).toBeVisible();
  expect((await sibling.boundingBox()).y).toBeCloseTo(before.y,1);
  await add.focus();await refresh(page,f);
  await expect(panel.locator('[data-node=child] > .add-session')).toBeFocused();
  expect((await sibling.boundingBox()).y).toBeCloseTo(before.y,1);
  await panel.locator('[data-node=child] > .add-session').click();
  await expect(page.locator('#create-dialog')).toBeVisible();
  await expect(page.locator('#create-form [name=parent]')).toHaveValue('child');
});


test('Add session click completes when polling occurs between pointer down and up',async({page})=>{
  const f=await fixture(page);await page.goto('/work#session/root-first');
  const panel=await tree(page),add=panel.locator('[data-node=child] > .add-session');
  await add.scrollIntoViewIfNeeded();const box=await add.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  await refresh(page,f);await page.mouse.up();
  await expect(page.locator('#create-dialog')).toBeVisible();
  await expect(page.locator('#create-form [name=parent]')).toHaveValue('child');
});

test('tree sibling order survives reordered polling responses and changed activity',async({page})=>{
  const f=await fixture(page);await page.goto('/work#session/root-first');
  const panel=await tree(page);
  const order=()=>panel.locator('.node').evaluateAll(nodes=>nodes.map(n=>n.dataset.node));
  const before=await order();
  f.sessions.reverse();for(const s of f.sessions)s.last_activity='2026-10-03T12:00:00Z';
  await refresh(page,f);
  expect(await order()).toEqual(before);
});

test('same-priority work cards keep order when activity changes',async({page})=>{
  const f=await fixture(page);f.sessions.find(s=>s.id==='child').attention_state='normal';
  await page.goto('/work');
  const cards=page.locator('#work-list .card');
  await expect(cards).toHaveCount(2);
  const before=await cards.evaluateAll(nodes=>nodes.map(n=>n.dataset.node));
  f.sessions.find(s=>s.id==='root').last_activity='2026-10-03T12:00:00Z';
  await refresh(page,f);
  expect(await cards.evaluateAll(nodes=>nodes.map(n=>n.dataset.node))).toEqual(before);
});

for(const stoppedRoot of [false,true])test(`Open work selects the group root when root is ${stoppedRoot?'stopped':'running'} and a middle child needs attention`,async({page})=>{
  await fixture(page,{stoppedRoot});await page.goto('/work');
  await page.locator('#work-list .card[data-node=root]').getByRole('link',{name:'Open work',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('root-first');
  await expect(page.locator('#session-tree .node.active')).toHaveAttribute('data-node','root');
  if(stoppedRoot){
    await page.locator('#session-detail > summary').click();
    await expect(page.locator('#continue-session')).toBeVisible();
  }
});

test('explicit child navigation still selects that child rather than redirecting to root',async({page})=>{
  await fixture(page);await page.goto('/work#session/root-first');
  const panel=await tree(page);
  await panel.getByRole('link',{name:'middle-child',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('middle-child');
  await expect(page.locator('#session-tree .node.active')).toHaveAttribute('data-node','child');
});


async function crowdedDrawer(page,f){
  for(let i=0;i<24;i++)f.sessions.push({...f.sessions.find(s=>s.id==='sibling'),id:`extra-${i}`,tmux_name:`extra-child-${i}`,created_at:`2026-10-04T00:00:${String(i).padStart(2,'0')}Z`});
  await page.goto('/work#session/root-first');
  await expect(page.locator('#session-title')).toHaveText('root-first');
  if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
  const frame=page.frameLocator('iframe:not([hidden])');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await frame.locator('#toggle-composer').click();await frame.locator('#composer').fill('Keep this unsent root draft');
  await frame.locator('#toggle-composer').click();
  await page.locator('#terminal-sessions').click();
  const add=page.locator('#session-tree [data-node=extra-23] > .add-session');
  await add.scrollIntoViewIfNeeded();await add.focus();
  return add;
}

test('poll retains focused Add session position in a scrolled Sessions drawer',async({page})=>{
  const f=await fixture(page),add=await crowdedDrawer(page,f);
  const before=await add.boundingBox(),scroll=await page.locator('#sessions-dialog').evaluate(e=>e.scrollTop);
  expect(scroll).toBeGreaterThan(0);
  const sockets=f.sockets;
  await page.evaluate(()=>{window.__navigationFrame=document.querySelector('iframe:not([hidden])');});
  await refresh(page,f);
  await expect(add).toBeFocused();
  expect((await add.boundingBox()).y).toBeCloseTo(before.y,1);
  expect(await page.locator('#sessions-dialog').evaluate(e=>e.scrollTop)).toBeCloseTo(scroll,1);
  expect(await page.evaluate(()=>window.__navigationFrame===document.querySelector('iframe:not([hidden])'))).toBe(true);
  expect(f.sockets).toBe(sockets);
  await add.click();await expect(page.locator('#create-form [name=parent]')).toHaveValue('extra-23');
});

test('canceling drawer-origin Add retains navigation position and terminal connection',async({page})=>{
  const f=await fixture(page),add=await crowdedDrawer(page,f);
  const scroll=await page.locator('#sessions-dialog').evaluate(e=>e.scrollTop);
  await add.click();await expect(page.locator('#create-dialog')).toBeVisible();
  await page.locator('#cancel-create').click();await expect(page.locator('#create-dialog')).toBeHidden();
  if(await page.locator('#sessions-dialog').isHidden())await page.locator('#terminal-sessions').click();
  await expect(page.locator('#sessions-dialog')).toBeVisible();
  expect(await page.locator('#sessions-dialog').evaluate(e=>e.scrollTop)).toBeCloseTo(scroll,1);
  await expect(page.locator('#session-title')).toHaveText('root-first');
  await page.locator('#close-sessions').click();
  const frame=page.frameLocator('iframe:not([hidden])');await expect(frame.locator('#connection')).toHaveText('Connected');
  await frame.locator('#toggle-composer').click();
  await expect(frame.locator('#composer')).toHaveValue('Keep this unsent root draft');
});
