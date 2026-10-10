import {test,expect} from '@playwright/test';

async function fixture(page,{label='Agent Console',currentURL=''}={}) {
  const session={id:'nav-root',tmux_name:'nav-root',tool:'shell',profile:'coder',running:false,managed:true,actions:[],attention_state:'normal'};
  const node={id:session.id,root_id:session.id,native_id:session.id,native_name:session.tmux_name,title:'Navigation fixture',owner_id:null,mechanical:'stopped',attention:'normal',result_state:'unknown',attempts:[],readiness:{}};
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    const bodies={
      '/api/me':{profiles:[],tool_status:[],auth_contexts:[]},
      '/api/interface':{label,current_url:currentURL},
      '/api/workbench':{sessions:[session],nodes:[node],groups:[{root_id:node.id,member_ids:[node.id],children_total:0,children_complete:0}],aliases:{},readiness:{ready:true,warnings:[]}},
      '/api/skill-registry':{entries:[],imports:[]},
      '/api/sessions': [session],
    };
    return route.fulfill({json:bodies[path]||{results:[],items:[],steps:[],graph:{},policy:{policy:{}}}});
  });
}
function visibleNavigation(page){return page.locator('nav:visible').filter({has:page.locator('a[href="#work"]')});}
async function settled(page){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}

test('workbench uses one brand and suppresses blank or duplicate instance labels',async({page})=>{
  for(const label of ['Agent Console','  AGENT CONSOLE  ','   ']) {
    await fixture(page,{label});await page.goto('/work');await expect(page.locator('#refresh')).toBeEnabled();
    await expect(page.locator('.top .brand')).toHaveText('Agent Console');
    await expect(page.locator('#instance')).toBeHidden();
  }
});

test('Work Skills and Settings remain reachable and identify the active route at every width',async({page})=>{
  await fixture(page);await page.goto('/work');
  for(const width of [320,760,768,1280]) {
    await page.setViewportSize({width,height:800});await settled(page);
    const nav=visibleNavigation(page);await expect(nav).toHaveCount(1);
    for(const [name,hash,panel] of [['Skills','#skills','#skills-view'],['Settings','#settings','#settings-view'],['Work','#work','#work-view']]) {
      const link=nav.getByRole('link',{name,exact:true});await expect(link).toBeVisible();
      const bounds=await link.boundingBox();expect(bounds.height).toBeGreaterThanOrEqual(44);expect(bounds.width).toBeGreaterThanOrEqual(44);
      await link.click();await expect(page).toHaveURL(new RegExp(hash+'$'));await expect(page.locator(panel)).toBeVisible();
      await expect(link).toHaveAttribute('aria-current','page');await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
    }
  }
});

test('session and results routes keep Work active and preserve browser navigation',async({page})=>{
  await fixture(page);await page.goto('/work#session/nav-root');
  await expect(page.locator('#session-view')).toBeVisible();await expect(visibleNavigation(page).getByRole('link',{name:'Work',exact:true})).toHaveAttribute('aria-current','page');
  await page.locator('#session-detail').evaluate(el=>{el.open=true;});await page.locator('#show-results').click();
  await expect(page.locator('#results-view')).toBeVisible();await expect(visibleNavigation(page).getByRole('link',{name:'Work',exact:true})).toHaveAttribute('aria-current','page');
  await visibleNavigation(page).getByRole('link',{name:'Skills',exact:true}).click();
  await page.goBack();await expect(page.locator('#results-view')).toBeVisible();
  await expect(visibleNavigation(page).getByRole('link',{name:'Work',exact:true})).toHaveAttribute('aria-current','page');
});

test('skip link focuses main without changing the current route',async({page})=>{
  await fixture(page);await page.goto('/work#skills');await expect(page.locator('#skills-view')).toBeVisible();
  await page.locator('.skip').focus();await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#skills$/);await expect(page.locator('#skills-view')).toBeVisible();await expect(page.locator('#main')).toBeFocused();
});

test('distinct long instance labels do not push navigation outside the viewport',async({page})=>{
  const label='Review-'+ 'isolated-instance-with-a-long-name-'.repeat(12);
  await fixture(page,{label,currentURL:'https://console.example/'});await page.goto('/work#settings');
  await expect(page.locator('#instance')).toHaveText(label);await expect(page.locator('#instance')).toBeVisible();
  for(const width of [320,760,768,1280]) {
    await page.setViewportSize({width,height:800});await settled(page);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    const geometry=await visibleNavigation(page).locator('a[href^="#"]').evaluateAll(elements=>elements.map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};}));
    for(const item of geometry){expect(item.left).toBeGreaterThanOrEqual(0);expect(item.right).toBeLessThanOrEqual(width);expect(item.hit).toBe(true);}
  }
  await expect(page.locator('#settings-current')).toHaveAttribute('href','https://console.example/');
  await page.getByText('More controls',{exact:true}).click();await expect(page.getByRole('link',{name:'Open full control panel'})).toBeVisible();
});
