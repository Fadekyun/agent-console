import { test, expect } from '@playwright/test';

async function fixture(page) {
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    let body={};
    if(path==='/api/me')body={login:'fixture',profiles:[],tool_status:[],auth_contexts:[]};
    else if(path==='/api/sessions')body=[{id:'copy-id',tmux_name:'copy-session',tool:'shell',profile:'general',running:true,managed:true,actions:[]}];
    else if(path==='/api/plans')body=[{id:'plan-copy',title:'Clipboard plan',status:'planned',repository:'/fixture'}];
    else if(path==='/api/plans/plan-copy')body={id:'plan-copy',title:'Clipboard plan',status:'planned',plan:'Plan content'};
    else if(path==='/api/delegations')body={roots:[],delegations:[]};
    else if(['/api/projects','/api/profiles','/api/session-groups'].includes(path))body=[];
    return route.fulfill({json:body});
  });
  return errors;
}
async function denyCopy(page,throws=true){
  await page.evaluate(throws=>{
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new DOMException('Denied','NotAllowedError');}}});
    document.execCommand=()=>{if(throws)throw new DOMException('Copy unavailable');return false;};
  },throws);
}
async function openPlan(page){
  await page.goto('/mobile');await page.locator('[data-mobile-tab=plans]').click();
  await page.locator('.mobile-plan').getByRole('button',{name:'View',exact:true}).click();
  await expect(page.locator('#mobile-plan-title')).toHaveText('Clipboard plan');
}

test('desktop copy failure exposes selectable name without leaving a temporary field or page error',async({page})=>{
  const errors=await fixture(page);await page.goto('/desktop');await denyCopy(page);
  const row=page.locator('#active-sessions tr').filter({hasText:'copy-session'});
  await row.locator('summary').click();await row.getByRole('button',{name:'Copy name',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Copy session name',exact:true});await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('textbox')).toHaveValue('copy-session');await expect(dialog.getByRole('textbox')).toBeFocused();
  await expect(page.locator('textarea.visually-hidden')).toHaveCount(0);
  expect(errors).toEqual([]);await dialog.getByRole('button',{name:'Close'}).click();await expect(dialog).toHaveCount(0);
  await expect(row.locator('summary')).toBeFocused();
});

test('mobile denied copy uses selectable in-page fallback above plan dialog and restores focus',async({page})=>{
  const errors=await fixture(page);await openPlan(page);await denyCopy(page,false);
  await page.locator('#mobile-plan-copy').click();
  const dialog=page.getByRole('dialog',{name:'Copy command',exact:true});await expect(dialog).toBeVisible();
  const field=dialog.getByRole('textbox');await expect(field).toHaveValue('agentctl plan execute plan-copy');await expect(field).toBeFocused();
  expect(await field.evaluate(el=>el.selectionEnd-el.selectionStart)).toBe('agentctl plan execute plan-copy'.length);
  const box=await dialog.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(page.viewportSize().width);
  await dialog.getByRole('button',{name:'Close'}).click();await expect(page.locator('#mobile-plan-copy')).toBeFocused();
  await expect(page.locator('#mobile-plan-dialog')).toBeVisible();expect(errors).toEqual([]);
});

test('native fallback inside modal has a focusable selection and cleans up on success',async({page})=>{
  await fixture(page);await openPlan(page);await denyCopy(page);
  await page.evaluate(()=>{document.execCommand=()=>{const el=document.activeElement;window.copyObservation={value:el.value,inModal:!!el.closest('dialog[open]'),selected:el.selectionEnd-el.selectionStart};return true;};});
  await page.locator('#mobile-plan-copy').click();
  await expect.poll(()=>page.evaluate(()=>window.copyObservation)).toEqual({value:'agentctl plan execute plan-copy',inModal:true,selected:'agentctl plan execute plan-copy'.length});
  await expect(page.locator('#mobile-plan-copy')).toBeFocused();await expect(page.locator('textarea.visually-hidden')).toHaveCount(0);
  await expect(page.getByRole('dialog',{name:'Copy command',exact:true})).toHaveCount(0);
});

test('late denied copy permission cannot reopen a dismissed plan dialog',async({page})=>{
  await fixture(page);await openPlan(page);test.skip(!await page.evaluate(()=>isSecureContext),'Async clipboard requires secure context');
  await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>new Promise((resolve,reject)=>{window.rejectCopy=reject;})}});document.execCommand=()=>false;});
  await page.locator('#mobile-plan-copy').click();await expect.poll(()=>page.evaluate(()=>!!window.rejectCopy)).toBe(true);
  await page.locator('[data-close=mobile-plan-dialog]').click();await page.locator('[data-mobile-tab=sessions]').click();
  await page.evaluate(()=>window.rejectCopy(new DOMException('Denied')));
  await expect(page.getByRole('dialog',{name:'Copy command',exact:true})).toHaveCount(0);await expect(page.locator('#mobile-plan-dialog')).toBeHidden();
});

test('reopening the same plan dialog does not revive an old denied copy request',async({page})=>{
  await fixture(page);await openPlan(page);test.skip(!await page.evaluate(()=>isSecureContext),'Async clipboard requires secure context');
  await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>new Promise((resolve,reject)=>{window.rejectCopy=reject;})}});document.execCommand=()=>false;});
  await page.locator('#mobile-plan-copy').click();await expect.poll(()=>page.evaluate(()=>!!window.rejectCopy)).toBe(true);
  await page.locator('[data-close=mobile-plan-dialog]').click();
  await page.locator('.mobile-plan').getByRole('button',{name:'View',exact:true}).click();await expect(page.locator('#mobile-plan-title')).toHaveText('Clipboard plan');
  await page.locator('#mobile-plan-copy').focus();await page.evaluate(()=>window.rejectCopy(new DOMException('Denied')));
  await expect(page.getByRole('dialog',{name:'Copy command',exact:true})).toHaveCount(0);await expect(page.locator('#mobile-plan-dialog')).toBeVisible();
});
