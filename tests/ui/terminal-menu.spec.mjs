import { test, expect } from '@playwright/test';

async function terminalFixture(page, { delayed = false } = {}) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path === '/api/sessions' ? [] : {} });
  });
  await page.addInitScript(delayed => {
    window.__terminalBytes = [];
    class Socket {
      static OPEN = 1;
      constructor() {
        this.readyState = 0;
        window.__openTerminal = () => { this.readyState = 1; this.onopen?.(); };
        if (!delayed) queueMicrotask(window.__openTerminal);
      }
      send(value) { if (typeof value !== 'string') window.__terminalBytes.push([...value]); }
      close() { this.readyState = 3; }
    }
    window.WebSocket = Socket;
  }, delayed);
}
async function openMore(page) {
  await page.locator('#terminal-more > summary').click();
  await expect(page.locator('#terminal-more')).toHaveAttribute('open', '');
}
async function reachable(page, button) {
  await button.scrollIntoViewIfNeeded();
  const result = await button.evaluate(element => {
    const box = element.getBoundingClientRect();
    const menu = document.querySelector('.terminal-menu').getBoundingClientRect();
    const point = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return { top: box.top, bottom: box.bottom, width: box.width, height: box.height,
      menuTop: menu.top, menuBottom: menu.bottom, viewport: innerHeight,
      hit: element.contains(point), scroll: window.scrollY };
  });
  expect(result.hit).toBe(true);
  expect(result.top).toBeGreaterThanOrEqual(result.menuTop);
  expect(result.bottom).toBeLessThanOrEqual(result.menuBottom);
  expect(result.menuBottom).toBeLessThanOrEqual(result.viewport);
  expect(result.scroll).toBe(0);
  return result;
}

for (const {height, embed} of [{height:180,embed:false},{height:100,embed:true},{height:260,embed:true}]) {
  test(`More controls remain reachable at 360x${height}, embedded=${embed}`, async ({page}) => {
    await page.setViewportSize({width:360,height});
    await terminalFixture(page);
    await page.goto(`/terminal?session=menu-fixture${embed?'&embed=1':''}`);
    await expect(page.locator('#connection')).toHaveText('Connected');
    const terminal = await page.locator('.terminal-frame').boundingBox();
    await openMore(page);
    for (const button of await page.locator('.terminal-menu button:visible').all()) {
      const result = await reachable(page, button);
      expect(result.width).toBeGreaterThanOrEqual(44);
      expect(result.height).toBeGreaterThanOrEqual(44);
    }
    const escape = page.locator('.terminal-menu [data-key]').filter({hasText:/^Esc$/});
    await reachable(page, escape);
    await escape.click();
    await expect.poll(()=>page.evaluate(()=>window.__terminalBytes)).toEqual([[27]]);
    await page.keyboard.press('Escape');
    await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
    await expect(page.locator('#terminal-more > summary')).toBeFocused();
    expect(await page.evaluate(()=>window.__terminalBytes)).toEqual([[27]]);
    expect(await page.locator('.terminal-frame').boundingBox()).toEqual(terminal);
  });
}

test('delayed initial connection preserves focused controls and menu Escape never reaches the terminal', async ({page}) => {
  await terminalFixture(page,{delayed:true});
  await page.goto('/terminal?session=menu-fixture');
  await openMore(page);
  await page.evaluate(()=>window.__openTerminal());
  await expect(page.locator('#terminal-more > summary')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
  expect(await page.evaluate(()=>window.__terminalBytes)).toEqual([]);
  // A real terminal Escape still works once the menu is dismissed.
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.press('Escape');
  await expect.poll(()=>page.evaluate(()=>window.__terminalBytes)).toEqual([[27]]);
});

test('More closes on keyboard exit and outside pointer, and Peers restores focus without leaving a hidden open menu', async ({page}) => {
  await terminalFixture(page);
  await page.goto('/terminal?session=menu-fixture');
  await openMore(page);
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
  await openMore(page);
  await page.locator('.terminal-frame').click({position:{x:15,y:300}});
  await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
  await openMore(page);
  await page.locator('#peers').click();
  await expect(page.locator('#peers-dialog')).toBeVisible();
  await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
  await page.keyboard.press('Escape');
  await expect(page.locator('#peers-dialog')).not.toBeVisible();
  await expect(page.locator('#terminal-more > summary')).toBeFocused();
  expect(await page.evaluate(()=>window.__terminalBytes)).toEqual([]);
});

test('open More follows viewport shrink without resizing the PTY by opening the menu', async ({page}) => {
  await terminalFixture(page);
  await page.goto('/terminal?session=menu-fixture');
  await openMore(page);
  await page.setViewportSize({width:360,height:180});
  await reachable(page,page.locator('#detach'));
});


test('embedded More stays within its actual iframe and closes when focus returns to the parent', async ({page}) => {
  await terminalFixture(page);
  await page.route('**/menu-host', route => route.fulfill({contentType:'text/html',body:'<button id="parent-control">Parent control</button><iframe title="Terminal" style="display:block;width:340px;height:180px" src="/terminal?session=menu-fixture&embed=1"></iframe>'}));
  await page.goto('/menu-host');
  const frame=page.frameLocator('iframe');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await openMore(frame);
  await reachable(frame,frame.locator('#detach'));
  await page.locator('#parent-control').click();
  await expect(frame.locator('#terminal-more')).not.toHaveAttribute('open','');
});


test('wide touch screens retain 44px terminal menu controls',async({page},testInfo)=>{
  test.skip(testInfo.project.name==='desktop','Touch pointer projects exercise tablet width.');
  await page.setViewportSize({width:900,height:420});
  await terminalFixture(page);await page.goto('/terminal?session=menu-fixture&embed=1');
  await openMore(page);
  expect(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)).toBe(true);
  for(const button of await page.locator('.terminal-menu button:visible').all()){
    const box=await reachable(page,button);expect(box.width).toBeGreaterThanOrEqual(44);expect(box.height).toBeGreaterThanOrEqual(44);
  }
});

test('menu fits a panned visual viewport when the keyboard changes its visible height',async({page})=>{
  await terminalFixture(page);await page.goto('/terminal?session=menu-fixture');
  await openMore(page);
  await page.evaluate(()=>{
    const viewport=new EventTarget();Object.assign(viewport,{height:150,offsetTop:30});
    Object.defineProperty(window,'visualViewport',{configurable:true,value:viewport});
    window.dispatchEvent(new Event('resize'));
  });
  await reachable(page,page.locator('#detach'));
  const bounds=await page.locator('.terminal-menu').boundingBox();expect(bounds.y+bounds.height).toBeLessThanOrEqual(180);
});

test('initial connection does not take focus from another toolbar control',async({page})=>{
  await page.setViewportSize({width:1280,height:800});
  await terminalFixture(page,{delayed:true});await page.goto('/terminal?session=menu-fixture');
  await page.locator('#terminal-theme').focus();await page.evaluate(()=>window.__openTerminal());
  await expect(page.locator('#terminal-theme')).toBeFocused();
});
