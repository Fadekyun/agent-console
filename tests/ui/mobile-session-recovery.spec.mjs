import {test,expect} from '@playwright/test';
async function fixture(page){
 const sessions=[{id:'one',tmux_name:'session-one',created_at:'2026-10-01',tool:'shell',profile:'general',running:true,managed:true,actions:['attach','restart','kill'],attention_state:'normal'}];
 const profiles=[{name:'general',read_write_capability:'write',allowed_delegation_profiles:['general']}];
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/me')return route.fulfill({json:{login:'fixture',access_surface:'local-lan',default_tool:'shell',profiles,tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',default:true,status:'ready'}]}});
  if(path==='/api/sessions')return route.fulfill({json:sessions});
  if(path==='/api/profiles')return route.fulfill({json:profiles});
  if(path==='/api/delegations')return route.fulfill({json:{roots:sessions,delegations:[]}});
  if(['/api/projects','/api/plans','/api/session-groups'].includes(path))return route.fulfill({json:[]});
  return route.fulfill({status:404,json:{detail:'Unexpected fixture route '+path}});
 });
 await page.goto('/mobile');await expect(page.locator('#mobile-sessions')).toContainText('session-one');
 return {sessions,errors};
}
test('mobile ignores stale refresh responses and retains sessions on refresh failure',async({page})=>{
 await fixture(page);let held,reads=0;
 await page.route('**/api/sessions?state=all',async route=>{
  reads++;if(reads===1){held=route;return;}
  return route.fulfill({json:[{id:'new',tmux_name:'newest-session',running:true,actions:[]}]});
 });
 await page.locator('#mobile-refresh').click();await expect.poll(()=>!!held).toBe(true);
 await page.locator('#mobile-refresh').click();await expect(page.locator('#mobile-sessions')).toContainText('newest-session');
 await held.fulfill({json:[]});await page.waitForTimeout(100);
 await expect(page.locator('#mobile-sessions')).toContainText('newest-session');
 await page.route('**/api/sessions?state=all',route=>route.fulfill({status:503,json:{detail:'Temporary unavailable'}}));
 await page.locator('#mobile-refresh').click();await expect(page.locator('#mobile-sessions-status')).toContainText('Temporary unavailable');
 await expect(page.locator('#mobile-sessions')).toContainText('newest-session');
});
test('mobile restart submits once and reports failure with retry',async({page})=>{
 const {errors}=await fixture(page);let held,count=0;
 await page.route('**/api/sessions/session-one/restart',route=>{count++;held=route;});
 const restart=page.locator('#mobile-sessions').getByRole('button',{name:'Restart',exact:true});
 await restart.click();await expect.poll(()=>count).toBe(1);await expect(restart).toBeDisabled();
 await held.fulfill({status:409,json:{detail:'Session is busy'}});
 await expect(page.locator('#mobile-sessions-status')).toContainText('Session is busy');await expect(restart).toBeEnabled();expect(errors).toEqual([]);
});
test('mobile kill failure leaves confirmation open and allows retry',async({page})=>{
 const {errors}=await fixture(page);let calls=0;
 await page.route('**/api/sessions/session-one/kill',route=>{calls++;return route.fulfill({status:409,json:{detail:'Session changed; retry'}});});
 await page.locator('#mobile-sessions').getByRole('button',{name:'Kill',exact:true}).click();await page.locator('#mobile-kill-confirm').click();
 await expect(page.locator('#mobile-kill-status')).toContainText('Session changed; retry');await expect(page.locator('#mobile-kill-dialog')).toBeVisible();await expect(page.locator('#mobile-kill-confirm')).toBeEnabled();expect(calls).toBe(1);expect(errors).toEqual([]);
});
test('mobile delegate prevents duplicate children and preserves failed draft',async({page})=>{
 const {errors}=await fixture(page);let held,count=0;
 await page.route('**/api/sessions/session-one/delegations',route=>{count++;held=route;});
 await page.locator('[data-mobile-tab=orchestration]').click();await page.getByRole('button',{name:'Delegate',exact:true}).click();
 await page.locator('#mobile-delegate [name=task]').fill('Keep this task');const submit=page.locator('#mobile-delegate button[type=submit]');await submit.click();
 await expect.poll(()=>count).toBe(1);await expect(submit).toBeDisabled();await held.fulfill({status:409,json:{detail:'Capacity reached'}});
 await expect(page.locator('#mobile-delegate-status')).toContainText('Capacity reached');await expect(page.locator('#mobile-delegate [name=task]')).toHaveValue('Keep this task');await expect(submit).toBeEnabled();expect(errors).toEqual([]);
});
test('late attention save cannot close or erase another session draft',async({page})=>{
 const {sessions,errors}=await fixture(page);sessions.push({...sessions[0],id:'two',tmux_name:'session-two'});await page.locator('#mobile-refresh').click();await expect(page.locator('.mobile-session')).toHaveCount(2);
 let held;await page.route('**/api/sessions/session-one/attention',route=>{held=route;});
 const card=name=>page.locator('.mobile-session').filter({hasText:name});
 await card('session-one').getByRole('button',{name:'Details'}).click();await page.locator('#mobile-attention-note').fill('First note');await page.locator('#mobile-attention-save').click();await expect.poll(()=>!!held).toBe(true);
 await page.locator('[data-close=mobile-attention-dialog]').click();await card('session-two').getByRole('button',{name:'Details'}).click();await page.locator('#mobile-attention-note').fill('Second draft');
 await held.fulfill({json:sessions[0]});await page.waitForTimeout(100);
 await expect(page.locator('#mobile-attention-dialog')).toBeVisible();await expect(page.locator('#mobile-attention-note')).toHaveValue('Second draft');expect(errors).toEqual([]);
});
test('late child creation cannot close a newer delegate form',async({page})=>{
 const {errors}=await fixture(page);let held,count=0;
 await page.route('**/api/sessions/session-one/delegations',route=>{count++;held=route;});
 await page.locator('[data-mobile-tab=orchestration]').click();await page.getByRole('button',{name:'Delegate',exact:true}).click();await page.locator('#mobile-delegate [name=task]').fill('First child');
 await page.locator('#mobile-delegate button[type=submit]').click();await expect.poll(()=>!!held).toBe(true);
 await page.locator('#mobile-delegate').evaluate(form=>form.requestSubmit());expect(count).toBe(1);
 await page.locator('[data-close=mobile-delegate-dialog]').click();await page.getByRole('button',{name:'Delegate',exact:true}).click();await page.locator('#mobile-delegate [name=task]').fill('New child draft');
 await held.fulfill({json:{}});await page.waitForTimeout(100);await expect(page.locator('#mobile-delegate-dialog')).toBeVisible();await expect(page.locator('#mobile-delegate [name=task]')).toHaveValue('New child draft');await expect(page.locator('#mobile-delegate button[type=submit]')).toBeEnabled();expect(errors).toEqual([]);
});
