import {test, expect} from '@playwright/test';

async function fixture(page, redraw = false) {
  const session = {id:'resize',tmux_name:'resize',tool:'shell',profile:'general',running:true,managed:true,actions:['attach'],attention_state:'normal'};
  const node = {id:'resize',root_id:'resize',owner_id:null,native_id:'resize',native_name:'resize',title:'resize',task:'',tool:'shell',profile:'general',mechanical:'running',attention:'normal',result_state:'unknown',attempts:[],readiness:{}};
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    let body = {brief:''};
    if (path === '/api/sessions') body = [session];
    else if (path === '/api/me') body = {profiles:[],tool_status:[],auth_contexts:[]};
    else if (path === '/api/workbench') body = {sessions:[session],nodes:[node],groups:[{root_id:'resize',member_ids:['resize'],priority:1,children_total:0,children_complete:0}],aliases:{},readiness:{ready:true,warnings:[]}};
    return route.fulfill({json:body});
  });
  await page.addInitScript(redraw => {
    window.__resizeSockets = []; window.__resizeInput = []; window.__resizeEvents = [];
    class Socket {
      static OPEN = 1;
      constructor() { this.readyState = 1; window.__resizeSockets.push(this); queueMicrotask(() => this.onopen?.()); }
      send(value) {
        if (typeof value !== 'string') { window.__resizeInput.push(new TextDecoder().decode(value)); return; }
        const control = JSON.parse(value);
        if (control.type !== 'resize') return;
        window.__resizeEvents.push(control);
        if (!redraw) return;
        const {rows,cols} = control;
        // A tmux-style redraw with styled text and its status row at the bottom.
        const output = `\x1b[?1049h\x1b[1;${rows}r\x1b[2J\x1b[H`
          + Array.from({length:rows-1}, (_,i) => `ROW ${String(i).padStart(2,'0')}  spaces \x1b[1mbold words\x1b[0m stay here\r\n`).join('')
          + '\x1b[30;42m' + 'TMUX STATUS'.padEnd(cols, ' ') + '\x1b[0m';
        setTimeout(() => this.onmessage?.({data:output}), 0);
      }
      close() { this.readyState = 3; }
    }
    window.WebSocket = Socket;
  }, redraw);
}

for (const scenario of ['normal', 'inactive normal', 'alternate']) {
  test(`resize backfills missing live rows in the ${scenario} buffer`, async ({page}) => {
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await fixture(page); await page.goto('/terminal?session=resize');
    await expect(page.locator('#connection')).toHaveText('Connected');
    const result = await page.evaluate(async scenario => {
      const t = window.__terminal, write = text => new Promise(resolve => t.write(text, resolve));
      t.reset(); t.resize(39,18);
      if (scenario === 'alternate') await write('\x1b[?1049h');
      await write(Array.from({length:22}, (_,i) => `line-${i}\r\n`).join('') + '\x1b[4A');
      const buffer = t._core._bufferService.buffers[scenario === 'alternate' ? 'alt' : 'normal'];
      // Upstream #6063 records this masked logical-length precondition. Force it
      // explicitly: ordinary headless resize sweeps do not reproduce its origin.
      buffer.lines.length = scenario === 'alternate' ? 12 : 18;
      const before = Array.from({length:buffer.lines.length}, (_,i) => buffer.lines.get(i).translateToString(true));
      if (scenario === 'inactive normal') await write('\x1b[?1049h');
      t.resize(37,23);
      return {length:buffer.lines.length,required:buffer.ybase+t.rows,
        preserved:before.every((line,i) => buffer.lines.get(i)?.translateToString(true) === line),
        distinct:new Set(Array.from({length:buffer.lines.length}, (_,i) => buffer.lines.get(i))).size};
    }, scenario);
    expect(result.length).toBeGreaterThanOrEqual(result.required);
    expect(result.preserved).toBe(true);
    expect(result.distinct).toBe(result.length);
    // Exercise the parser sites that otherwise throw asynchronously on a missing row.
    const bottom = await page.evaluate(async scenario => {
      const t = window.__terminal, write = text => new Promise(resolve => t.write(text, resolve));
      if (scenario === 'inactive normal') await write('\x1b[?1049l');
      for (let row = 18; row < 23; row++) await write(`\x1b[${row+1};1H\x1b[2Kbottom-${row}`);
      const b = t.buffer.active;
      return Array.from({length:5}, (_,i) => b.getLine(b.baseY+18+i).translateToString(true));
    }, scenario);
    expect(bottom).toEqual(['bottom-18','bottom-19','bottom-20','bottom-21','bottom-22']);
    expect(await page.evaluate(() => window.__resizeInput)).toEqual([]);
    expect(errors).toEqual([]);
  });
}

test('first workbench expansion repairs short buffers and preserves styled rows and a draft at Retina scale', async ({browser}, info) => {
  test.skip(info.project.name !== 'desktop', 'Workbench expansion is a desktop control.');
  const context = await browser.newContext({viewport:{width:1280,height:800},deviceScaleFactor:2});
  try {
    const page = await context.newPage(); await fixture(page, true);
    await page.goto('/work#session/resize');
    const frame = page.frameLocator('#terminal-frames iframe:not([hidden])');
    await expect(frame.locator('#connection')).toHaveText('Connected');
    const original = await page.locator('#terminal-frames iframe').elementHandle();
    await frame.locator('#toggle-composer').click();
    await frame.locator('#composer').fill('Unsent draft with spaces');
    const snapshot = () => frame.locator('body').evaluate(() => {
      const t = window.__terminal, b = t.buffer.active;
      const lines = Array.from({length:t.rows}, (_,i) => b.getLine(b.viewportY+i)?.translateToString(true).trimEnd() || '');
      const rendered = Array.from(document.querySelectorAll('.xterm-rows > div'), row => row.textContent.trimEnd());
      return {rows:t.rows,cols:t.cols,lines,rendered,sockets:window.__resizeSockets.length,input:window.__resizeInput};
    });
    await expect.poll(async () => (await snapshot()).lines.at(-1)).toBe('TMUX STATUS');
    const initial = await snapshot();
    for (let cycle = 0; cycle < 2; cycle++) {
      if (cycle === 0) await frame.locator('body').evaluate(() => {
        // Reach the same missing-row precondition on the managed Workbench path.
        window.__terminal._core._bufferService.buffers.alt.lines.length -= 5;
      });
      await page.locator('#expand-terminal').click();
      await expect.poll(async () => (await snapshot()).rows).toBeGreaterThan(initial.rows);
      await expect.poll(async () => (await snapshot()).lines.at(-1)).toBe('TMUX STATUS');
      await expect.poll(async () => { const value = await snapshot(); return value.rendered.join('\n') === value.lines.join('\n'); }).toBe(true);
      const value = await snapshot();
      expect(value.cols).toBeGreaterThan(initial.cols);
      expect(value.lines.filter(line => line === 'TMUX STATUS')).toHaveLength(1);
      for (let row = 0; row < value.rows-1; row++) expect(value.lines[row]).toBe(`ROW ${String(row).padStart(2,'0')}  spaces bold words stay here`);
      expect(value.sockets).toBe(1); expect(value.input).toEqual([]);
      expect(await original.evaluate(el => el.isConnected)).toBe(true);
      await expect(frame.locator('#composer')).toHaveValue('Unsent draft with spaces');
      await page.locator('#expand-terminal').click();
      await expect.poll(async () => (await snapshot()).rows).toBe(initial.rows);
      await expect.poll(async () => (await snapshot()).lines.at(-1)).toBe('TMUX STATUS');
    }
  } finally { await context.close(); }
});

// A circular buffer at capacity recycles rows, so blindly appending could loop.
test('impossible alternate buffer bounds fail without trimming saved rows', async ({page}) => {
  await fixture(page); await page.goto('/terminal?session=resize');
  await expect(page.locator('#connection')).toHaveText('Connected');
  const result = await page.evaluate(async () => {
    const {guardTerminalResize} = await import('/static/terminal-resize.js');
    const t = window.__terminal;
    await new Promise(resolve => t.write('\x1b[?1049hPRESERVE THIS', resolve));
    const buffer = t._core._bufferService.buffers.alt;
    const before = Array.from({length:buffer.lines.length}, (_,i) => buffer.lines.get(i));
    buffer.ybase = 1;
    let error = '';
    try { guardTerminalResize(t); } catch (e) { error = e.message; }
    const preserved = before.length === buffer.lines.length && before.every((line,i) => line === buffer.lines.get(i));
    t.reset();
    return {error,preserved};
  });
  expect(result.error).toContain('Unsupported xterm buffer bounds');
  expect(result.preserved).toBe(true);
});
