import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';

async function fixture(page) {
  const session={id:'layout-root',tmux_name:'layout-root',running:true,managed:true,tool:'shell',profile:'general',actions:['attach'],attached_clients:0,attention_state:'normal'};
  const profiles=[{name:'general',display_name:'General',status:'active',read_write_capability:'write'}];
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    let body={};
    if(path==='/api/me')body={login:'fixture',profiles,tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',default:true}],default_tool:'shell'};
    else if(path==='/api/sessions')body=[session];
    else if(['/api/projects','/api/plans','/api/session-groups'].includes(path))body=[];
    else if(path==='/api/delegations')body={roots:[{...session,children:[]}],delegations:[]};
    else if(path==='/api/profiles')body=profiles;
    return route.fulfill({json:body});
  });
  await page.addInitScript(()=>{
    window.__layoutSent=[];
    class Socket{static OPEN=1;constructor(){this.readyState=1;queueMicrotask(()=>this.onopen?.());}send(v){if(typeof v!=='string')window.__layoutSent.push(new TextDecoder().decode(v));}close(){this.readyState=3;}}
    window.WebSocket=Socket;
  });
}
const draft='First line\nSecond line\nThird line\nFourth line\nFifth line';
async function inViewport(locator){
  const geometry=await locator.evaluate(el=>{const r=el.getBoundingClientRect();return{top:r.top,bottom:r.bottom,height:r.height,viewport:innerHeight,hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};});
  expect(geometry.top).toBeGreaterThanOrEqual(0);expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewport);expect(geometry.hit).toBe(true);return geometry;
}

test('multiline terminal drafts grow and retain their content across viewport changes',async({page})=>{
  await fixture(page);await page.setViewportSize({width:360,height:800});
  await page.goto('/terminal?session=layout-root&embed=1');await expect(page.locator('#connection')).toHaveText('Connected');
  await page.locator('#toggle-composer').click();const composer=page.locator('#composer');const initial=(await composer.boundingBox()).height;
  await composer.fill(draft);expect((await composer.boundingBox()).height).toBeGreaterThan(initial+40);
  await page.setViewportSize({width:360,height:180});await expect(composer).toHaveValue(draft);
  expect((await page.locator('.terminal-frame').boundingBox()).height).toBeGreaterThanOrEqual(24);
  expect((await inViewport(page.locator('#send-enter'))).height).toBeGreaterThanOrEqual(44);
  await page.setViewportSize({width:360,height:800});expect((await composer.boundingBox()).height).toBeGreaterThan(initial+40);
  await page.locator('#send-enter').click();expect(await page.evaluate(()=>window.__layoutSent)).toEqual([draft+'\r']);
  await expect(composer).toHaveValue('');
});

test('standalone keyboard-height layout keeps Send reachable and the draft unsent',async({page})=>{
  await fixture(page);await page.setViewportSize({width:360,height:240});await page.goto('/terminal?session=layout-root');
  await expect(page.locator('#connection')).toHaveText('Connected');await page.locator('#toggle-composer').click();await page.locator('#composer').fill(draft);
  await inViewport(page.locator('#send-enter'));await inViewport(page.locator('#paste-device'));
  expect((await page.locator('.terminal-frame').boundingBox()).height).toBeGreaterThanOrEqual(24);
  expect(await page.evaluate(()=>window.__layoutSent)).toEqual([]);
});

test('very short embedded drafts retain reachable scrollable Send controls',async({page})=>{
  await fixture(page);await page.setViewportSize({width:360,height:100});await page.goto('/terminal?session=layout-root&embed=1');
  await expect(page.locator('#connection')).toHaveText('Connected');await page.locator('#toggle-composer').click();await page.locator('#composer').fill(draft);
  await page.locator('#send-enter').scrollIntoViewIfNeeded();await inViewport(page.locator('#send-enter'));
  await page.locator('#send-enter').click();expect(await page.evaluate(()=>window.__layoutSent)).toEqual([draft+'\r']);
});

test('late legacy dock loading does not steal focus from search',async({page})=>{
  await page.setViewportSize({width:1280,height:800});await fixture(page);let delayed;
  await page.route('**/terminal?**',route=>{delayed=route;});await page.goto('/desktop');
  await page.locator('#active-sessions [data-attach]').click();await expect.poll(()=>Boolean(delayed)).toBe(true);
  await page.locator('#filter-search').fill('layout');
  await delayed.fulfill({contentType:'text/html',body:await readFile('web/static/terminal.html','utf8')});
  const frame=page.frameLocator('#terminal-frames iframe');await expect(frame.locator('#connection')).toHaveText('Connected');
  await expect(page.locator('#filter-search')).toBeFocused();
  await page.locator('.terminal-tab').click();await expect(frame.locator('.xterm-helper-textarea')).toBeFocused();
});
