import {test, expect} from '@playwright/test';

async function fixture(page) {
  const requests=[];
  await page.route('**/api/**', route => {
    const path=new URL(route.request().url()).pathname;requests.push(path);
    const data={
      '/api/me':{profiles:[],tool_status:[],auth_contexts:[]},
      '/api/interface':{label:'Fixture'},
      '/api/workbench':{sessions:[],nodes:[],groups:[],aliases:{},readiness:{ready:true,warnings:[]}},
      '/api/sessions':[], '/api/plans':[], '/api/projects':[], '/api/profiles':[],
      '/api/delegations':{roots:[],delegations:[]}, '/api/skills':{entries:[],errors:[]},
    };
    return route.fulfill({json:data[path]||{items:[],results:[]}});
  });
  return requests;
}
async function icon(button, name, label) {
  await expect(button).toHaveAttribute('data-icon',name);
  await expect(button).toHaveAccessibleName(label);
  const appearance=await button.evaluate(b=>({font:getComputedStyle(b).fontSize,mask:getComputedStyle(b,'::before').maskImage,width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height}));
  expect(appearance.font).toBe('0px');expect(appearance.mask).toContain('data:image/svg+xml');
  expect(appearance.width).toBeGreaterThanOrEqual(44);expect(appearance.height).toBeGreaterThanOrEqual(44);
}

test('compact controls keep accessible names, touch size, keyboard actions and no overflow',async({page})=>{
  const requests=await fixture(page);await page.goto('/work');
  const refresh=page.getByRole('button',{name:'Refresh',exact:true});
  // Existing mobile work filters hide Refresh; opening/closing remains available everywhere.
  if(await refresh.isVisible()) {
    await icon(refresh,'refresh','Refresh');const before=requests.length;
    await refresh.focus();await page.keyboard.press('Enter');
    await expect.poll(()=>requests.length).toBeGreaterThan(before);
  }
  await expect(page.locator('#new-session')).not.toHaveAttribute('data-icon-only');
  await page.locator('#new-session').click();await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('#create-dialog')).toHaveAccessibleName('New session');
  const close=page.locator('#cancel-create');await icon(close,'close','Close');
  await close.focus();await page.keyboard.press('Enter');await expect(page.locator('#create-dialog')).not.toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('dynamic labels stay truthful and transient states return to readable text',async({page})=>{
  await fixture(page);await page.goto('/work');
  await page.evaluate(()=>{
    const host=document.createElement('div');host.id='icon-probe';
    host.innerHTML='<button id="dynamic-history">History</button><button id="dynamic-expand" data-icon="expand" data-icon-only>Full screen</button><button id="primary-send">Send</button><button id="primary-open">Open terminal</button>';
    document.querySelector('main').append(host);
  });
  const history=page.locator('#dynamic-history'),expand=page.locator('#dynamic-expand');
  await icon(history,'history','History');
  await history.evaluate(b=>{b.textContent='Loading history…';});
  await expect(history).not.toHaveAttribute('data-icon-only');await expect(history).not.toHaveAttribute('title');
  await history.evaluate(b=>{b.textContent='History';});await icon(history,'history','History');
  await expand.evaluate(b=>{b.textContent='Restore';});await icon(expand,'collapse','Restore');await expect(expand).toHaveAttribute('title','Restore');
  await expand.evaluate(b=>{b.textContent='Full screen';});await icon(expand,'expand','Full screen');await expect(expand).toHaveAttribute('title','Full screen');
  await expand.evaluate(b=>b.removeAttribute('title'));await expect(expand).toHaveAttribute('title','Full screen');
  await expect(page.locator('#primary-send')).not.toHaveAttribute('data-icon-only');await expect(page.locator('#primary-open')).not.toHaveAttribute('data-icon-only');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('legacy navigation uses SVG decoration and compact mobile refresh remains usable',async({page})=>{
  await fixture(page);await page.goto('/desktop');
  await expect(page.locator('.nav-button[data-view="skills"] [data-icon="skills"]')).toHaveAttribute('aria-hidden','true');
  if(await page.locator('.nav-button[data-view="skills"]').isVisible()) {
    await page.locator('#rail-toggle').click();
    await expect(page.locator('.nav-button[data-view="skills"]')).toHaveAccessibleName('Skills');
    await expect(page.locator('#rail-toggle')).toHaveAttribute('title','Expand navigation');
  }
  await page.goto('/mobile');const refresh=page.locator('#mobile-refresh');await icon(refresh,'refresh','Refresh');
  await refresh.click();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});


test('dialogs have named headings and compact controls reflow at 320px',async({page},info)=>{
  test.skip(info.project.name!=='samsung','One explicit narrow-viewport audit');
  await page.setViewportSize({width:320,height:740});await fixture(page);
  for(const path of ['/work','/desktop','/mobile']) {
    await page.goto(path);
    expect(await page.locator('dialog').evaluateAll(dialogs=>dialogs.every(dialog=>{
      const heading=document.getElementById(dialog.getAttribute('aria-labelledby'));
      return heading && dialog.contains(heading) && heading.textContent.trim();
    }))).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  await icon(page.locator('#mobile-refresh'),'refresh','Refresh');
  await page.goto('/work');await page.locator('#new-session').click();
  await icon(page.locator('#cancel-create'),'close','Close');
  await expect(page.locator('#create-dialog')).toHaveAccessibleName('New session');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
