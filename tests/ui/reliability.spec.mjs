import { expect, test } from '@playwright/test';

function mockIdentity() {
  return {
    access_surface: 'test', login: 'test',
    tool_status: [{ name: 'shell', status: 'ready' }, { name: 'codex', status: 'ready' }],
    profiles: [
      { name: 'general', display_name: 'General', read_write_capability: 'write', worktree_requirement: 'none', status: 'active' },
      { name: 'coder', display_name: 'Coder', read_write_capability: 'write', worktree_requirement: 'optional', status: 'active' },
      { name: 'planner', display_name: 'Planner', read_write_capability: 'read_only', worktree_requirement: 'none', status: 'active' },
      { name: 'reviewer', display_name: 'Reviewer', read_write_capability: 'read_only', worktree_requirement: 'none', status: 'active' },
      { name: 'scout', display_name: 'Scout', read_write_capability: 'read_only', worktree_requirement: 'none', status: 'active' },
    ],
    auth_contexts: [
      { tool: 'shell', name: 'default', status: 'ready', default: true, enabled: true },
      { tool: 'codex', name: 'default', status: 'ready', default: true, enabled: true },
    ],
    default_tool: 'shell',
  };
}

async function routeCommonMobile(page) {
  await page.route('**/api/me', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(mockIdentity()),
  }));
  await page.route('**/api/models*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ models: [], stale: false }),
  }));
  await page.route('**/api/projects', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: '[]',
  }));
  await page.route('**/api/profiles', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify([
      { name: 'coder', display_name: 'Coder', read_write_capability: 'write', worktree_requirement: 'optional', status: 'active', allowed_delegation_profiles: ['reviewer', 'scout'] },
      { name: 'reviewer', display_name: 'Reviewer', read_write_capability: 'read_only', worktree_requirement: 'none', status: 'active' },
      { name: 'scout', display_name: 'Scout', read_write_capability: 'read_only', worktree_requirement: 'none', status: 'active' },
      { name: 'planner', display_name: 'Planner', read_write_capability: 'read_only', worktree_requirement: 'none', status: 'active' },
    ]),
  }));
}

test.describe('issue 114 mobile reliability', () => {
  test('filters stopped sessions, keeps navigation usable, and preserves parent delegation rules', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await routeCommonMobile(page);

    await page.route('**/api/sessions?state=all', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { tmux_name: 'running-session', tool: 'shell', profile: 'general', running: true, live_state: 'tmux live', managed: true, actions: ['attach'] },
        { tmux_name: 'stopped-session', tool: 'shell', profile: 'general', running: false, live_state: 'stopped', managed: true, actions: [] },
      ]),
    }));
    await page.route('**/api/sessions?state=active', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { tmux_name: 'root-coder', tool: 'codex', profile: 'coder', running: true, repository: '/work/repo', actions: ['attach'] },
      ]),
    }));
    await page.route('**/api/delegations', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        roots: [{ tmux_name: 'root-coder', tool: 'codex', profile: 'coder', running: true, children: [] }],
        delegations: [], max_children_per_parent: 3,
      }),
    }));
    await page.route('**/api/session-groups', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }));

    await page.goto('/mobile');
    await expect(page.getByText('running-session', { exact: true })).toBeVisible();
    await expect(page.getByText('stopped-session', { exact: true })).toHaveCount(0);

    const metrics = await page.locator('.mobile-tabs').evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      buttons: el.querySelectorAll('button').length,
    }));
    expect(metrics.buttons).toBe(7);
    expect(metrics.scrollWidth).toBeGreaterThan(metrics.clientWidth);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();

    await page.locator('[data-mobile-tab="orchestration"]').click();
    await expect(page.getByText('root-coder', { exact: true })).toBeVisible();
    await page.locator('.mobile-tree-node').filter({ hasText: 'root-coder' }).getByRole('button', { name: 'Delegate' }).click();

    await expect(page.locator('#mobile-delegate-dialog')).toBeVisible();
    await expect(page.locator('#mobile-delegate input[name="parent"]')).toHaveValue('root-coder');
    await expect(page.locator('#mobile-delegate input[name="repository"]')).toHaveValue('/work/repo');
    await expect(page.locator('#mobile-delegate select[name="profile"] option')).toHaveText(['Reviewer', 'Scout']);
  });
});

test.describe('issue 114 terminal interaction', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 800 } });

  test('touch terminal becomes type-ready and conventional clipboard shortcuts use the composer', async ({ page }) => {
    await page.addInitScript(() => {
      class MockWebSocket {
        static OPEN = 1;
        constructor(url) {
          this.url = url;
          this.readyState = 0;
          this.binaryType = 'arraybuffer';
          setTimeout(() => {
            this.readyState = MockWebSocket.OPEN;
            this.onopen?.({});
          }, 0);
        }
        send(value) {
          globalThis.__sent = globalThis.__sent || [];
          globalThis.__sent.push(value);
        }
        close() {
          this.readyState = 3;
        }
      }
      globalThis.WebSocket = MockWebSocket;
    });

    await page.route('**/api/sessions/touch-test/brief', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ brief: null }),
    }));

    await page.goto('/terminal?session=touch-test');
    await expect.poll(() => page.evaluate(() => document.body.dataset.reliabilityTerminal || '')).toBe('ready');
    await expect(page.locator('body')).toHaveAttribute('data-terminal-mode', 'type');

    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: async (value) => { globalThis.__copied = value; },
          readText: async () => 'paste-me',
        },
      });
      globalThis.__terminal.getSelection = () => 'copy-me';
      globalThis.__terminal.focus();
    });

    await page.keyboard.press('Control+C');
    await expect.poll(() => page.evaluate(() => globalThis.__copied || '')).toBe('copy-me');

    await page.evaluate(() => globalThis.__terminal.focus());
    await page.keyboard.press('Control+V');
    await expect(page.locator('#composer')).toHaveValue('paste-me');
    await expect(page.locator('#connection')).toHaveText('Pasted into composer; review before sending');
  });
});

test.describe('issue 114 desktop scaled layout', () => {
  test('reliability stylesheet contains the intermediate-width card layout and proportional dock state', async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 700 });
    await page.route('**/api/me', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(mockIdentity()),
    }));
    await page.route('**/api/sessions*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
    await page.route('**/api/plans', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
    await page.route('**/api/delegations', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{"roots":[],"delegations":[]}' }));
    await page.route('**/api/projects', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
    await page.route('**/api/session-groups', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));

    await page.goto('/desktop');
    const reliability = page.locator('style[data-agent-console-reliability="114"]');
    await expect(reliability).toHaveCount(1);
    const css = await reliability.textContent();
    expect(css).toContain('@media (min-width: 761px) and (max-width: 1100px)');
    expect(css).toContain('.session-table { min-width: 0 !important; }');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  });
});
