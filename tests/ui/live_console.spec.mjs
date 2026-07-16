import { expect, test } from '@playwright/test';

const liveURL = process.env.LIVE_AGENT_CONSOLE_URL;

test.describe('live Agent Console dogfood', () => {
  test.skip(!liveURL, 'Set LIVE_AGENT_CONSOLE_URL to run disposable live dogfood tests.');

  test('renders the real UI and exercises a disposable terminal with two clients', async ({ browser, page, request }, testInfo) => {
    const suffix = `${Date.now().toString(36)}-${testInfo.project.name}`;
    const name = `ui-live-${suffix}`.slice(0, 63);
    const childName = `ui-peer-${suffix}`.slice(0, 63);
    let created = false;
    let childCreated = false;

    try {
      const create = await request.post('/api/sessions', {
        data: {
          tool: 'shell',
          profile: 'general',
          name,
          repository: process.env.AGENT_CONSOLE_WORKSPACE_ROOT || '.',
          task: 'Disposable live browser dogfood session',
          auth_context: 'default',
        },
      });
      expect(create.ok(), await create.text()).toBeTruthy();
      created = true;

      if (testInfo.project.name !== 'desktop') {
        await page.goto('/mobile');
        await expect(page.locator('.mobile-session').filter({ hasText: name })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
      }
      await page.goto('/desktop');
      await expect(page.locator('#view-title')).toHaveText('Sessions');
      await expect(page.locator('.session-row').filter({ hasText: name })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();

      await page.locator('[data-view="orchestration"]:visible').click();
      const root = page.locator('.tree-node').filter({ hasText: name }).first();
      await root.locator('[data-delegate]').click();
      await page.locator('#delegate-form select[name="tool"]').selectOption('shell');
      await page.locator('#delegate-form input[name="name"]').fill(childName);
      await page.locator('#delegate-form textarea[name="task"]').fill('Disposable read-only live delegation test');
      await page.locator('#delegate-form button[type="submit"]').click();
      await expect(page.locator('#session-tree')).toContainText(childName);
      childCreated = true;
      await expect.poll(async () => {
        const response = await request.get('/api/delegations');
        const tree = await response.json();
        return tree.roots.find((item) => item.tmux_name === name)?.children.some((item) => item.tmux_name === childName);
      }).toBeTruthy();
      const childKill = await request.post(`/api/sessions/${encodeURIComponent(childName)}/kill`, { data: { confirmed: true } });
      expect(childKill.ok(), await childKill.text()).toBeTruthy();
      childCreated = false;

      await page.goto(`/terminal?session=${encodeURIComponent(name)}`);
      await expect(page.locator('#connection')).toContainText('Connected');
      await page.locator('#composer').fill(`printf 'LIVE_REVIEW_${suffix}\\n'`);
      await page.locator('#send-enter').click();

      await expect.poll(async () => {
        const response = await request.get(`/api/sessions/${encodeURIComponent(name)}/review?lines=40`);
        if (!response.ok()) return false;
        return (await response.json()).content.includes(`LIVE_REVIEW_${suffix}`);
      }).toBeTruthy();

      await page.locator('#peers').click();
      await expect(page.locator('#peers-dialog')).toBeVisible();
      await page.getByRole('button', { name: 'Insert review command' }).first().click();
      await expect(page.locator('#composer')).not.toHaveValue('');

      const secondContext = await browser.newContext({ viewport: testInfo.project.use.viewport });
      const secondPage = await secondContext.newPage();
      await secondPage.goto(`${liveURL}/terminal?session=${encodeURIComponent(name)}`);
      await expect(secondPage.locator('#connection')).toContainText('Connected');
      await expect.poll(async () => {
        const response = await request.get('/api/sessions?state=active');
        const sessions = await response.json();
        return sessions.find((item) => item.tmux_name === name)?.attached_clients;
      }).toBe(2);

      await secondPage.locator('#detach').click();
      await expect(secondPage.locator('#connection')).toContainText('Detached');
      await secondPage.locator('#reconnect').click();
      await expect(secondPage.locator('#connection')).toContainText('Connected');
      await secondContext.close();

      await page.goto('/desktop');
      const row = page.locator('.session-row').filter({ hasText: name }).first();
      await row.locator('summary').click();
      await row.locator('[data-action="kill"]').click();
      await expect(page.locator('#confirm-dialog')).toBeVisible();
      await page.locator('#confirm-submit').click();
      await expect.poll(async () => {
        const response = await request.get('/api/sessions?state=active');
        return !(await response.json()).some((item) => item.tmux_name === name);
      }).toBeTruthy();
      created = false;
    } finally {
      if (childCreated) {
        await request.post(`/api/sessions/${encodeURIComponent(childName)}/kill`, { data: { confirmed: true } });
      }
      if (created) {
        await request.post(`/api/sessions/${encodeURIComponent(name)}/kill`, { data: { confirmed: true } });
      }
    }
  });
});
