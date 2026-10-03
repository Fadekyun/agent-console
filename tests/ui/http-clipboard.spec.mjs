import {test,expect} from '@playwright/test';

test.skip(({baseURL}) => new URL(baseURL).hostname !== 'console-http.test', 'Use playwright.http-clipboard.config.mjs to exercise a real insecure HTTP origin.');

async function fixture(page){
  const writes=[];
  const session={id:'clip',tmux_name:'clipboard-test',tool:'shell',profile:'coder',repository:'/tmp/fixture',running:true,managed:true,live_state:'agent active',attention_state:'normal',actions:['attach','interrupt','kill'],child_count:0,total_child_count:0};
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;let body={};
    if(path==='/api/interface')body={label:'Clipboard fixture'};
    else if(path.endsWith('/brief'))body={brief:''};
    else if(path.endsWith('/review'))body={content:'HTTP clipboard text\nNever auto-send\n',line_count:2,capture_scope:'history',alternate_screen:false};
    else if(path==='/api/me')body={login:'fixture',access_surface:'local-lan',default_tool:'shell',profiles:[{name:'coder',display_name:'Coder',status:'active',read_write_capability:'write'}],auth_contexts:[{tool:'shell',name:'default',status:'ready',enabled:true,default:true}],tool_status:[{name:'shell',status:'ready'}]};
    else if(path==='/api/sessions')body=[session];
    else if(path==='/api/delegations')body={roots:[session],delegations:[],max_children_per_parent:3};
    else if(['/api/plans','/api/projects','/api/profiles'].includes(path))body=[];
    await route.fulfill({json:body});
  });
  await page.routeWebSocket('**/ws/sessions/**',ws=>{ws.onMessage(value=>{if(Buffer.isBuffer(value))writes.push(value.toString());});ws.send('HTTP clipboard text\r\n');});
  return writes;
}
async function open(page){
  const writes=await fixture(page);await page.goto('/terminal?session=clipboard-test');
  await expect(page.locator('#connection')).toHaveText('Connected');
  expect(await page.evaluate(()=>isSecureContext)).toBe(false);
  expect(await page.evaluate(()=>typeof navigator.clipboard)).toBe('undefined');
  return writes;
}

test('HTTP terminal exposes Copy and Paste with input collapsed',async({page},info)=>{
  const writes=await open(page);await expect(page.locator('#input-drawer')).toBeHidden();
  for(const id of ['#copy-selection','#paste-clipboard']){
    await expect(page.locator(id)).toBeVisible();
    const box=await page.locator(id).boundingBox();const minimum=info.project.name==='desktop'?40:44;expect(box.width).toBeGreaterThanOrEqual(minimum);expect(box.height).toBeGreaterThanOrEqual(minimum);
  }
  if(info.project.name==='desktop')await page.locator('#paste-clipboard').click();else await page.locator('#paste-clipboard').tap();
  await expect(page.locator('#paste-sheet-text')).toBeFocused();
  await page.locator('#paste-sheet-text').fill('Cancelled draft');
  await page.locator('[data-close="paste-sheet"]').click();
  await expect(page.locator('#composer')).toHaveValue('');expect(writes).toEqual([]);
});

test('HTTP manual paste returns focus to draft and never sends',async({page})=>{
  const writes=await open(page);await page.locator('#toggle-composer').click();
  await page.locator('#composer').fill('before AFTER');
  await page.locator('#composer').evaluate(e=>e.setSelectionRange(7,12));
  await page.locator('#paste-device').click();
  await expect(page.getByRole('dialog',{name:'Paste from device',exact:true})).toBeVisible();
  await expect(page.locator('#paste-sheet-text')).toBeFocused();
  await page.locator('#paste-sheet-text').fill('review\nthis');
  await page.locator('#use-manual-paste').click();
  await expect(page.locator('#composer')).toHaveValue('before review\nthis');
  await expect(page.locator('#composer')).toBeFocused();expect(writes).toEqual([]);
});

test('HTTP copy fallback preserves text selection and focus',async({page})=>{
  await open(page);await page.locator('#copy-selection').click();
  await expect(page.locator('#text-content')).toContainText('HTTP clipboard text');
  await page.evaluate(()=>{
    const text=document.querySelector('#text-content');text.focus();
    const r=document.createRange();r.selectNodeContents(text);getSelection().removeAllRanges();getSelection().addRange(r);
    document.execCommand=()=>true;
  });
  await page.locator('#copy-dom-selection').click();
  await expect(page.locator('#text-content')).toBeFocused();
  expect(await page.evaluate(()=>getSelection().toString())).toContain('HTTP clipboard text');
});

test('HTTP native keyboard paste reaches the terminal with bracketed paste intact',async({page},info)=>{
  test.skip(info.project.name!=='desktop','Keyboard paste is a desktop check.');
  const writes=await open(page);await page.locator('#copy-selection').click();
  await expect(page.locator('#text-content')).toContainText('HTTP clipboard text');
  await page.locator('#copy-visible').click();
  await expect(page.locator('#connection')).toHaveText('Visible text copied');
  await page.locator('[data-close="text-dialog"]').click();
  await page.evaluate(()=>new Promise(resolve=>window.__terminal.write('\x1b[?2004h',resolve)));
  await page.locator('.xterm-helper-textarea').focus();await expect(page.locator('.xterm-helper-textarea')).toBeFocused();await page.keyboard.press('Control+V');
  await expect.poll(()=>writes).toEqual(['\x1b[200~HTTP clipboard text\rNever auto-send\r\x1b[201~']);
  await expect(page.locator('#composer')).toHaveValue('');
});

test('HTTP selected terminal text has a selectable fallback without losing the selection',async({page},info)=>{
  const writes=await open(page);
  await page.evaluate(()=>{window.__terminal.select(0,0,4);document.execCommand=()=>false;});
  await expect.poll(()=>page.evaluate(()=>window.__terminal.getSelection())).toBe('HTTP');
  if(info.project.name==='desktop')await page.locator('#copy-selection').click();else await page.locator('#copy-selection').tap();
  await expect(page.getByRole('dialog',{name:'Copy terminal selection',exact:true})).toBeVisible();
  await expect(page.locator('#copy-sheet-text')).toHaveValue('HTTP');
  await expect(page.locator('#copy-sheet-text')).toBeFocused();
  expect(await page.locator('#copy-sheet-text').evaluate(e=>e.selectionEnd-e.selectionStart)).toBe(4);
  expect(await page.evaluate(()=>window.__terminal.getSelection())).toBe('HTTP');expect(writes).toEqual([]);
});

test('HTTP native Copy shortcut copies selected output rather than sending Ctrl-C',async({page},info)=>{
  test.skip(info.project.name!=='desktop','Native keyboard shortcuts are a desktop check.');
  const writes=await open(page);
  await page.evaluate(()=>{window.__terminal.select(0,0,4);window.__terminal.focus();});
  await expect.poll(()=>page.evaluate(()=>window.__terminal.getSelection())).toBe('HTTP');
  await page.keyboard.press('Control+C');
  await page.locator('#paste-clipboard').click();await page.locator('#paste-sheet-text').press('Control+V');
  await expect(page.locator('#paste-sheet-text')).toHaveValue('HTTP');expect(writes).toEqual([]);
});

test('HTTP compact Paste works in the legacy dock and stages a draft',async({page},info)=>{
  test.skip(info.project.name!=='desktop','The legacy desktop dock is hidden by design at mobile widths; standalone terminal covers touch.');
  const writes=await fixture(page);await page.goto('/desktop');
  await page.getByRole('button',{name:'Attach',exact:true}).click();
  const frame=page.frameLocator('#terminal-frames iframe:not([hidden])');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  const native=page.frames().find(f=>f.url().includes('/terminal?'));
  expect(await native.evaluate(()=>isSecureContext)).toBe(false);
  await expect(frame.locator('#input-drawer')).toBeHidden();
  if(info.project.name==='desktop')await frame.locator('#paste-clipboard').click();else await frame.locator('#paste-clipboard').tap();
  await expect(frame.locator('#paste-sheet-text')).toBeFocused();
  await frame.locator('#paste-sheet-text').fill('Dock draft\nneeds review');
  await frame.locator('#use-manual-paste').click();
  await expect(frame.locator('#composer')).toHaveValue('Dock draft\nneeds review');
  await expect(frame.locator('#composer')).toBeFocused();expect(writes).toEqual([]);
});
