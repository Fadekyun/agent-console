import { test, expect } from '@playwright/test';

const profiles = ['general', 'coder'].map(name => ({name, display_name:name, status:'active', read_write_capability:'write', worktree_requirement:'none', allowed_delegation_profiles:['coder']}));
const plans = ['one', 'two'].map(id => ({id, title:`Plan ${id}`, status:'planned', repository:'/fixture', revision_state:'current', plan:`Content ${id}`}));
function session(name) { return {id:name,tmux_name:name,tool:'shell',profile:'general',running:true,managed:true,actions:[],attention_state:'normal',last_activity:'2026-10-03T00:00:00Z',repository:'/fixture'}; }
async function fixture(page) {
  const requests = [], errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/terminal?**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Fixture terminal</title>'}));
  await page.route('**/api/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(req.method()!=='GET') requests.push({path,body:req.postDataJSON()});
    let body={};
    if(path==='/api/me') body={login:'fixture',access_surface:'local-lan',default_tool:'shell',profiles,tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',default:true,status:'ready'}]};
    else if(path==='/api/sessions') body=[session('first'),session('second')];
    else if(path==='/api/plans') body=plans;
    else if(path==='/api/delegations') body={roots:[{...session('first'),children:[]}],delegations:[]};
    else if(path==='/api/session-groups'||path==='/api/projects') body=[];
    else if(path==='/api/profiles') body=profiles;
    else if(path.startsWith('/api/profiles/')) body={...profiles.find(p=>p.name===path.split('/').at(-1)),content:`Instructions ${path.split('/').at(-1)}`};
    else if(path.startsWith('/api/plans/')&&path.endsWith('/execute')) body=session('implemented');
    else if(path.startsWith('/api/plans/')) body=plans.find(p=>p.id===path.split('/').at(-1));
    else if(path.endsWith('/attention')) body={...session(path.split('/').at(-2)),...req.postDataJSON()};
    await route.fulfill({json:body});
  });
  return {requests,errors};
}
async function openProfile(page, mobile, name) {
  await page.locator(mobile?'[data-mobile-tab="profiles"]':'[data-view="profiles"]').first().click();
  const list=page.locator(mobile?'#mobile-profiles-list':'#profiles-list');
  await list.locator(mobile?'.mobile-profile-card':'.profile-card').filter({has:page.getByText(name,{exact:true})}).getByRole('button',{name:mobile?'Edit':'Edit instructions',exact:true}).click();
}
function profileNodes(page,mobile) {
  const prefix=mobile?'mobile-':'';
  return {dialog:page.locator(`#${prefix}profile-editor-dialog`), title:page.locator(`#${prefix}profile-editor-title`), content:page.locator(`#${prefix}profile-editor-form [name=content]`), save:page.locator(`#${prefix}profile-editor-form button[type=submit]`), close:page.locator(`[data-close="${prefix}profile-editor-dialog"]`)};
}

test('legacy Refresh can be used repeatedly without an asynchronous event error',async({page})=>{
  const {errors}=await fixture(page);await page.goto('/desktop');
  await expect(page.locator('#active-sessions')).toContainText('first');
  await page.locator('#refresh').click();
  await expect(page.locator('#refresh')).toBeEnabled();
  await page.locator('#refresh').click();
  await expect(page.locator('#refresh')).toBeEnabled();
  expect(errors).toEqual([]);
});

test('New group button opens its dialog on initial and subsequent renders',async({page})=>{
  await fixture(page);await page.goto('/desktop#orchestration');
  await page.locator('#new-group-btn').click();
  await expect(page.locator('#group-dialog')).toBeVisible();
  await page.locator('[data-close="group-dialog"]').click();
  await page.locator('[data-view="sessions"]').first().click();await page.locator('#refresh').click();
  await page.locator('[data-view="orchestration"]').first().click();await page.locator('#new-group-btn').click();
  await expect(page.locator('#group-dialog')).toBeVisible();
});

test('plan execute is enabled for the next plan after a successful run',async({page})=>{
  const {requests}=await fixture(page);await page.goto('/desktop#orchestration');
  for(const id of ['one','two']) {
    await page.locator('[data-view="orchestration"]').first().click();
    await page.locator('.plan-row').filter({hasText:`Plan ${id}`}).getByRole('button').click();
    await expect(page.locator('#plan-title')).toHaveText(`Plan ${id}`);
    await expect(page.locator('#plan-form button[type=submit]')).toBeEnabled();
    await page.locator('#plan-form button[type=submit]').click();
    await expect(page.locator('#plan-dialog')).not.toBeVisible();
    await expect(page.locator('#inspector-name')).toHaveText('implemented');
  }
  expect(requests.filter(r=>r.path.endsWith('/execute')).map(r=>r.path)).toEqual(['/api/plans/one/execute','/api/plans/two/execute']);
});

for(const mobile of [false,true]) {
  test(`${mobile?'mobile':'desktop'} late profile load cannot overwrite the next profile`,async({page})=>{
    const {requests}=await fixture(page);let oldRoute;
    await page.route('**/api/profiles/general',route=>{oldRoute=route;});
    await page.goto(mobile?'/mobile':'/desktop');await openProfile(page,mobile,'general');
    const node=profileNodes(page,mobile);
    await expect.poll(()=>Boolean(oldRoute)).toBe(true);
    await node.close.click();await openProfile(page,mobile,'coder');
    await expect(node.content).toHaveValue('Instructions coder');
    await oldRoute.fulfill({json:{...profiles[0],content:'Stale general instructions'}});
    await page.waitForTimeout(100);
    await expect(node.title).toHaveText('Edit: coder');
    await expect(node.content).toHaveValue('Instructions coder');
    await node.save.click();
    await expect.poll(()=>requests.find(r=>r.path==='/api/profiles/coder')?.body?.content).toBe('Instructions coder');
  });

  test(`${mobile?'mobile':'desktop'} late save cannot close a newly opened profile editor`,async({page})=>{
    await fixture(page);let saveRoute;
    await page.route('**/api/profiles/general',route=>route.request().method()==='PUT'?(saveRoute=route):route.fallback());
    await page.goto(mobile?'/mobile':'/desktop');await openProfile(page,mobile,'general');
    const node=profileNodes(page,mobile);await expect(node.content).toHaveValue('Instructions general');
    await node.save.click();await expect.poll(()=>Boolean(saveRoute)).toBe(true);
    await node.close.click();await openProfile(page,mobile,'coder');await expect(node.content).toHaveValue('Instructions coder');
    await saveRoute.fulfill({json:{saved:true}});
    await page.waitForTimeout(1000);
    await expect(node.dialog).toBeVisible();await expect(node.title).toHaveText('Edit: coder');await expect(node.save).toBeEnabled();
  });
}

test('empty orchestration still offers a working New group control',async({page})=>{
  await fixture(page);await page.route('**/api/delegations',route=>route.fulfill({json:{roots:[],delegations:[]}}));
  await page.goto('/desktop#orchestration');await page.locator('#new-group-btn').click();
  await expect(page.locator('#group-dialog')).toBeVisible();
});

test('late plan content cannot replace the next selected execution target',async({page})=>{
  const {requests}=await fixture(page);let older;
  await page.route('**/api/plans/one',route=>{older=route;});
  await page.goto('/desktop#orchestration');
  await page.locator('.plan-row').filter({hasText:'Plan one'}).getByRole('button').click();
  await expect.poll(()=>Boolean(older)).toBe(true);
  await expect(page.locator('#plan-form button[type=submit]')).toBeDisabled();
  await page.locator('[data-close="plan-dialog"]').click();
  await page.locator('.plan-row').filter({hasText:'Plan two'}).getByRole('button').click();
  await expect(page.locator('#plan-title')).toHaveText('Plan two');
  await older.fulfill({json:plans[0]});await page.waitForTimeout(100);
  await expect(page.locator('#plan-title')).toHaveText('Plan two');
  await page.locator('#plan-form button[type=submit]').click();
  await expect.poll(()=>requests.filter(r=>r.path.endsWith('/execute')).map(r=>r.path)).toEqual(['/api/plans/two/execute']);
});

test('late attention save updates the list without selecting the previous session again',async({page})=>{
  await fixture(page);let saved;
  await page.route('**/api/sessions/first/attention',route=>{saved=route;});
  await page.goto('/desktop');
  await page.locator('.session-row').filter({hasText:'first'}).click();
  await page.locator('#attention-form button[type=submit]').click();await expect.poll(()=>Boolean(saved)).toBe(true);
  await page.locator('.session-row').filter({hasText:'second'}).click();await expect(page.locator('#inspector-name')).toHaveText('second');
  await saved.fulfill({json:{...session('first'),attention_state:'ready_for_review'}});
  await expect(page.locator('.session-row').filter({hasText:'first'})).toContainText('Ready for review');
  await expect(page.locator('#inspector-name')).toHaveText('second');
});

for(const mobile of [false,true]) test(`${mobile?'mobile':'desktop'} profile save stays disabled if instructions fail to load`,async({page})=>{
  const {requests}=await fixture(page);
  await page.route('**/api/profiles/general',route=>route.fulfill({status:503,json:{detail:'Profile loading unavailable'}}));
  await page.goto(mobile?'/mobile':'/desktop');await openProfile(page,mobile,'general');
  const node=profileNodes(page,mobile);await expect(node.dialog).toContainText('Profile loading unavailable');
  await expect(node.save).toBeDisabled();expect(requests.filter(r=>r.path.startsWith('/api/profiles/'))).toEqual([]);
});

for(const empty of [false,true]) test(`mobile group Open ${empty?'reports an empty group':'navigates to its first terminal'}`,async({page})=>{
  await fixture(page);
  await page.route('**/api/delegations',route=>route.fulfill({json:{roots:[],delegations:[]}}));
  await page.route('**/api/session-groups',route=>route.fulfill({json:[{id:'group-one',name:'Work group',member_count:2}]}));
  await page.route('**/api/session-groups/group-one/open',route=>route.fulfill({json:{available:empty?[]:[session('first'),session('second')]}}));
  const alerts=[];page.on('dialog',async d=>{alerts.push(d.message());await d.accept();});
  await page.goto('/mobile');await page.locator('[data-mobile-tab=orchestration]').click();
  await page.locator('.mobile-tree-node').filter({hasText:'Work group'}).getByRole('button',{name:'Open',exact:true}).click();
  if(empty){await expect.poll(()=>alerts).toEqual(['No running sessions in this group.']);await expect(page).toHaveURL(/\/mobile$/);}
  else await expect(page).toHaveURL(/\/terminal\?session=first$/);
});

test('mobile late plan content cannot replace the next selected execution target',async({page})=>{
  const {requests}=await fixture(page);let older;
  await page.route('**/api/plans/one',route=>{older=route;});
  page.on('dialog',d=>d.accept());
  await page.goto('/mobile');await page.locator('[data-mobile-tab=plans]').click();
  await page.locator('.mobile-plan').filter({hasText:'Plan one'}).getByRole('button',{name:'View',exact:true}).click();
  await expect.poll(()=>Boolean(older)).toBe(true);
  await expect(page.locator('#mobile-plan-start')).toBeDisabled();
  await page.locator('[data-close=mobile-plan-dialog]').click();
  await page.locator('.mobile-plan').filter({hasText:'Plan two'}).getByRole('button',{name:'View',exact:true}).click();
  await expect(page.locator('#mobile-plan-title')).toHaveText('Plan two');
  await older.fulfill({json:plans[0]});await page.waitForTimeout(100);
  await expect(page.locator('#mobile-plan-title')).toHaveText('Plan two');
  await page.locator('#mobile-plan-start').click();
  await expect.poll(()=>requests.filter(r=>r.path.endsWith('/execute')).map(r=>r.path)).toEqual(['/api/plans/two/execute']);
});

test('mobile failed plan load keeps execution disabled',async({page})=>{
  await fixture(page);
  await page.route('**/api/plans/one',route=>route.fulfill({status:503,json:{detail:'Plan unavailable'}}));
  await page.goto('/mobile');await page.locator('[data-mobile-tab=plans]').click();
  await page.locator('.mobile-plan').filter({hasText:'Plan one'}).getByRole('button',{name:'View',exact:true}).click();
  await expect(page.locator('#mobile-plan-body')).toHaveText('Plan unavailable');
  await expect(page.locator('#mobile-plan-start')).toBeDisabled();
});

for(const mobile of [false,true]) test(`${mobile?'mobile':'desktop'} initializes and changes layout when storage is unavailable`,async({page})=>{
  const {errors}=await fixture(page);
  await page.addInitScript(()=>{Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Storage denied','SecurityError');}});});
  await page.goto(mobile?'/mobile':'/desktop');
  await expect(page.locator(mobile?'#mobile-sessions':'#active-sessions')).toContainText('first');
  await page.locator(mobile?'#mobile-layout':'#layout-select').selectOption(mobile?'desktop':'mobile');
  await expect(page).toHaveURL(mobile?/\/desktop$/:/\/mobile$/);
  await expect(page.locator(mobile?'#active-sessions':'#mobile-sessions')).toContainText('first');
  expect(errors).toEqual([]);
});

test('refresh preserves an attention draft and successful save releases it',async({page})=>{
  await page.clock.install();
  const {requests}=await fixture(page);await page.goto('/desktop');
  await page.locator('.session-row').filter({hasText:'first'}).click();
  await page.locator('#attention-form [name=note]').fill('Unfinished note');
  await page.locator('#attention-form [name=state]').selectOption('ready_for_review');
  await page.clock.fastForward(10000);
  await expect(page.locator('#attention-form [name=note]')).toHaveValue('Unfinished note');
  await expect(page.locator('#attention-form [name=state]')).toHaveValue('ready_for_review');
  await page.locator('#attention-form button[type=submit]').click();
  await expect(page.locator('#attention-status')).toHaveText('State updated');
  expect(requests.find(r=>r.path.endsWith('/attention')).body).toEqual({state:'ready_for_review',note:'Unfinished note'});
  await page.clock.fastForward(10000);
  await expect(page.locator('#attention-form [name=note]')).toHaveValue('');
});

test('mobile Create ignores rapid duplicate submissions and recovers after failure',async({page})=>{
  await fixture(page);const creates=[];
  await page.route('**/api/sessions',route=>route.request().method()==='POST'?creates.push(route):route.fallback());
  await page.goto('/mobile');await page.locator('[data-mobile-tab=new]').click();
  await page.locator('#mobile-new').evaluate(form=>{form.requestSubmit();form.requestSubmit();});
  await expect.poll(()=>creates.length).toBe(1);
  await expect(page.locator('#mobile-new button[type=submit]')).toBeDisabled();
  await creates[0].fulfill({status:503,json:{detail:'Try again'}});
  await expect(page.locator('#mobile-new button[type=submit]')).toBeEnabled();
  await page.locator('#mobile-new button[type=submit]').click();
  await expect.poll(()=>creates.length).toBe(2);
  await creates[1].fulfill({json:session('created')});
  await expect(page).toHaveURL(/\/terminal\?session=created$/);
});

test('terminal dock close is a separate keyboard operable button',async({page})=>{
  await fixture(page);
  await page.route('**/api/sessions?**',route=>route.fulfill({json:[{...session('first'),actions:['attach']}]}));
  await page.goto('/desktop');await page.locator('.session-row').filter({hasText:'first'}).click();
  await page.getByRole('button',{name:'Open terminal dock',exact:true}).click();
  const close=page.getByRole('button',{name:'Close first',exact:true});
  await expect(close).toHaveJSProperty('tagName','BUTTON');
  await expect(page.locator('[role=tab] button')).toHaveCount(0);
  await close.focus();await page.keyboard.press('Enter');
  await expect(page.locator('#terminal-dock')).not.toBeVisible();
});
