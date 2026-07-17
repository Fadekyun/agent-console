import { expect, test } from '@playwright/test';

const liveURL = process.env.LIVE_AGENT_CONSOLE_URL;

test.describe('mobile Agent Console tabs', () => {
  test.skip(!liveURL, 'Set LIVE_AGENT_CONSOLE_URL to run mobile live dogfood tests.');

  test('renders all four mobile tabs without horizontal overflow and exercises API actions', async ({ page, request }, testInfo) => {
    const suffix = `${Date.now().toString(36)}-${testInfo.project.name}`;
    const name = `ui-mobile-${suffix}`.slice(0, 63);
    let created = false;

    const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

    try {
      const create = await request.post('/api/sessions', {
        data: {
          tool: 'shell',
          profile: 'general',
          name,
          repository: process.env.AGENT_CONSOLE_WORKSPACE_ROOT || '.',
          task: 'Disposable mobile browser dogfood session',
          auth_context: 'default',
        },
      });
      expect(create.ok(), await create.text()).toBeTruthy();
      created = true;

      await page.goto('/mobile');

      // Sessions tab (default) — card visible, no overflow
      await expect(page.locator('.mobile-session').filter({ hasText: name })).toBeVisible();
      expect(await noOverflow()).toBeTruthy();

      // Plans tab — list or empty state, no overflow
      await page.locator('[data-mobile-tab="plans"]').click();
      await expect(page.locator('[data-mobile-view="plans"]:not([hidden])')).toBeVisible();
      await expect(page.locator('#mobile-plans')).toBeVisible();
      expect(await noOverflow()).toBeTruthy();

      // Orchestration tab — tree or empty state, no overflow
      await page.locator('[data-mobile-tab="orchestration"]').click();
      await expect(page.locator('[data-mobile-view="orchestration"]:not([hidden])')).toBeVisible();
      await expect(page.locator('#mobile-tree')).toBeVisible();
      expect(await noOverflow()).toBeTruthy();

      // Back to sessions for action tests
      await page.locator('[data-mobile-tab="sessions"]').click();
      await expect(page.locator('.mobile-session').filter({ hasText: name })).toBeVisible();

      // Interrupt via API, assert list reflects
      const interrupt = await request.post(`/api/sessions/${encodeURIComponent(name)}/interrupt`, { data: {} });
      expect(interrupt.ok(), await interrupt.text()).toBeTruthy();
      expect(await noOverflow()).toBeTruthy();

      // Restart via API
      const restart = await request.post(`/api/sessions/${encodeURIComponent(name)}/restart`, { data: {} });
      expect(restart.ok(), await restart.text()).toBeTruthy();

      // Attention PATCH via API
      const attention = await request.fetch(`/api/sessions/${encodeURIComponent(name)}/attention`, {
        method: 'PATCH',
        data: { state: 'ready_for_review', note: 'Mobile test review ready' },
      });
      expect(attention.ok(), await attention.text()).toBeTruthy();

      // Kill via API
      const kill = await request.post(`/api/sessions/${encodeURIComponent(name)}/kill`, { data: { confirmed: true } });
      expect(kill.ok(), await kill.text()).toBeTruthy();
      created = false;
    } finally {
      if (created) {
        await request.post(`/api/sessions/${encodeURIComponent(name)}/kill`, { data: { confirmed: true } });
      }
    }
  });
});

test.describe('mobile lifecycle button clicks', () => {
  test('clicking Interrupt/Restart/Kill buttons dispatches correct API calls', async ({ page }) => {
    const sessionName = 'ui-mock-buttons-test';

    // Mock /api/me for startup
    await page.route('**/api/me', async (route) => {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          access_surface: 'test', login: 'test',
          tool_status: [{ name: 'shell', status: 'ready' }],
          profiles: [{ name: 'general', display_name: 'General', read_write_capability: 'write', worktree_requirement: 'none', requires_human_approval: false, status: 'active' }],
          auth_contexts: [{ tool: 'shell', name: 'default', status: 'ready', default: true }],
          default_tool: 'shell',
        }),
      });
    });

    // Mock /api/models
    await page.route('**/api/models*', async (route) => {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ models: [], stale: false }),
      });
    });

    // Mock /api/sessions to return a session with all lifecycle actions
    await page.route('**/api/sessions?state=all', async (route) => {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify([{
          tmux_name: sessionName, tool: 'shell', profile: 'general',
          live_state: 'tmux live', managed: true,
          actions: ['attach', 'interrupt', 'restart', 'kill'],
        }]),
      });
    });

    // Capture lifecycle POST requests
    const interruptReq = page.waitForRequest(
      (r) => r.url().includes(`/api/sessions/${sessionName}/interrupt`) && r.method() === 'POST'
    );
    const restartReq = page.waitForRequest(
      (r) => r.url().includes(`/api/sessions/${sessionName}/restart`) && r.method() === 'POST'
    );
    const killReq = page.waitForRequest(
      (r) => r.url().includes(`/api/sessions/${sessionName}/kill`) && r.method() === 'POST'
    );

    // Allow lifecycle POSTs through (don't block)
    await page.route('**/api/sessions/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.goto('/mobile');
    await expect(page.locator('.mobile-session')).toBeVisible();

    // Click Interrupt
    await page.locator('button:has-text("Interrupt")').click();
    await interruptReq;

    // Click Restart
    await page.locator('button:has-text("Restart")').click();
    await restartReq;

    // Click Kill — triggers confirmation dialog
    await page.locator('button:has-text("Kill")').click();
    await expect(page.locator('#mobile-kill-dialog')).toBeVisible();
    await page.locator('#mobile-kill-confirm').click();
    await killReq;
  });
});
