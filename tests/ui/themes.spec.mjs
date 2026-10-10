import { test, expect } from '@playwright/test';

async function fixture(page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const bodies = {
      '/api/me': {profiles:[],tool_status:[],auth_contexts:[]},
      '/api/interface': {label:'Test'},
      '/api/sessions': [{id:'theme-probe-id',tmux_name:'theme-probe'}],
      '/api/workbench': {sessions:[],nodes:[],groups:[],aliases:{},readiness:{ready:true,warnings:[]}},
    };
    return route.fulfill({json:bodies[path] || {brief:'',results:[],items:[]}});
  });
}
const state = page => page.evaluate(() => ({...document.documentElement.dataset}));

test('all palettes and appearances persist and provide readable semantic colours', async ({page}) => {
  await fixture(page);
  await page.goto('/work#settings');
  for (const palette of ['forest','ocean','violet']) {
    await page.locator('[data-palette-select]').selectOption(palette);
    for (const mode of ['light','dark']) {
      await page.locator('#theme').selectOption(mode);
      expect(await state(page)).toMatchObject({palette,theme:mode,colorMode:mode});
      const contrast = await page.evaluate(() => {
        const styles = getComputedStyle(document.documentElement);
        function luminance(name) {
          const hex = styles.getPropertyValue(name).trim().replace('#','');
          const values = hex.match(/../g).map(x => parseInt(x,16)/255).map(x => x <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4);
          return values[0]*.2126+values[1]*.7152+values[2]*.0722;
        }
        return [['--text','--surface'],['--muted','--surface'],['--accent','--accent-foreground'],['--terminal-text','--terminal-bg']].map(([a,b]) => {
          const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
        });
      });
      contrast.forEach(value => expect(value).toBeGreaterThanOrEqual(4.5));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
  await page.reload();
  await expect(page.locator('#theme')).toHaveValue('dark');
  await expect(page.locator('[data-palette-select]')).toHaveValue('violet');
  await page.locator('#theme').selectOption('system');
  await page.emulateMedia({colorScheme:'light'});
  await expect(page.locator('html')).toHaveAttribute('data-color-mode','light');
  await page.emulateMedia({colorScheme:'dark'});
  await expect(page.locator('html')).toHaveAttribute('data-color-mode','dark');
});

test('open terminal and other tab synchronize without reconnect or losing terminal contents', async ({page,context}) => {
  await fixture(page);let connections=0;
  await page.routeWebSocket('**/ws/sessions/**', socket => { connections++;socket.send('Preserved terminal output\r\n'); });
  await page.goto('/work#settings');
  await page.evaluate(() => {const frame=document.createElement('iframe');frame.id='theme-probe';frame.src='/terminal?session=theme-probe';frame.style.height='400px';document.body.append(frame);});
  const frame=page.frameLocator('#theme-probe');
  await expect(frame.locator('#connection')).toHaveText('Connected');
  await expect(frame.locator('.xterm-screen')).toBeVisible();
  const originalConnections=connections;
  const tab=await context.newPage();await fixture(tab);await tab.goto('/work#settings');
  await page.locator('[data-palette-select]').selectOption('ocean');
  await expect(frame.locator('html')).toHaveAttribute('data-palette','ocean');
  await expect(tab.locator('[data-palette-select]')).toHaveValue('ocean');
  await page.locator('#theme').selectOption('dark');
  await expect(frame.locator('html')).toHaveAttribute('data-color-mode','dark');
  const terminalState=await page.frames().find(f=>f.url().includes('/terminal?')).evaluate(async () => {
    const {xtermTheme}=await import('/static/theme.js?v=10');
    const term=window.__terminal?.terminal || window.__terminal;
    return {theme:xtermTheme(), output:term?.buffer?.active?.getLine(0)?.translateToString?.()};
  });
  expect(terminalState.theme.magenta).toBe('#c9a1e8');
  expect(terminalState.output).toContain('Preserved terminal output');
  expect(connections).toBe(originalConnections);
  await tab.close();
});

test('invalid preferences and unavailable storage do not break theme initialization', async ({page}) => {
  await fixture(page);
  await page.addInitScript(() => {
    localStorage.setItem('agent-console-theme','invalid');
    localStorage.setItem('agent-console-palette','invalid');
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = function(key) { if(key.startsWith('agent-console-'))throw new DOMException('denied','SecurityError');return original.call(this,key); };
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key,value) { if(key.startsWith('agent-console-'))throw new DOMException('denied','SecurityError');return set.call(this,key,value); };
  });
  await page.goto('/work#settings');
  await expect(page.locator('[data-palette-select]')).toHaveValue('forest');
  await page.locator('[data-palette-select]').selectOption('violet');
  await expect(page.locator('html')).toHaveAttribute('data-palette','violet');
  await page.locator('#theme').selectOption('dark');
  await expect(page.locator('html')).toHaveAttribute('data-color-mode','dark');
});
