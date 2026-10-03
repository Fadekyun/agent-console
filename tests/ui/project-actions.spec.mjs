import {test, expect} from '@playwright/test';

async function mount(page) {
  await page.goto('/desktop');
  await page.evaluate(async () => {
    document.body.innerHTML = '<main><div id="actions"></div></main>';
    const {projectActions} = await import('/static/project-actions.js?v=1');
    window.changes = []; window.refreshed = 0;
    document.querySelector('#actions').append(projectActions({id:'project-a',name:'Project A',repository:'/workspace/a',description:'Current description',status:'active'}, {
      api: async (url, options) => {
        window.changes.push({url, method:options.method, body:options.body && JSON.parse(options.body)});
        if (window.apiError) throw new Error(window.apiError);
        if (window.delayRequest) await new Promise(resolve => {window.finishRequest = resolve;});
        return {};
      }, refresh: async () => { window.refreshed++; }
    }));
  });
  await page.getByRole('button',{name:'Edit project',exact:true}).click();
}

test('project editing saves name, repository, description and status with one pending request', async ({page}) => {
  await mount(page);
  const dialog=page.getByRole('dialog',{name:'Edit project'});
  await page.getByLabel('Name',{exact:true}).fill('Renamed project');
  await page.getByLabel('Description').fill('Updated description');
  await page.getByLabel('Status',{exact:true}).selectOption('paused');
  await page.evaluate(()=>{window.delayRequest=true;});
  await page.getByRole('button',{name:'Save project'}).click();
  await expect(page.getByRole('button',{name:'Save project'})).toBeDisabled();
  await page.keyboard.press('Escape'); await expect(dialog).toBeVisible();
  expect(await page.evaluate(()=>window.changes)).toEqual([{url:'/api/projects/project-a',method:'PUT',body:{name:'Renamed project',repository:'/workspace/a',description:'Updated description',status:'paused'}}]);
  await page.evaluate(()=>window.finishRequest());
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(()=>window.refreshed)).toBe(1);
  await expect(page.getByRole('button',{name:'Edit project',exact:true})).toBeFocused();
});

test('project delete requires confirmation and retains actionable backend errors', async ({page}) => {
  await mount(page);
  await page.getByRole('button',{name:'Delete project',exact:true}).click();
  expect(await page.evaluate(()=>window.changes.length)).toBe(0);
  await expect(page.getByRole('button',{name:'Keep project'})).toBeFocused();
  await page.getByRole('button',{name:'Keep project'}).click();
  await expect(page.getByRole('button',{name:'Delete permanently'})).toBeHidden();
  await page.getByRole('button',{name:'Delete project',exact:true}).click();
  await page.evaluate(()=>{window.apiError='Unassign sessions before deletion';});
  await page.getByRole('button',{name:'Delete permanently'}).click();
  await expect(page.getByRole('status')).toContainText('Unassign sessions before deletion');
  await expect(page.getByRole('button',{name:'Delete permanently'})).toBeEnabled();
  await page.evaluate(()=>{window.apiError=null;});
  await page.getByRole('button',{name:'Delete permanently'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await page.evaluate(()=>window.changes.map(x=>x.method))).toEqual(['DELETE','DELETE']);
});

test('project editor fits a 320px viewport and supports keyboard cancellation', async ({page}) => {
  await page.setViewportSize({width:320,height:568});
  await mount(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const box=await page.getByRole('dialog').boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(320);
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await page.evaluate(()=>window.changes.length)).toBe(0);
});
