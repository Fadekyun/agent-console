import {test, expect} from '@playwright/test';

async function fixture(page) {
  const session = {id:'resize',tmux_name:'resize',tool:'shell',profile:'general',running:true,managed:true,actions:['attach'],attention_state:'normal'};
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    let body = {brief:''};
    if (path === '/api/sessions') body = [session];
    else if (path === '/api/me') body = {profiles:[],tool_status:[],auth_contexts:[]};
    else if (path === '/api/delegations') body = {roots:[{...session,children:[]}],delegations:[]};
    else if (['/api/projects','/api/plans','/api/session-groups','/api/profiles'].includes(path)) body = [];
    return route.fulfill({json:body});
  });
  await page.addInitScript(() => {
    window.__resizeSockets = [];
    window.__resizeInput = [];
    class Socket {
      static OPEN = 1;
      constructor() { this.readyState = 1; window.__resizeSockets.push(this); queueMicrotask(() => this.onopen?.()); }
      send(value) { if (typeof value !== 'string') window.__resizeInput.push(new TextDecoder().decode(value)); }
      close() { this.readyState = 3; }
    }
    window.WebSocket = Socket;
  });
}
async function write(target, data) {
  await target.locator('body').evaluate((_, output) => new Promise(resolve => {
    window.__resizeSockets.at(-1).onmessage({data:output});
    window.__terminal.write('', resolve);
  }), data);
}
async function visible(target) {
  return target.locator('body').evaluate(() => {
    const t = window.__terminal, b = t.buffer.active;
    return Array.from({length:t.rows}, (_, i) => b.getLine(b.viewportY + i)?.translateToString(true) || '').join('\n');
  });
}
async function redraw(target) {
  const rows = await target.locator('body').evaluate(() => window.__terminal.rows);
  // tmux enters its alternate screen only after the dock has fitted below 24 rows.
  // Scrolling/redrawing is valid terminal output, not a second submitted input.
  await write(target, '\x1b[?1049h' + `\x1b[${rows};1H` + 'USER-DRAFT\r\n'.repeat(rows)
    + '\x1b[2J\x1b[H' + 'current output\r\n'.repeat(rows - 1) + 'USER-DRAFT');
  expect((await visible(target)).match(/USER-DRAFT/g)).toHaveLength(1);
}

test('maximizing a short dock does not resurrect old prompt redraws', async ({page}, info) => {
  test.skip(info.project.name !== 'desktop', 'The desktop dock owns native fullscreen.');
  await page.setViewportSize({width:1280,height:800});
  await fixture(page); await page.goto('/desktop');
  await page.locator('#active-sessions [data-attach]').click();
  const frame = page.frameLocator('#terminal-frames iframe');
  const original = await page.locator('#terminal-frames iframe').elementHandle();
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await frame.locator('#toggle-composer').click();
  await frame.locator('#composer').fill('Unsent draft stays here');
  for (let cycle = 0; cycle < 3; cycle++) {
    await expect.poll(() => frame.locator('body').evaluate(() => window.__terminal.rows)).toBeLessThan(24);
    await redraw(frame);
    await page.locator('#terminal-dock-fullscreen').click();
    await expect.poll(() => frame.locator('body').evaluate(() => window.__terminal.rows)).toBeGreaterThan(24);
    expect((await visible(frame)).match(/USER-DRAFT/g)).toHaveLength(1);
    expect(await frame.locator('body').evaluate(() => window.__terminal.buffer.active.baseY)).toBe(0);
    await expect(frame.locator('#composer')).toHaveValue('Unsent draft stays here');
    expect(await original.evaluate(el => el.isConnected)).toBe(true);
    expect(await frame.locator('body').evaluate(() => window.__resizeSockets.length)).toBe(1);
    expect(await frame.locator('body').evaluate(() => window.__resizeInput)).toEqual([]);
    if (cycle === 0) await page.screenshot({path:info.outputPath('maximized.png')});
    // Returning to the normal screen empties alt. A later shrink must fix its
    // capacity again, even though the first attach was already correct.
    await write(frame, '\x1b[?1049l');
    await page.locator('#terminal-dock-fullscreen').click();
  }
  await frame.locator('.xterm-helper-textarea').focus();
  await page.keyboard.type('next input');
  expect(await frame.locator('body').evaluate(() => window.__resizeInput.join(''))).toBe('next input');
});

test('viewport resize preserves alternate redraws and normal scrollback', async ({page}) => {
  await fixture(page); await page.setViewportSize({width:900,height:800});
  await page.goto('/terminal?session=resize');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await write(page, Array.from({length:60}, (_, i) => `HISTORY-${String(i).padStart(2,'0')}\r\n`).join(''));
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.setViewportSize({width:900,height:320});
    await expect.poll(() => page.evaluate(() => window.__terminal.rows)).toBeLessThan(24);
    await redraw(page);
    await page.setViewportSize({width:1280,height:900});
    await expect.poll(() => page.evaluate(() => window.__terminal.rows)).toBeGreaterThan(24);
    expect((await visible(page)).match(/USER-DRAFT/g)).toHaveLength(1);
    expect(await page.evaluate(() => window.__terminal.buffer.active.baseY)).toBe(0);
    await write(page, '\x1b[?1049l');
    const history = await page.evaluate(() => {
      const b = window.__terminal.buffer.normal;
      return Array.from({length:b.length}, (_, i) => b.getLine(i).translateToString(true)).join('\n');
    });
    for (let i = 0; i < 60; i++) expect(history.match(new RegExp(`HISTORY-${String(i).padStart(2,'0')}`, 'g'))).toHaveLength(1);
    expect(await page.evaluate(() => window.__resizeInput)).toEqual([]);
  }
});
