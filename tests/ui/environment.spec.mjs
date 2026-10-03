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
