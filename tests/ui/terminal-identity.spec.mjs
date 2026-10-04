import {test,expect} from '@playwright/test';

async function fixture(page){
  const sessions=[{id:'original-id',tmux_name:'original',tool:'shell',profile:'general',running:true,managed:true,actions:['attach'],attached_clients:0,attention_state:'normal'}];
  const requests=[];
  await page.route('**/api/**',route=>{
    const url=new URL(route.request().url()), path=url.pathname;requests.push(url);
    let body={};
    if(path==='/api/sessions')body=sessions;
    else if(path.endsWith('/brief'))body={brief:''};
    else if(path.endsWith('/review'))body={content:'Owned output',capture_scope:'fixture'};
    else if(path==='/api/me')body={login:'fixture',default_tool:'shell',profiles:[{name:'general',status:'active',read_write_capability:'write'}],tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',default:true}]};
    else if(path==='/api/delegations')body={roots:sessions.map(s=>({...s,children:[]})),delegations:[]};
    else if(['/api/projects','/api/plans','/api/session-groups','/api/profiles'].includes(path))body=[];
    return route.fulfill({json:body});
  });
  await page.addInitScript(()=>{
    window.__identitySockets=[];window.__identityBytes=[];
    class Socket{
      static OPEN=1;
      constructor(url){this.url=url;this.readyState=1;window.__identitySockets.push(this);queueMicrotask(()=>this.onopen?.());}
      send(value){if(typeof value!=='string')window.__identityBytes.push(new TextDecoder().decode(value));}
      close(){this.readyState=3;}
    }
    window.WebSocket=Socket;
  });
  return{sessions,requests};
}
async function connected(page,url='/terminal?session=original'){
  await page.goto(url);await expect(page.locator('#connection')).toHaveText('Connected');
}

test('standalone rename follows the pinned ID for reconnect and reads while retaining draft',async({page})=>{
  const {sessions,requests}=await fixture(page);await connected(page);
  await expect(page).toHaveURL(/session_id=original-id/);
  await page.locator('#toggle-composer').click();await page.locator('#composer').fill('Private original draft');
  sessions[0].tmux_name='renamed';sessions.push({...sessions[0],id:'replacement-id',tmux_name:'original'});
  await page.evaluate(()=>{const socket=window.__identitySockets.at(-1);socket.readyState=3;socket.onclose({code:1006});});
  await expect.poll(()=>page.evaluate(()=>window.__identitySockets.at(-1).url)).toMatch(/\/renamed\?session_id=original-id$/);
  await expect(page.locator('#session-name')).toHaveText('renamed');await expect(page.locator('#composer')).toHaveValue('Private original draft');
  await page.locator('#copy-selection').click();await expect(page.locator('#text-content')).toContainText('Owned output');
  const review=requests.filter(url=>url.pathname.endsWith('/review')&&url.searchParams.get('lines')==='1000').at(-1);
  expect(review.pathname).toBe('/api/sessions/renamed/review');expect(review.searchParams.get('session_id')).toBe('original-id');
  await page.reload();await expect(page.locator('#connection')).toHaveText('Connected');await expect(page.locator('#composer')).toHaveValue('Private original draft');
  await connected(page,'/terminal?session=original&session_id=replacement-id');await expect(page.locator('#composer')).toHaveValue('');
  expect(await page.evaluate(()=>window.__identityBytes)).toEqual([]);
});

test('known missing identity refuses a reused name and does not expose its stored draft',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].id='replacement-id';
  await page.addInitScript(()=>sessionStorage.setItem('agent-console:composer:id:original-id','Private old draft'));
  await page.goto('/terminal?session=original&session_id=original-id');
  await expect(page.locator('#connection')).toContainText('Original session is no longer available');
  expect(await page.evaluate(()=>window.__identitySockets.length)).toBe(0);await expect(page.locator('#composer')).toHaveValue('');
});

test('unavailable identity lookup refuses blind attachment and manual retry recovers',async({page})=>{
  await fixture(page);let unavailable=true;
  await page.route('**/api/sessions?state=all',route=>unavailable?route.fulfill({status:503,json:{detail:'Unavailable'}}):route.fallback());
  await page.goto('/terminal?session=original&session_id=original-id');await expect(page.locator('#connection')).toContainText('Session identity unavailable');
  expect(await page.evaluate(()=>window.__identitySockets.length)).toBe(0);
  unavailable=false;await page.locator('#reconnect').click();await expect(page.locator('#connection')).toHaveText('Connected');
});

test('older name-keyed drafts require explicit restore and remain unsent',async({page})=>{
  const {sessions}=await fixture(page);sessions[0].tmux_name='renamed';
  await page.addInitScript(()=>sessionStorage.setItem('agent-console:composer:original','Older unowned draft'));
  await connected(page,'/terminal?session=original&session_id=original-id');await expect(page.locator('#composer')).toHaveValue('');
  await page.reload();await expect(page.locator('#connection')).toHaveText('Connected');await expect(page.locator('#composer')).toHaveValue('');
  await page.locator('#restore-legacy-draft').click();
  await expect(page.locator('#composer')).toHaveValue('Older unowned draft');
  expect(await page.evaluate(()=>sessionStorage.getItem('agent-console:composer:id:original-id'))).toBe('Older unowned draft');
  expect(await page.evaluate(()=>window.__identityBytes)).toEqual([]);
});

test('legacy dock keeps the same iframe and active tab through rename and updates mutable tab actions',async({page})=>{
  await page.setViewportSize({width:1280,height:800});await page.clock.install();const {sessions}=await fixture(page);await page.goto('/desktop');
  await page.locator('#active-sessions [data-attach]').click();const frame=page.frameLocator('#terminal-frames iframe');await expect(frame.locator('#connection')).toHaveText('Connected');
  const original=await page.locator('#terminal-frames iframe').elementHandle();
  await frame.locator('#toggle-composer').click();await frame.locator('#composer').fill('Keep dock draft');
  sessions[0].tmux_name='renamed';sessions.push({...sessions[0],id:'replacement-id',tmux_name:'original'});
  await page.clock.fastForward(10100);await expect(page.locator('.terminal-tab')).toHaveText('renamed');
  expect(await original.evaluate(el=>el.isConnected)).toBe(true);await expect(frame.locator('#composer')).toHaveValue('Keep dock draft');
  await expect(frame.locator('#session-name')).toHaveText('renamed');
  await page.locator('.terminal-tab').click();await expect(frame.locator('.xterm-helper-textarea')).toBeFocused();
  await page.getByRole('button',{name:'Close renamed',exact:true}).click();await expect(page.locator('#terminal-frames iframe')).toHaveCount(0);
});


test('terminal failure offers manual reconnect without an automatic retry loop',async({page})=>{
  await fixture(page);await connected(page);
  await page.evaluate(()=>{const socket=window.__identitySockets.at(-1);socket.readyState=3;socket.onclose({code:4001,reason:'Temporary terminal failure'});});
  await expect(page.locator('#connection')).toHaveText('Temporary terminal failure');await expect(page.locator('#reconnect')).toBeVisible();
  await page.waitForTimeout(600);expect(await page.evaluate(()=>window.__identitySockets.length)).toBe(1);
  await page.locator('#reconnect').click();await expect(page.locator('#connection')).toHaveText('Connected');
  expect(await page.evaluate(()=>window.__identitySockets.length)).toBe(2);
});

for (const entry of ['typing', 'cleared', 'direct recovery']) {
  test(`saved draft remains recoverable when ${entry} precedes identity hydration`, async ({page}) => {
    await fixture(page);
    await page.addInitScript(() => {
      if (!sessionStorage.getItem('hydration-seeded')) {
        sessionStorage.setItem('agent-console:composer:id:original-id', 'Previously saved draft');
        sessionStorage.setItem('hydration-seeded', 'true');
      }
    });
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/sessions?state=all', async route => { await gate; await route.fallback(); });
    await page.goto('/terminal?session=original&session_id=original-id');
    if (entry === 'direct recovery') {
      await page.locator('.xterm-helper-textarea').focus();
      await page.keyboard.type('New typing');
    } else {
      await page.locator('#toggle-composer').click();
      await page.locator('#composer').fill('New typing');
      if (entry === 'cleared') await page.locator('#composer').fill('');
    }
    const current = entry === 'cleared' ? '' : 'New typing';
    expect(await page.evaluate(() => sessionStorage.getItem('agent-console:composer:id:original-id'))).toBe('Previously saved draft');
    release();
    await expect(page.locator('#connection')).toHaveText('Connected');
    await expect(page.locator('#composer')).toHaveValue(current);
    await expect(page.locator('#restore-saved-draft')).toBeVisible();
    await page.reload();
    await expect(page.locator('#connection')).toHaveText('Connected');
    await expect(page.locator('#composer')).toHaveValue(current);
    await expect(page.locator('#restore-saved-draft')).toBeVisible();
    // A selected current draft must survive explicit recovery too.
    await page.locator('#composer').evaluate(el => el.setSelectionRange(0, el.value.length));
    await page.locator('#restore-saved-draft').click();
    await expect(page.locator('#composer')).toHaveValue(current ? `${current}\nPreviously saved draft` : 'Previously saved draft');
    await expect(page.locator('#restore-saved-draft')).toBeHidden();
    expect(await page.evaluate(() => window.__identityBytes)).toEqual([]);
    await page.reload();
    await expect(page.locator('#connection')).toHaveText('Connected');
    await expect(page.locator('#restore-saved-draft')).toBeHidden();
    await expect(page.locator('#composer')).toHaveValue(current ? `${current}\nPreviously saved draft` : 'Previously saved draft');
  });
}

test('identity failure retains saved draft until validated retry and leaves early typing intact', async ({page}) => {
  await fixture(page);
  await page.addInitScript(() => sessionStorage.setItem('agent-console:composer:id:original-id', 'Saved before outage'));
  let unavailable = true;
  await page.route('**/api/sessions?state=all', route => unavailable ? route.fulfill({status:503,json:{detail:'Unavailable'}}) : route.fallback());
  await page.goto('/terminal?session=original&session_id=original-id');
  await expect(page.locator('#connection')).toContainText('Session identity unavailable');
  await page.locator('#toggle-composer').click(); await page.locator('#composer').fill('During outage');
  await expect(page.locator('#restore-saved-draft')).toBeHidden();
  unavailable = false; await page.locator('#reconnect').click();
  await expect(page.locator('#connection')).toHaveText('Connected');
  await expect(page.locator('#composer')).toHaveValue('During outage');
  await page.locator('#restore-saved-draft').click();
  await expect(page.locator('#composer')).toHaveValue('During outage\nSaved before outage');
  expect(await page.evaluate(() => window.__identityBytes)).toEqual([]);
});

test('failed recovery backup preserves the original stored draft and offers in-page restore', async ({page}) => {
  await fixture(page);
  await page.addInitScript(() => {
    sessionStorage.setItem('agent-console:composer:id:original-id', 'Saved under storage pressure');
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.endsWith(':recovery')) throw new DOMException('Fixture storage full', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  });
  let release; const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/sessions?state=all', async route => { await gate; await route.fallback(); });
  await page.goto('/terminal?session=original&session_id=original-id');
  await page.locator('#toggle-composer').click(); await page.locator('#composer').fill('Early text');
  release(); await expect(page.locator('#connection')).toHaveText('Connected');
  await expect(page.locator('#restore-saved-draft')).toBeVisible();
  await expect(page.locator('#restore-saved-draft')).toContainText('saving paused');
  await page.locator('#composer').fill('Newer text');
  expect(await page.evaluate(() => sessionStorage.getItem('agent-console:composer:id:original-id'))).toBe('Saved under storage pressure');
  await page.locator('#restore-saved-draft').click();
  await expect(page.locator('#composer')).toHaveValue('Newer text\nSaved under storage pressure');
  expect(await page.evaluate(() => sessionStorage.getItem('agent-console:composer:id:original-id'))).toBe('Newer text\nSaved under storage pressure');
  expect(await page.evaluate(() => window.__identityBytes)).toEqual([]);
});

test('untouched saved draft hydrates automatically without recovery on ordinary reload', async ({page}) => {
  await fixture(page);
  await page.addInitScript(() => sessionStorage.setItem('agent-console:composer:id:original-id', 'Normal saved draft'));
  await connected(page, '/terminal?session=original&session_id=original-id');
  await expect(page.locator('#composer')).toHaveValue('Normal saved draft');
  await expect(page.locator('#restore-saved-draft')).toBeHidden();
  await page.reload(); await expect(page.locator('#connection')).toHaveText('Connected');
  await expect(page.locator('#composer')).toHaveValue('Normal saved draft');
  await expect(page.locator('#restore-saved-draft')).toBeHidden();
});

test('visibility bookkeeping before identity does not turn an untouched saved draft into recovery', async ({page}) => {
  await fixture(page);
  await page.addInitScript(() => sessionStorage.setItem('agent-console:composer:id:original-id', 'Untouched dock draft'));
  let release; const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/sessions?state=all', async route => { await gate; await route.fallback(); });
  await page.goto('/terminal?session=original&session_id=original-id&embed=1&lifecycle=managed');
  for (const visible of [true, false, true]) {
    await page.evaluate(visible => window.postMessage({type:'agent-console:terminal-visibility',visible},location.origin), visible);
    await page.waitForTimeout(10);
  }
  release(); await expect(page.locator('#connection')).toHaveText('Connected');
  await expect(page.locator('#composer')).toHaveValue('Untouched dock draft');
  await expect(page.locator('#restore-saved-draft')).toBeHidden();
  expect(await page.evaluate(() => window.__identityBytes)).toEqual([]);
});
