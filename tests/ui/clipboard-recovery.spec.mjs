import { test, expect } from '@playwright/test';

async function open(page) {
  const writes = [];
  const session = {id:'clipboard-recovery',tmux_name:'clipboard-recovery',running:true,managed:true};
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({json: path === '/api/sessions' ? [session, {...session,id:'peer',tmux_name:'clipboard-peer'}] : path.endsWith('/review') ? {content:'Clipboard recovery output',line_count:1} : {brief:''}});
  });
  await page.routeWebSocket('**/ws/sessions/**', ws => {ws.onMessage(value => {if(Buffer.isBuffer(value)) writes.push(value.toString());}); ws.send('Clipboard recovery output\r\n');});
  await page.goto('/terminal?session=clipboard-recovery');
  await expect(page.locator('#connection')).toHaveText('Connected');
  return writes;
}
async function pendingClipboard(page) {
  await page.evaluate(() => {
    window.__clipboardReads = 0;
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:() => {window.__clipboardReads++; return new Promise(resolve => {window.__resolveClipboard = resolve;});}}});
  });
}

test('localhost clipboard API inserts a real clipboard value without sending', async ({page,context,baseURL}) => {
  test.skip(new URL(baseURL).hostname === 'console-http.test', 'Clipboard API requires secure context.');
  const writes = await open(page);
  expect(await page.evaluate(() => isSecureContext)).toBe(true);
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await page.evaluate(() => navigator.clipboard.writeText('Real clipboard\nreview first'));
  await page.locator('#paste-clipboard').click();
  await expect(page.locator('#composer')).toHaveValue('Real clipboard\nreview first');
  expect(writes).toEqual([]);
});

test('localhost copies selected terminal output through the real clipboard', async ({page,context,baseURL}) => {
  test.skip(new URL(baseURL).hostname === 'console-http.test', 'Clipboard API requires secure context.');
  const writes = await open(page);
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await page.evaluate(() => window.__terminal.select(0,0,9));
  await page.locator('#copy-selection').click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('Clipboard');
  expect(writes).toEqual([]);
});

test('pending clipboard reads do not duplicate or replace a newer draft', async ({page,baseURL}) => {
  test.skip(new URL(baseURL).hostname === 'console-http.test', 'Async clipboard is secure-context only.');
  const writes = await open(page); await pendingClipboard(page);
  await page.locator('#toggle-composer').click();
  await page.locator('#composer').fill('Original draft');
  await page.locator('#paste-clipboard').click();
  await expect(page.locator('#paste-clipboard')).toBeDisabled();
  await expect(page.locator('#paste-device')).toBeDisabled();
  await page.locator('#composer').fill('Newer draft');
  await page.evaluate(() => window.__resolveClipboard('old clipboard'));
  await expect(page.locator('#paste-clipboard')).toBeEnabled();
  await expect(page.locator('#composer')).toHaveValue('Newer draft');
  await expect(page.locator('#connection')).toContainText('Draft changed');
  expect(await page.evaluate(() => window.__clipboardReads)).toBe(1); expect(writes).toEqual([]);
});

test('closing input cancels a pending paste without reopening it', async ({page,baseURL}) => {
  test.skip(new URL(baseURL).hostname === 'console-http.test', 'Async clipboard is secure-context only.');
  const writes = await open(page); await pendingClipboard(page);
  await page.locator('#toggle-composer').click();
  await page.locator('#paste-device').click();
  await page.locator('#toggle-composer').click();
  await page.evaluate(() => window.__resolveClipboard('delayed clipboard'));
  await expect(page.locator('#paste-clipboard')).toBeEnabled();
  await expect(page.locator('#input-drawer')).toBeHidden();
  await expect(page.locator('#composer')).toHaveValue(''); expect(writes).toEqual([]);
});

test('denied clipboard permission offers native manual paste', async ({page}) => {
  const writes = await open(page);
  await page.evaluate(() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:async()=>{throw new DOMException('Denied','NotAllowedError');}}}));
  await page.locator('#paste-clipboard').click();
  await expect(page.locator('#paste-sheet-text')).toBeFocused();
  await page.locator('#paste-sheet-text').fill('Manual fallback');
  await page.locator('#use-manual-paste').click();
  await expect(page.locator('#composer')).toHaveValue('Manual fallback'); expect(writes).toEqual([]);
});

test('peer names remain selectable when both copy APIs fail', async ({page}) => {
  const writes = await open(page);
  await page.evaluate(() => {
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new DOMException('Denied','NotAllowedError');}}});
    document.execCommand=()=>{throw new DOMException('Blocked','NotAllowedError');};
  });
  await page.locator('#terminal-more > summary').click(); await page.locator('#peers').click();
  await page.getByRole('button',{name:'Copy name',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Copy session name',exact:true})).toBeVisible();
  await expect(page.locator('#copy-sheet-text')).toHaveValue('clipboard-peer');
  await expect(page.locator('#copy-sheet-text')).toBeFocused();
  await page.locator('[data-close="copy-sheet"]').click();
  await expect(page.locator('#peers-dialog')).toBeVisible(); expect(writes).toEqual([]);
});

test('IME confirmation Enter never submits the composer draft', async ({page},info) => {
  test.skip(info.project.name !== 'desktop', 'Desktop Enter submits; touch already uses explicit Send.');
  const writes = await open(page); await page.locator('#toggle-composer').click();
  await page.locator('#composer').fill('日本語 draft');
  await page.locator('#composer').dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true});
  await expect(page.locator('#composer')).toHaveValue('日本語 draft'); expect(writes).toEqual([]);
  await page.locator('#composer').press('Enter'); await expect.poll(() => writes.join('')).toBe('日本語 draft\r');
});


test('late clipboard rejection does not reopen closed text or peer dialogs', async ({page,baseURL}) => {
  test.skip(new URL(baseURL).hostname === 'console-http.test', 'Async clipboard is secure-context only.');
  await open(page);
  for (const origin of ['text', 'peers']) {
    await page.evaluate(() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:() => new Promise((resolve,reject) => {window.__rejectCopy = reject;})}}));
    if (origin === 'text') { await page.locator('#text-view').click(); await page.locator('#copy-visible').click(); }
    else { await page.locator('#terminal-more > summary').click(); await page.locator('#peers').click(); await page.getByRole('button',{name:'Copy name',exact:true}).click(); }
    await page.locator(`[data-close="${origin === 'text' ? 'text-dialog' : 'peers-dialog'}"]`).click();
    await page.evaluate(async () => { document.execCommand=()=>{window.__legacyCopyCalled=true;return false;}; window.__rejectCopy(new DOMException('Denied','NotAllowedError')); await new Promise(resolve=>setTimeout(resolve,0)); });
    await expect(page.locator('#copy-sheet')).not.toBeVisible();
    expect(await page.evaluate(() => !!window.__legacyCopyCalled)).toBe(false);
  }
});
