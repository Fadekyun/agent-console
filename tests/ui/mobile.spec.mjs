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

test.describe('mobile skills view', () => {
  const SKILLS_RESPONSE = {
    entries: [
      { name: 'test-catalog-skill', description: 'A test catalog skill', kind: 'standard', tools: ['codex', 'claude'], allowed_profiles: null, requires_approval: false, source_present: true, synced: [{ tool: 'codex', linked: true }, { tool: 'claude', linked: false }], assigned_to: [{ profile: 'coder', assigned_at: '2026-07-20T00:00:00', assigned_by: 'test' }] },
      { name: 'super-skill', description: 'A superpower for testing', kind: 'superpower', tools: ['codex'], allowed_profiles: ['general', 'coder'], requires_approval: true, source_present: true, synced: [{ tool: 'codex', linked: true }], assigned_to: [] },
    ],
    errors: [],
  };

  test('mobile skill card shows six ordered direct children with assigned standard skill content and actions', async ({ page }) => {
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
    await page.route('**/api/models*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: [], stale: false }) });
    });
    await page.route('**/api/sessions?state=all', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });
    await page.route('**/api/projects', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });
    await page.route('**/api/skills', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SKILLS_RESPONSE) });
    });

    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="skills"]').click();
    await expect(page.locator('[data-mobile-view="skills"]:not([hidden])')).toBeVisible();
    await expect(page.locator('#mobile-skills-list')).toBeVisible();

    const card = page.locator('.mobile-skill-card').first();
    await expect(card).toBeVisible();
    await expect(card).toContainText('test-catalog-skill');
    await expect(card).toContainText('standard');
    await expect(card).toContainText('A test catalog skill');
    await expect(card).toContainText('codex: synced');
    await expect(card).toContainText('claude: missing');
    await expect(card).toContainText('Standard skill · no approval gate');
    await expect(card).toContainText('Assigned:');
    await expect(card).toContainText('coder');
    await expect(card.getByRole('button', { name: 'Assign', exact: true })).toBeVisible();
    await expect(card.getByRole('button', { name: 'Unassign coder', exact: true })).toBeVisible();

    const directChildrenOrder = await page.evaluate(() => {
      const c = document.querySelector('.mobile-skill-card');
      return Array.from(c.children).map((el) => {
        if (el.tagName === 'STRONG') return 'name';
        if (el.tagName === 'SMALL') return 'description';
        if (el.classList.contains('skill-approval-info')) return 'approval';
        if (el.classList.contains('skill-assigned-list')) return 'assignments';
        if (el.querySelector('button')) return 'actions';
        return 'tools';
      });
    });
    expect(directChildrenOrder).toEqual(['name', 'description', 'tools', 'approval', 'assignments', 'actions']);
  });
});

test.describe('mobile Attach rendering at narrow widths (Issue #31)', () => {
  for (const width of [360, 390]) {
    test(`Attach anchor visible and no overflow at ${width}px width`, async ({ page }) => {
      test.skip(!!process.env.LIVE_AGENT_CONSOLE_URL, 'Mock test, skip when live URL set.');

      await page.setViewportSize({ width, height: 800 });
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
      await page.route('**/api/models*', async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: [], stale: false }) });
      });
      await page.route('**/api/sessions?state=all', async (route) => {
        await route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify([{
            tmux_name: 'attach-test-session', tool: 'shell', profile: 'general',
            live_state: 'tmux live', managed: true,
            actions: ['attach', 'interrupt', 'restart', 'kill'],
          }]),
        });
      });
      await page.route('**/api/delegations', async (route) => {
        await route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({
            roots: [{ tmux_name: 'root-session', tool: 'opencode', profile: 'coder', running: true, children: [{ tmux_name: 'child-session', profile: 'scout' }] }],
            delegations: [],
            max_children_per_parent: 3,
          }),
        });
      });

      await page.goto('/mobile');

      // Check Attach in session card
      const sessionAttach = page.locator('.mobile-session a.button.primary');
      await expect(sessionAttach.first()).toBeVisible();
      await expect(sessionAttach.first()).toHaveText('Attach');
      await expect(sessionAttach.first()).not.toHaveCSS('clip', /rect/);

      // Check Attach in orchestration card
      await page.locator('[data-mobile-tab="orchestration"]').click();
      await expect(page.locator('[data-mobile-view="orchestration"]:not([hidden])')).toBeVisible();
      const treeAttach = page.locator('.mobile-tree-node a.button.primary');
      await expect(treeAttach.first()).toBeVisible();
      await expect(treeAttach.first()).toHaveText('Attach');
      await expect(treeAttach.first()).not.toHaveCSS('clip', /rect/);

      // No horizontal overflow
      await expect(async () => {
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
        expect(overflow).toBeFalsy();
      }).toPass({ timeout: 3000 });
    });
  }
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
    const actions = page.locator('.mobile-session-actions');
    await actions.getByRole('button', { name: 'Interrupt', exact: true }).click();
    await interruptReq;

    // Click Restart
    await actions.getByRole('button', { name: 'Restart', exact: true }).click();
    await restartReq;

    // Click Kill — triggers confirmation dialog
    await actions.getByRole('button', { name: 'Kill', exact: true }).click();
    await expect(page.locator('#mobile-kill-dialog')).toBeVisible();
    await page.locator('#mobile-kill-confirm').click();
    await killReq;
  });
});
