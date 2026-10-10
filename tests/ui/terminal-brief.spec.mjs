import {test, expect} from '@playwright/test';

const draftKey = 'agent-console:composer:id:brief-id';
const original = {id:'brief-id', tmux_name:'brief-session', running:true, managed:true};
async function fixture(page, {draft='Original draft', embedded=false}={}) {
  const briefs=[], writes=[];
  if (draft !== null) await page.addInitScript(({key,value}) => sessionStorage.setItem(key,value), {key:draftKey,value:draft});
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/brief')) { briefs.push(route); return; }
    return route.fulfill({json:path==='/api/sessions'?[original]:{}});
  });
  await page.routeWebSocket('**/ws/sessions/**', socket => {
    socket.onMessage(data => {if(Buffer.isBuffer(data)) writes.push(data.toString());});
    socket.send('Ready\r\n');
  });
  await page.goto(`/terminal?session=brief-session${embedded?'&embed=1&lifecycle=managed':''}`);
  if (embedded) await visibility(page,true);
  await expect(page.locator('#connection')).toHaveText('Connected');
  return {briefs,writes};
}
async function visibility(page, visible) {
  await page.evaluate(visible => window.postMessage({type:'agent-console:terminal-visibility',visible},location.origin),visible);
}
async function openInput(page) {
  if (await page.locator('#input-drawer').isHidden()) await page.locator('#toggle-composer').click();
}
async function load(page) {
  await page.locator('#terminal-more > summary').click();
  await page.locator('#load-brief').click();
}
async function finish(route, brief='Stored brief', status=200) {
  await route.fulfill({status,json:status===200?{brief}:{detail:brief}});
  // Allow the response handler to settle before asserting that nothing changed.
  await route.request().response().then(response=>response.finished());
}
async function settle(page) { await page.waitForTimeout(100); }

for (const [selection,start,end] of [['full',0,16],['partial',4,9]]) {
  test(`delayed explicit brief preserves a newer ${selection} selected draft`, async ({page}) => {
    const {briefs,writes} = await fixture(page);
    await openInput(page); await load(page); await expect.poll(()=>briefs.length).toBe(1);
    await page.locator('#composer').fill('Newer draft text');
    await page.locator('#composer').evaluate((node,range)=>node.setSelectionRange(...range),[start,end]);
    await finish(briefs[0]); await settle(page);
    await expect(page.locator('#composer')).toHaveValue('Newer draft text');
    expect(await page.evaluate(key=>sessionStorage.getItem(key),draftKey)).toBe('Newer draft text');
    await expect(page.locator('#connection')).not.toContainText('Brief loaded');
    expect(writes).toEqual([]);
  });
}

test('silent initial brief respects an intentional edit then clear', async ({page}) => {
  const {briefs}=await fixture(page,{draft:null});
  await expect.poll(()=>briefs.length).toBe(1); await openInput(page);
  await page.locator('#composer').fill('Deliberate edit'); await page.locator('#composer').fill('');
  await finish(briefs[0]); await settle(page);
  await expect(page.locator('#composer')).toHaveValue('');
  expect(await page.evaluate(key=>sessionStorage.getItem(key),draftKey)).toBe('');
});

test('delayed brief cannot reopen a collapsed Input drawer', async ({page}) => {
  const {briefs}=await fixture(page); await openInput(page); await load(page);
  await expect.poll(()=>briefs.length).toBe(1); await page.locator('#toggle-composer').click();
  await finish(briefs[0]); await settle(page);
  await expect(page.locator('#input-drawer')).toBeHidden();
  await expect(page.locator('#composer')).toHaveValue('Original draft');
  await expect(page.locator('#composer')).not.toBeFocused();
});

test('delayed brief ignores a hidden managed terminal view', async ({page}) => {
  const {briefs}=await fixture(page,{embedded:true}); await openInput(page); await load(page);
  await expect.poll(()=>briefs.length).toBe(1); await visibility(page,false);
  await expect(page.locator('#connection')).toHaveText('Terminal closed · session still running');
  await page.locator('#toggle-composer').focus();
  await finish(briefs[0]); await settle(page);
  await expect(page.locator('#composer')).toHaveValue('Original draft');
  await expect(page.locator('#composer')).not.toBeFocused();
  await expect(page.locator('#connection')).toHaveText('Terminal closed · session still running');
});

for (const failOld of [false,true]) test(`latest explicit brief wins over an older ${failOld?'failure':'response'}`, async ({page}) => {
  const {briefs}=await fixture(page); await openInput(page); await load(page);
  await expect.poll(()=>briefs.length).toBe(1); await load(page); await expect.poll(()=>briefs.length).toBe(2);
  await finish(briefs[1],'Newest brief'); await expect(page.locator('#composer')).toHaveValue('Original draftNewest brief');
  await finish(briefs[0],failOld?'Old request failed':'Old brief',failOld?503:200); await settle(page);
  await expect(page.locator('#composer')).toHaveValue('Original draftNewest brief');
  await expect(page.locator('#connection')).toHaveText('Brief loaded into composer; review and send when ready');
});

test('selection change alone invalidates an explicit brief', async ({page}) => {
  const {briefs}=await fixture(page); await openInput(page); await load(page);
  await expect.poll(()=>briefs.length).toBe(1);
  await page.locator('#composer').evaluate(node=>node.setSelectionRange(0,node.value.length));
  await finish(briefs[0]); await settle(page);
  await expect(page.locator('#composer')).toHaveValue('Original draft');
});

test('untouched initial brief autoload stays silent and preserves collapsed Input', async ({page}) => {
  const {briefs}=await fixture(page,{draft:null}); await expect.poll(()=>briefs.length).toBe(1);
  await finish(briefs[0]); await expect(page.locator('#composer')).toHaveValue('Stored brief');
  await expect(page.locator('#input-drawer')).toBeHidden();
  await expect(page.locator('#composer')).not.toBeFocused();
  expect(await page.evaluate(key=>sessionStorage.getItem(key),draftKey)).toBe('Stored brief');
});

test('saved empty draft skips silent brief autoload', async ({page}) => {
  const {briefs}=await fixture(page,{draft:''}); await settle(page);
  expect(briefs).toHaveLength(0); await expect(page.locator('#composer')).toHaveValue('');
  expect(await page.evaluate(key=>sessionStorage.getItem(key),draftKey)).toBe('');
});

test('unchanged explicit brief retains selected-range insertion semantics', async ({page}) => {
  const {briefs,writes}=await fixture(page); await openInput(page);
  await page.locator('#composer').evaluate(node=>node.setSelectionRange(0,8)); await load(page);
  await expect.poll(()=>briefs.length).toBe(1); await finish(briefs[0],'Replacement');
  await expect(page.locator('#composer')).toHaveValue('Replacement draft');
  await expect(page.locator('#composer')).toBeFocused(); expect(writes).toEqual([]);
});

test('brief identity refresh rejects a reused name for another durable session', async ({page}) => {
  const {briefs}=await fixture(page);
  await page.route('**/api/sessions?state=all',route=>route.fulfill({json:[{...original,id:'replacement-id'}]}));
  await load(page); await expect(page.locator('#connection')).toHaveText('Original session is no longer available');
  expect(briefs).toHaveLength(0); await expect(page.locator('#composer')).toHaveValue('Original draft');
});

test('edits while brief identity refresh is pending prevent fetching stale brief', async ({page}) => {
  const {briefs}=await fixture(page); let identity;
  await page.route('**/api/sessions?state=all',route=>{identity=route;});
  await openInput(page); await load(page); await expect.poll(()=>Boolean(identity)).toBe(true);
  await page.locator('#composer').fill('Edited before identity returned');
  await identity.fulfill({json:[original]}); await settle(page);
  expect(briefs).toHaveLength(0);
  await expect(page.locator('#composer')).toHaveValue('Edited before identity returned');
});
