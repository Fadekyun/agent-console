import { test, expect } from '@playwright/test';

async function overviewFixture(page, { name = 'A normal session name', warnings = [] } = {}) {
  const readiness = { ready: !warnings.length, warnings };
  const session = { id: 'root', tmux_name: name, tool: 'shell', profile: 'coder', running: true, managed: true, attention_state: 'normal', actions: [] };
  const node = { id: 'root', root_id: 'root', owner_id: null, native_id: 'root', native_name: name, title: name, task: 'Inspect the user interface', tool: 'shell', profile: 'coder', mechanical: 'running', attention: 'normal', result_state: 'unknown', attempts: [], readiness: {}, created_at: '2026-10-03T00:00:00Z', last_activity: '2026-10-03T00:00:00Z' };
  const unexpected = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let json;
    if (path === '/api/me') json = { profiles: [], tool_status: [], auth_contexts: [] };
    else if (path === '/api/sessions') json = [session];
    else if (path === '/api/interface') json = { label: 'Test', workspace: '/tmp/fixture' };
    else if (path === '/api/workbench') json = { sessions: [session], nodes: [node], groups: [{ root_id: 'root', member_ids: ['root'], children_total: 0, children_complete: 0 }], aliases: {}, readiness };
    else { unexpected.push(path); await route.fulfill({ status: 500, json: { detail: 'Unexpected fixture request' } }); return; }
    await route.fulfill({ json });
  });
  return { readiness, unexpected, session, node };
}

test('unbroken valid session names fit work cards at phone and zoom widths', async ({ page }) => {
  const { unexpected } = await overviewFixture(page, { name: 'x'.repeat(80) });
  await page.goto('/work');
  for (const width of [320, 360, 390, 640, 1280]) {
    await page.setViewportSize({ width, height: 500 });
    await expect(page.locator('.card h3')).toHaveText('x'.repeat(80));
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const card = await page.locator('.card').boundingBox();
    expect(card.x + card.width).toBeLessThanOrEqual(width);
    await page.locator('#refresh').click();
    await expect(page.locator('#refresh')).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  expect(unexpected).toEqual([]);
});

test('direct Settings load shows readiness warnings and refresh recovers in place', async ({ page }) => {
  const { readiness, unexpected } = await overviewFixture(page, { warnings: ['Selected harness needs account setup'] });
  await page.goto('/work#settings');
  await expect(page.locator('#readiness-summary')).toHaveText('1 readiness warning');
  await expect(page.locator('#readiness-details')).toContainText('Selected harness needs account setup');
  await expect(page.locator('#readiness-details')).toBeVisible();
  await page.locator('#readiness-summary').click();
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#refresh')).toBeEnabled();
  await expect(page.locator('#readiness-details')).toBeHidden();
  readiness.warnings = []; readiness.ready = true;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#readiness-summary')).toHaveText('System ready');
  await expect(page.locator('#readiness-details')).toBeEmpty();
  readiness.warnings = ['Another account requires setup']; readiness.ready = false;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#readiness-details')).toContainText('Another account requires setup');
  await expect(page.locator('#readiness-details')).toBeVisible();
  expect(unexpected).toEqual([]);
});


test('Open work survives attention updates held across pointer activation', async ({ page }) => {
  const { node, session } = await overviewFixture(page);
  await page.routeWebSocket('**/ws/sessions/**', socket => socket.send('Fixture ready\r\n'));
  await page.goto('/work');
  const open = page.getByRole('link', { name: 'Open work', exact: true });
  await open.evaluate(element => element.scrollIntoView({ block: 'center' }));
  await expect(open).toBeInViewport();
  await open.evaluate(element => { window.originalWorkLink = element; });
  const box = await open.boundingBox();
  expect(await page.evaluate(({ x, y, width, height }) => document.elementFromPoint(x + width / 2, y + height / 2)?.closest('a')?.textContent, box)).toBe('Open work');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  node.attention = session.attention_state = 'blocked'; node.needs_attention = true;
  const response = page.waitForResponse(r => r.url().includes('/api/workbench?'));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await response; await expect(page.locator('#refresh')).toBeEnabled();
  expect(await open.boundingBox()).toEqual(box);
  await page.mouse.up();
  await expect(page).toHaveURL(/#session\/A%20normal%20session%20name$/);
  await page.evaluate(() => { location.hash = '#work'; });
  await expect(page.locator('.card')).toContainText('Attention: Blocked');
  expect(await open.evaluate(element => element === window.originalWorkLink)).toBe(true);
});

test('polling updates overview cards in place and filtering still removes stale matches', async ({ page }) => {
  const { node } = await overviewFixture(page);
  await page.goto('/work');
  const open = page.getByRole('link', { name: 'Open work', exact: true });
  await open.evaluate(element => { window.originalWorkLink = element; });
  node.attention = 'needs_input'; node.needs_attention = true;
  await page.locator('#refresh').click();
  await expect(page.locator('.card')).toContainText('Attention: Needs input');
  expect(await open.evaluate(element => element === window.originalWorkLink)).toBe(true);
  node.title = 'Updated session title';
  await page.locator('#refresh').click();
  await expect(page.locator('.card h3')).toHaveText('Updated session title');
  expect(await open.evaluate(element => element === window.originalWorkLink)).toBe(true);
  await page.locator('#search').fill('not-present');
  await expect(page.locator('.card')).toHaveCount(0);
  await page.locator('#search').fill('Updated session');
  await expect(page.locator('.card')).toHaveCount(1);
});
