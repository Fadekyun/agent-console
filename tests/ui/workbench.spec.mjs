import { test, expect } from '@playwright/test';
async function fixture(page) {
  const sessions = [{id:'root',tmux_name:'session-one',tool:'shell',profile:'coder',repository:'/tmp/repo',initial_task:'Fix the small layout issue',running:true,managed:true,attention_state:'normal',actions:['attach','interrupt','kill']}];
  const requests=[];
  await page.route('**/api/**', async route => {
    const req=route.request(), path=new URL(req.url()).pathname; let body={};
    if(path==='/api/me') body={profiles:[{name:'coder',display_name:'Coder',read_write_capability:'write',status:'active'},{name:'reviewer',display_name:'Reviewer',read_write_capability:'read_only',status:'active'}],tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',provider:'local',status:'ready'}]};
    else if(path==='/api/interface') body={label:'Staging',current_url:'https://current.example/'};
    else if(path==='/api/sessions') body=sessions;
    else if(path.endsWith('/children')) { requests.push(req.postDataJSON()); body={...sessions[0], id:'child',tmux_name:'session-two',parent_session_id:'root',initial_task:req.postDataJSON().task}; sessions.push(body); }
    else if(path.endsWith('/brief')) body={brief:''};
    else if(path.endsWith('/review')) body={content:'Check passed',alternate_screen:false};
    else if(path==='/api/skills/effective') body={effective:[],issues:[]};
    else if(path==='/api/skill-registry/preview') body={validation:{valid:true,issues:[]},policies:[],notice:'Selected skills only.'};
    await route.fulfill({json:body});
  });
  await page.routeWebSocket('**/ws/sessions/**', ws => { ws.send('Connected to staging\r\n'); });
  return {requests};
}
test('work, manual child, drafts and mobile terminal use the real components',async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));const {requests}=await fixture(page);
  await page.goto('/work'); await expect(page.getByRole('heading',{name:'Work in progress'})).toBeVisible();
  await page.getByRole('link',{name:'Open work'}).click();
  await page.getByRole('button',{name:'+ Add session',exact:true}).click();
  await page.locator('[name=task]').fill('Review only the changed layout');
  await page.locator('[name=profile]').selectOption('reviewer');
  await page.getByRole('button',{name:'Create session',exact:true}).click();
  await expect(page.locator('#session-title')).toHaveText('session-two');
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
  await page.getByRole('link',{name:'Skills',exact:true}).click();
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
