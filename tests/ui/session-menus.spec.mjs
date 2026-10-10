import { test, expect } from '@playwright/test';

async function fixture(page, count = 1) {
  const profiles = [{name:'general',display_name:'General',status:'active',read_write_capability:'write',allowed_delegation_profiles:['general']}];
  const sessions = Array.from({length:count},(_,index)=>({id:`audit-${index}`,tmux_name:`audit-${index}`,tool:'shell',profile:'general',running:true,managed:true,actions:['attach','interrupt','restart','kill'],attention_state:'normal',last_activity:'2026-10-03T00:00:00Z',repository:'/fixture',attached_clients:0}));
  const requests = [], errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
    const request=route.request(), path=new URL(request.url()).pathname;
    requests.push({path,method:request.method()});
    let body={};
    if(path==='/api/me')body={login:'audit',access_surface:'local-lan',default_tool:'shell',profiles,tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',default:true,status:'ready'}]};
    else if(path==='/api/sessions')body=sessions;
    else if(path==='/api/delegations')body={roots:sessions.map(s=>({...s,children:[]})),delegations:[]};
    else if(path==='/api/profiles')body=profiles;
    else if(['/api/plans','/api/projects','/api/session-groups'].includes(path))body=[];
    else if(path.endsWith('/review'))body={notice:'Audit fixture',source:'fixture',content:'Selected session output'};
    else if(path.endsWith('/kill')){const session=sessions.find(s=>path.includes(s.tmux_name));session.running=false;session.actions=[];body=session;}
    await route.fulfill({json:body});
  });
  return {requests,errors,sessions};
}
async function openLastMenu(page) {
  const menu=page.locator('#active-sessions .action-menu').last();
  await menu.locator('summary').click();
  await expect(menu).toHaveAttribute('open','');
  return menu;
}
async function reachable(locator) {
  await expect.poll(()=>locator.evaluate(button=>{
    const r=button.getBoundingClientRect();
    const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    return hit===button||button.contains(hit);
  })).toBe(true);
}

test('only-row More actions escape table clipping and invoke the selected action',async({page})=>{
  const {requests,errors}=await fixture(page);await page.goto('/desktop');
  const menu=await openLastMenu(page);
  for(const button of await menu.locator('button').all())await reachable(button);
  await menu.locator('[data-review]').click();
  await expect(page.locator('#review-dialog')).toBeVisible();
  await expect(page.locator('#review-content')).toContainText('Selected session output');
  expect(requests.filter(r=>r.path.endsWith('/review')).map(r=>r.path)).toEqual(['/api/sessions/audit-0/review']);
  expect(errors).toEqual([]);
});

test('last-row menu remains reachable after polling and keeps action focus',async({page})=>{
  await page.clock.install();const {requests,sessions}=await fixture(page,3);await page.goto('/desktop');
  const menu=await openLastMenu(page);await menu.locator('[data-review]').focus();
  const reads=requests.filter(r=>r.path==='/api/sessions').length;
  sessions[0].last_activity='2026-10-03T00:01:00Z';
  await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  await expect(menu).toHaveAttribute('open','');await expect(menu.locator('[data-review]')).toBeFocused();
  await reachable(menu.locator('[data-review]'));
  await menu.locator('[data-review]').press('Enter');
  await expect(page.locator('#review-title')).toContainText('audit-2');
  await expect(page.locator('#session-inspector')).toBeHidden();
});

test('Enter and Space toggle More without opening inspector; Escape restores summary focus',async({page})=>{
  await fixture(page);await page.goto('/desktop');const summary=page.locator('.action-menu summary');
  await summary.focus();await summary.press('Enter');await expect(summary).toHaveAttribute('aria-expanded','true');
  await expect(page.locator('#session-inspector')).toBeHidden();
  await summary.press('ArrowDown');await expect(page.locator('[data-copy-name]')).toBeFocused();
  await page.keyboard.press('ArrowDown');await expect(page.locator('.action-menu [data-review]')).toBeFocused();
  await page.keyboard.press('Escape');await expect(summary).toBeFocused();await expect(summary).toHaveAttribute('aria-expanded','false');
  await summary.press('Space');await expect(summary).toHaveAttribute('aria-expanded','true');
  await summary.press('ArrowUp');await expect(page.locator('.action-menu [data-action=kill]')).toBeFocused();
  await summary.focus();await summary.press('Space');await expect(summary).toHaveAttribute('aria-expanded','false');
});

test('outside interaction closes More, only one menu opens, and Tab retains native action order',async({page})=>{
  await fixture(page,2);await page.goto('/desktop');await openLastMenu(page);
  await page.locator('.action-menu summary').first().focus();await page.locator('.action-menu summary').first().press('Enter');await expect(page.locator('.action-menu[open]')).toHaveCount(1);
  await page.locator('.action-menu summary').first().press('Tab');await expect(page.locator('[data-copy-name]').first()).toBeFocused();
  await page.keyboard.press('Tab');await expect(page.locator('[data-review]').first()).toBeFocused();
  await page.locator('h1').first().click();await expect(page.locator('.action-menu[open]')).toHaveCount(0);
});

test('More stays usable after viewport resize, scroll, and short-screen overflow',async({page})=>{
  await fixture(page,7);await page.goto('/desktop');const menu=await openLastMenu(page);
  await page.setViewportSize({width:360,height:260});
  await menu.locator('summary').scrollIntoViewIfNeeded();
  const kill=menu.locator('[data-action="kill"]');await kill.scrollIntoViewIfNeeded();await reachable(kill);
  const box=await menu.locator('.action-menu-popover').boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(360);expect(box.y).toBeGreaterThanOrEqual(0);expect(box.y+box.height).toBeLessThanOrEqual(260);
  await kill.click();await expect(page.locator('#confirm-dialog')).toBeVisible();
  await page.locator('#confirm-dialog button[value=cancel]').click();
});

test('holding a real menu click across a poll retains its DOM target and activates once',async({page})=>{
  await page.clock.install();const {requests,sessions}=await fixture(page,3);await page.goto('/desktop');
  const menu=await openLastMenu(page), review=menu.locator('[data-review]');
  const target=await review.elementHandle(), box=await review.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  const reads=requests.filter(r=>r.path==='/api/sessions').length;
  sessions[2].last_activity='2026-10-03T00:10:00Z';
  await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  expect(await target.evaluate(element=>element.isConnected)).toBe(true);
  await page.mouse.up();
  await expect(page.locator('#review-dialog')).toBeVisible();
  expect(requests.filter(r=>r.path.endsWith('/review')).map(r=>r.path)).toEqual(['/api/sessions/audit-2/review']);
});

test('polling closes unavailable actions and surviving handlers use fresh session details',async({page})=>{
  await page.clock.install();const {sessions,requests}=await fixture(page);await page.goto('/desktop');
  let menu=await openLastMenu(page);sessions[0].repository='/updated-repository';
  let reads=requests.filter(r=>r.path==='/api/sessions').length;await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  await menu.locator('[data-delegate]').click();await expect(page.locator('#delegate-form [name=repository]')).toHaveValue('/updated-repository');
  await page.locator('[data-close="delegate-dialog"]').click();menu=await openLastMenu(page);
  sessions[0].running=false;sessions[0].actions=[];reads=requests.filter(r=>r.path==='/api/sessions').length;await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  await expect(page.locator('.action-menu[open]')).toHaveCount(0);await expect(page.locator('#active-sessions [data-action="kill"]')).toHaveCount(0);
});

test('activity and attention changes keep active rows in creation order',async({page})=>{
  await page.clock.install();const {sessions,requests}=await fixture(page,3);
  sessions[0].created_at='2026-10-01T01:00:00Z';sessions[1].created_at='2026-10-01T02:00:00Z';sessions[2].created_at='2026-10-01T03:00:00Z';
  await page.goto('/desktop');const names=page.locator('#active-sessions .session-name');
  await expect(names).toHaveText(['audit-0','audit-1','audit-2']);
  sessions[2].attention_state='blocked';sessions[2].last_activity='2026-10-03T10:00:00Z';
  const reads=requests.filter(r=>r.path==='/api/sessions').length;await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  await expect(names).toHaveText(['audit-0','audit-1','audit-2']);
  await expect(page.locator('#active-sessions .session-row').last()).toContainText('Blocked');
});

test('inspector Escape preserves draft and restores row focus without stealing focus on refresh',async({page})=>{
  await page.clock.install();const {requests}=await fixture(page);await page.goto('/desktop');
  const row=page.locator('#active-sessions .session-row');await row.focus();await row.press('Enter');
  await expect(page.locator('#inspector-close')).toBeFocused();
  const note=page.locator('#attention-form [name=note]');await note.fill('Unsaved audit draft');
  const reads=requests.filter(r=>r.path==='/api/sessions').length;await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  await expect(note).toBeFocused();await note.press('Escape');await expect(page.locator('#session-inspector')).toBeHidden();await expect(row).toBeFocused();
  expect(requests.filter(r=>r.method!=='GET')).toEqual([]);
  await row.press('Enter');await expect(note).toHaveValue('Unsaved audit draft');
  const review=page.locator('#inspector-actions').getByRole('button',{name:'Review output',exact:true});
  await review.focus();const before=requests.filter(r=>r.path==='/api/sessions').length;await page.clock.fastForward(10100);
  await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(before);await expect(review).toBeFocused();
  await review.click();
  await expect(page.locator('#review-dialog')).toBeVisible();await page.keyboard.press('Escape');
  await expect(page.locator('#review-dialog')).toBeHidden();await expect(page.locator('#session-inspector')).toBeVisible();
});

test('row keyboard navigation follows displayed creation order instead of API order',async({page})=>{
  const {sessions}=await fixture(page,3);
  sessions[0].created_at='2026-10-01T02:00:00Z';sessions[1].created_at='2026-10-01T03:00:00Z';sessions[2].created_at='2026-10-01T01:00:00Z';
  await page.goto('/desktop');await expect(page.locator('#active-sessions .session-name')).toHaveText(['audit-2','audit-0','audit-1']);
  const first=page.locator('#active-sessions .session-row').first();await first.focus();await first.press('ArrowDown');
  await expect(page.locator('#inspector-name')).toHaveText('audit-0');
});


test('inspector follows renamed session ID and never transfers its draft to a reused name',async({page})=>{
  await page.clock.install();const {sessions,requests}=await fixture(page);await page.goto('/desktop');
  await page.locator('#active-sessions .session-row').click();const note=page.locator('#attention-form [name=note]');await note.fill('Private original draft');
  sessions[0].tmux_name='renamed-original';sessions.push({...sessions[0],id:'new-id',tmux_name:'audit-0',attention_note:''});
  const reads=requests.filter(r=>r.path==='/api/sessions').length;await page.clock.fastForward(10100);await expect.poll(()=>requests.filter(r=>r.path==='/api/sessions').length).toBeGreaterThan(reads);
  await expect(page.locator('#inspector-name')).toHaveText('renamed-original');await expect(note).toHaveValue('Private original draft');
  await page.locator('#inspector-close').click();await page.locator('#active-sessions .session-row[data-session="audit-0"]').click();await expect(note).toHaveValue('');
});
