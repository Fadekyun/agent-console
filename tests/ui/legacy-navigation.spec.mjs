import {test,expect} from '@playwright/test';
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

async function reachable(locator,height){
  await locator.scrollIntoViewIfNeeded();const box=await locator.boundingBox();expect(box.y).toBeGreaterThanOrEqual(0);expect(box.y+box.height).toBeLessThanOrEqual(height);
  expect(await locator.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})).toBe(true);
}
test.describe('legacy responsive navigation',()=>{
 test.skip(({isMobile})=>isMobile,'Runs explicit desktop/tablet/phone dimensions once');
 for(const width of [768,1280])test(`${width}px short-screen sidebar keeps navigation and collapse reachable`,async({page})=>{
  await page.setViewportSize({width,height:400});await fixture(page);await page.goto('/desktop');
  await reachable(page.locator('#rail-toggle'),400);await page.locator('#rail-toggle').click();
  for(const [view,label] of [['sessions','Sessions'],['projects','Projects'],['profiles','Profiles'],['skills','Skills'],['jevghost','Jev Ghost'],['orchestration','Orchestration'],['new','New session']]){
   const button=page.locator(`.sidebar [data-view=${view}]`);await expect(button).toHaveAccessibleName(label);await reachable(button,400);
  }
  await reachable(page.locator('.sidebar a[href="/environment"]'),400);await reachable(page.locator('#rail-toggle'),400);await page.locator('#rail-toggle').click();
  expect(await page.locator('.sidebar').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
 });
 for(const width of [320,360,390])test(`${width}px phone reaches secondary sections through navigation and restores focus`,async({page})=>{
  await page.setViewportSize({width,height:600});const {errors}=await fixture(page);await page.route('**/api/skills',route=>route.fulfill({json:{entries:[],errors:[]}}));await page.goto('/desktop');
  const trigger=page.getByRole('button',{name:'More navigation',exact:true}),dialog=page.getByRole('dialog',{name:'More navigation',exact:true});
  for(const [view,label] of [['projects','Projects'],['profiles','Profiles'],['skills','Skills'],['jevghost','Jev Ghost']]){
   await trigger.click();await expect(trigger).toHaveAttribute('aria-expanded','true');await dialog.getByRole('button',{name:label,exact:true}).click();await expect(dialog).not.toBeVisible();await expect(page.locator(`#view-title`)).toHaveText(label);await expect(trigger).toBeFocused();await expect(trigger).toHaveAttribute('aria-expanded','false');
  }
  await trigger.click();await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();await expect(trigger).toBeFocused();
  await trigger.click();const link=dialog.getByRole('link',{name:'Environment',exact:true});await reachable(link,600);await expect(link).toHaveAttribute('href','/environment');
  await page.keyboard.press('Escape');for(const button of await page.locator('.bottom-nav button').all()){const box=await button.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);}
  expect(errors).toEqual([]);
 });
 test('short phone navigation scrolls to Environment and closes when switching to desktop',async({page})=>{
  await page.setViewportSize({width:360,height:240});await fixture(page);await page.goto('/desktop');const trigger=page.getByRole('button',{name:'More navigation',exact:true});await trigger.click();
  await reachable(page.locator('#navigation-dialog a[href="/environment"]'),240);await page.setViewportSize({width:1024,height:600});await expect(page.locator('#navigation-dialog')).not.toBeVisible();await expect(page.locator('.sidebar')).toBeVisible();
 });
});
