import {test, expect} from '@playwright/test';

async function fixture(page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const bodies = {
      '/api/me': {profiles:[], tool_status:[], auth_contexts:[], login:'fixture'},
      '/api/projects': [], '/api/profiles': [], '/api/sessions': [{id:'theme-controls',tmux_name:'theme-controls'}],
      '/api/workbench': {sessions:[],nodes:[],groups:[],aliases:{},readiness:{ready:true,warnings:[]}},
    };
    return route.fulfill({json:bodies[path] || {brief:'',results:[],items:[]}});
  });
  let connection;
  await page.routeWebSocket('**/ws/sessions/**', socket => {connection=socket;socket.send('Keep this output\r\n');});
  return text=>connection.send(text);
}
async function contrast(locator, property='color') {
  return locator.evaluate((element, prop) => {
    const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
    const context=canvas.getContext('2d');
    function rgba(color) {context.clearRect(0,0,1,1);context.fillStyle=color;context.fillRect(0,0,1,1);return [...context.getImageData(0,0,1,1).data];}
    function over(a,b) {const alpha=a[3]/255;return a.slice(0,3).map((c,i)=>c*alpha+b[i]*(1-alpha)).concat(255);}
    function background(el) {if(!el)return [255,255,255,255];const c=rgba(getComputedStyle(el).backgroundColor);return c[3]===255?c:over(c,background(el.parentElement));}
    function luminance(c){const a=c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return a[0]*.2126+a[1]*.7152+a[2]*.0722;}
    const bg=background(prop==='outlineColor'?element.parentElement:element);
    const fg=over(rgba(getComputedStyle(element)[prop]),bg);
    const a=luminance(fg),b=luminance(bg);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
  }, property);
}

test('hovered primary actions remain readable in every palette and mode', async ({page}) => {
  await fixture(page);await page.goto('/terminal?session=theme-controls');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await page.locator('#toggle-composer').click();
  for(const palette of ['forest','ocean','violet'])for(const mode of ['light','dark']) {
    await page.evaluate(value=>window.AgentConsoleAppearance.set(value),{palette,mode});
    await page.locator('#send-enter').hover();
    expect(await contrast(page.locator('#send-enter')),`${palette} ${mode} hovered Send`).toBeGreaterThanOrEqual(4.5);
  }
});

test('mobile settings links and keyboard focus follow the selected palette', async ({page}) => {
  await fixture(page);await page.goto('/mobile');
  await page.locator('.appearance-controls summary').click();
  const link=page.getByRole('link',{name:'Environment variables'});
  for(const palette of ['forest','ocean','violet'])for(const mode of ['light','dark']) {
    await page.evaluate(value=>window.AgentConsoleAppearance.set(value),{palette,mode});
    expect(await contrast(link),`${palette} ${mode} settings link`).toBeGreaterThanOrEqual(4.5);
  }
  await page.locator('#mobile-refresh').focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  expect(await page.locator('#mobile-refresh').evaluate(el=>el.matches(':focus-visible'))).toBe(true);
  expect(await contrast(page.locator('#mobile-refresh'),'outlineColor')).toBeGreaterThanOrEqual(3);
});

test('appearance is accessible in embedded terminal More without discarding output or draft', async ({page}) => {
  await fixture(page);await page.goto('/terminal?session=theme-controls&embed=1');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await page.locator('#toggle-composer').click();await page.locator('#composer').fill('Unsent draft');
  await page.locator('#terminal-more summary').click();
  await expect(page.locator('#terminal-theme')).toBeVisible();
  await page.locator('#terminal-theme').selectOption('dark');
  await page.locator('[data-palette-select]').selectOption('violet');
  await expect(page.locator('html')).toHaveAttribute('data-palette','violet');
  await expect(page.locator('#composer')).toHaveValue('Unsent draft');
  await page.keyboard.press('Escape');await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
  await expect(page.locator('#terminal-more summary')).toBeFocused();
  expect(await page.evaluate(()=>window.__terminal.buffer.active.getLine(0).translateToString())).toContain('Keep this output');
  await page.reload();await page.locator('#terminal-more summary').click();
  await expect(page.locator('#terminal-theme')).toHaveValue('dark');
  await expect(page.locator('[data-palette-select]')).toHaveValue('violet');
});


test('terminal appearance controls stay reachable above a short mobile viewport', async ({page}) => {
  await fixture(page);await page.setViewportSize({width:360,height:240});
  await page.goto('/terminal?session=theme-controls&embed=1');await expect(page.locator('#connection')).toHaveText('Connected');
  await page.locator('#terminal-more summary').click();
  const palette=page.locator('[data-palette-select]');await palette.scrollIntoViewIfNeeded();
  const box=await palette.boundingBox();expect(box.y).toBeGreaterThanOrEqual(0);expect(box.y+box.height).toBeLessThanOrEqual(240);
  expect(await palette.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})).toBe(true);
  await palette.selectOption('ocean');await page.keyboard.press('Escape');
  await expect(page.locator('#terminal-more summary')).toBeFocused();
  await expect(page.locator('#terminal-more')).not.toHaveAttribute('open','');
});

test('hovered scroll and unread actions remain readable in every palette', async ({page}) => {
  const send=await fixture(page);await page.goto('/terminal?session=theme-controls');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await page.evaluate(()=>new Promise(resolve=>window.__terminal.write(Array.from({length:100},(_,i)=>`Output ${i}\r\n`).join(''),resolve)));
  await page.evaluate(()=>window.__terminal.scrollToTop());
  const action=page.locator('#new-output');await expect(action).toBeVisible();
  for(const unread of [false,true]) {
    if(unread) {
      send('Unread output\r\n');
      await expect(action).toHaveClass(/has-unread/);
    }
    for(const palette of ['forest','ocean','violet'])for(const mode of ['light','dark']) {
      await page.evaluate(value=>window.AgentConsoleAppearance.set(value),{palette,mode});
      await action.hover();expect(await contrast(action),`${palette} ${mode} unread=${unread}`).toBeGreaterThanOrEqual(4.5);
    }
  }
});

test('composer Paste remains an accessible unclipped icon at phone and keyboard heights', async ({page}) => {
  await fixture(page);
  for(const [width,height,embedded] of [[1280,800,true],[360,800,true],[360,180,false]]) {
    await page.setViewportSize({width,height});await page.goto(`/terminal?session=theme-controls${embedded?'&embed=1':''}`);
    await expect(page.locator('#connection')).toHaveText('Connected');
    if(await page.locator('#toggle-composer').getAttribute('aria-expanded')!=='true') await page.locator('#toggle-composer').click();
    const paste=page.locator('#paste-device');await paste.scrollIntoViewIfNeeded();
    await expect(paste).toHaveAccessibleName('Paste from device');
    const metrics=await paste.evaluate(el=>{
      const s=getComputedStyle(el),icon=getComputedStyle(el,'::before'),r=el.getBoundingClientRect();
      return {font:s.fontSize,width:r.width,height:r.height,iconWidth:parseFloat(icon.width),iconHeight:parseFloat(icon.height),
        availableWidth:el.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight),availableHeight:el.clientHeight-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom),
        hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
    });
    expect(metrics.font).toBe('0px');expect(metrics.width).toBeGreaterThanOrEqual(44);expect(metrics.height).toBeGreaterThanOrEqual(44);
    expect(metrics.iconWidth).toBeGreaterThan(0);expect(metrics.iconWidth).toBeLessThanOrEqual(metrics.availableWidth);
    expect(metrics.iconHeight).toBeLessThanOrEqual(metrics.availableHeight);expect(metrics.hit).toBe(true);
  }
});
