import { test, expect } from '@playwright/test';

test('write-only environment controls retain scope and support rotation and suppression', async ({page}) => {
  const changes = [];
  await page.route('**/api/projects', route => route.fulfill({json:[{id:'project-one',name:'One'}]}));
  await page.route('**/api/environment**', async route => {
    const request = route.request();
    if (request.method() !== 'GET') changes.push({url:request.url(),method:request.method(),body:request.postDataJSON()});
    await route.fulfill({json:{entries:[{name:'EXISTING_KEY',state:'enabled',scope:'global'}],effective:[{name:'EXISTING_KEY',source:'global'}],host_names:['HOST_KEY'],sessions:[{name:'session',status:'detached',revision:1,refresh_required:true}]}});
  });
  await page.goto('/environment');
  await expect(page.getByRole('heading',{name:'Environment',exact:true})).toBeVisible();
  await page.locator('#environment-scope').selectOption('project-one');
  await page.locator('#environment-name').fill('APP_KEY');
  await page.locator('#environment-value').fill('synthetic-secret-value');
  await page.getByRole('button',{name:'Save variable'}).click();
  await expect(page.locator('#environment-value')).toHaveValue('');
  await expect(page.locator('#environment-message')).toContainText('Environment saved');
  expect(changes[0].url).toContain('project_id=project-one');
  expect(changes[0].body).toEqual({state:'enabled',value:'synthetic-secret-value'});
  await expect(page.locator('body')).not.toContainText('synthetic-secret-value');
  await page.getByRole('button',{name:'Suppress inherited variable'}).click();
  await expect(page.locator('#environment-message')).toContainText('Environment saved');
  expect(changes.at(-1).body).toEqual({state:'suppressed'});
  await page.getByRole('button',{name:'Disable',exact:true}).click();
  await expect.poll(()=>changes.at(-1)?.body?.state).toBe('disabled');
  await expect(page.locator('#environment-sessions')).toContainText('Restart to refresh');
  await page.locator('#environment-multiline').check();
  await page.locator('#environment-multiline-value').fill('line one\nline two');
  await page.getByRole('button',{name:'Save variable'}).click();
  await expect.poll(()=>changes.at(-1)?.body?.value).toBe('line one\nline two');
  await expect(page.locator('#environment-multiline-value')).toHaveValue('');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

const environmentData = name => ({entries:[{name,state:'enabled'}],effective:[{name,source:'project'}],host_names:[],sessions:[]});
async function scopes(page) {
  await page.route('**/api/projects',route=>route.fulfill({json:[{id:'a',name:'A'},{id:'b',name:'B'}]}));
}
test('environment scope switch removes stale actions and failed loads can retry safely',async({page})=>{
  await scopes(page);const changes=[];let release,fail=true;
  await page.route('**/api/environment**',async route=>{
    const request=route.request(),scope=new URL(request.url()).searchParams.get('project_id');
    if(request.method()!=='GET'){changes.push(request.url());return route.fulfill({json:{ok:true}});}
    if(scope==='b'&&fail){await new Promise(resolve=>{release=resolve;});return route.fulfill({status:503,json:{detail:'B unavailable'}});}
    return route.fulfill({json:environmentData(scope==='b'?'B_KEY':'A_KEY')});
  });
  await page.goto('/environment?project_id=a');await expect(page.locator('#environment-entries')).toContainText('A_KEY');
  await page.getByRole('button',{name:'Delete',exact:true}).evaluate(node=>{window.staleEnvironmentDelete=node.onclick;});
  await page.locator('#environment-scope').selectOption('b');
  await expect(page.locator('#environment-entries')).not.toContainText('A_KEY');
  await expect(page.getByRole('button',{name:'Save variable'})).toBeDisabled();
  await page.evaluate(()=>window.staleEnvironmentDelete());expect(changes).toEqual([]);
  await expect.poll(()=>typeof release).toBe('function');release();
  await expect(page.locator('#environment-message')).toHaveText('B unavailable');
  await expect(page.locator('#environment-name')).toBeDisabled();
  fail=false;await page.getByRole('button',{name:'Retry loading variables'}).click();
  await expect(page.locator('#environment-entries')).toContainText('B_KEY');
  await page.getByRole('button',{name:'Disable',exact:true}).click();
  await expect.poll(()=>changes.length).toBe(1);expect(changes[0]).toContain('/B_KEY?project_id=b');
});
test('environment ignores responses from old scopes and late saves',async({page})=>{
  await scopes(page);let releaseLoad,releaseSave;const changes=[];
  await page.route('**/api/environment**',async route=>{
    const request=route.request(),scope=new URL(request.url()).searchParams.get('project_id');
    if(request.method()!=='GET'){
      changes.push(request.url());await new Promise(resolve=>{releaseSave=resolve;});
      return route.fulfill({status:409,json:{detail:'Stale A save failed'}});
    }
    if(scope==='b')await new Promise(resolve=>{releaseLoad=resolve;});
    return route.fulfill({json:environmentData(scope==='b'?'B_KEY':'A_KEY')});
  });
  await page.goto('/environment?project_id=a');await expect(page.locator('#environment-entries')).toContainText('A_KEY');
  await page.locator('#environment-scope').selectOption('b');await expect.poll(()=>typeof releaseLoad).toBe('function');
  await page.locator('#environment-scope').selectOption('a');await expect(page.locator('#environment-entries')).toContainText('A_KEY');
  releaseLoad();await expect(page.locator('#environment-name')).toBeEnabled();
  await page.getByRole('button',{name:'Disable',exact:true}).click();await expect.poll(()=>typeof releaseSave).toBe('function');
  expect(changes[0]).toContain('/A_KEY?project_id=a');await expect(page.locator('#environment-scope')).toBeDisabled();
  // Exercise stale completion guards even if a scope change is triggered outside the disabled selector.
  releaseLoad=null;await page.locator('#environment-scope').evaluate(node=>{node.value='b';node.dispatchEvent(new Event('change'));});
  await expect.poll(()=>typeof releaseLoad).toBe('function');releaseLoad();
  await expect(page.locator('#environment-entries')).toContainText('B_KEY');releaseSave();
  await expect(page.locator('#environment-scope')).toBeEnabled();
  await expect(page.locator('#environment-message')).not.toContainText('Stale A');
  await expect(page.locator('#environment-entries')).not.toContainText('A_KEY');
});

test('failed environment save retains its draft for a safe retry and clears after success',async({page})=>{
  await scopes(page);const writes=[];
  await page.route('**/api/environment**',async route=>{
    if(route.request().method()==='GET')return route.fulfill({json:environmentData('APP_KEY')});
    writes.push(route.request().postDataJSON());
    return writes.length===1
      ?route.fulfill({status:503,json:{detail:'Temporary save failure'}})
      :route.fulfill({json:{ok:true}});
  });
  await page.goto('/environment?project_id=a');
  await page.locator('#environment-name').fill('APP_KEY');
  await page.locator('#environment-value').fill('synthetic-retry-value');
  await page.getByRole('button',{name:'Save variable'}).click();
  await expect(page.locator('#environment-message')).toHaveText('Temporary save failure');
  await expect(page.locator('#environment-value')).toHaveValue('synthetic-retry-value');
  await expect(page.getByRole('button',{name:'Save variable'})).toBeEnabled();
  await page.getByRole('button',{name:'Save variable'}).click();
  await expect(page.locator('#environment-message')).toContainText('Environment saved');
  await expect(page.locator('#environment-value')).toHaveValue('');
  expect(writes).toEqual(Array(2).fill({value:'synthetic-retry-value',state:'enabled'}));
});

test('failed suppression preserves a multiline draft and successful suppression clears it',async({page})=>{
  await scopes(page);const writes=[];
  await page.route('**/api/environment**',async route=>{
    if(route.request().method()==='GET')return route.fulfill({json:environmentData('APP_KEY')});
    writes.push(route.request().postDataJSON());
    return writes.length===1
      ?route.fulfill({status:503,json:{detail:'Temporary suppression failure'}})
      :route.fulfill({json:{ok:true}});
  });
  await page.goto('/environment?project_id=a');
  await page.locator('#environment-name').fill('APP_KEY');
  await page.locator('#environment-multiline').check();
  await page.locator('#environment-multiline-value').fill('synthetic line one\nsynthetic line two');
  await page.getByRole('button',{name:'Suppress inherited variable'}).click();
  await expect(page.locator('#environment-message')).toHaveText('Temporary suppression failure');
  await expect(page.locator('#environment-multiline-value')).toHaveValue('synthetic line one\nsynthetic line two');
  await page.getByRole('button',{name:'Suppress inherited variable'}).click();
  await expect(page.locator('#environment-message')).toContainText('Environment saved');
  await expect(page.locator('#environment-multiline-value')).toHaveValue('');
  expect(writes).toEqual(Array(2).fill({state:'suppressed'}));
});

test('successful environment write clears the secret even if refreshing metadata fails',async({page})=>{
  await scopes(page);let saved=false;
  await page.route('**/api/environment**',async route=>{
    if(route.request().method()!=='GET'){saved=true;return route.fulfill({json:{ok:true}});}
    return saved?route.fulfill({status:503,json:{detail:'Metadata refresh failed'}}):route.fulfill({json:environmentData('APP_KEY')});
  });
  await page.goto('/environment');
  await page.locator('#environment-name').fill('APP_KEY');
  await page.locator('#environment-value').fill('synthetic-success-value');
  await page.getByRole('button',{name:'Save variable'}).click();
  await expect(page.locator('#environment-message')).toHaveText('Metadata refresh failed');
  await expect(page.locator('#environment-value')).toHaveValue('');
  await expect(page.getByRole('button',{name:'Retry loading variables'})).toBeVisible();
});

test('late successful save cannot clear a draft belonging to a new scope',async({page})=>{
  await scopes(page);let releaseSave;
  await page.route('**/api/environment**',async route=>{
    if(route.request().method()==='GET')return route.fulfill({json:environmentData('APP_KEY')});
    await new Promise(resolve=>{releaseSave=resolve;});return route.fulfill({json:{ok:true}});
  });
  await page.goto('/environment?project_id=a');
  await page.locator('#environment-name').fill('APP_KEY');
  await page.locator('#environment-value').fill('synthetic-old-scope-value');
  await page.getByRole('button',{name:'Save variable'}).click();
  await expect.poll(()=>typeof releaseSave).toBe('function');
  // The selector is disabled for users, but external scope changes must still be guarded.
  await page.locator('#environment-scope').evaluate(node=>{node.value='b';node.dispatchEvent(new Event('change'));});
  await expect(page.locator('#environment-entries')).toContainText('APP_KEY');
  await page.locator('#environment-value').evaluate(node=>{node.value='synthetic-new-scope-value';});
  releaseSave();
  await expect(page.locator('#environment-scope')).toBeEnabled();
  await expect(page.locator('#environment-value')).toHaveValue('synthetic-new-scope-value');
  await expect(page.locator('#environment-message')).not.toContainText('Environment saved');
});
