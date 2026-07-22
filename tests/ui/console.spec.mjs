import { expect, test } from '@playwright/test';

function session(overrides = {}) {
  return {
    id: 'sess-root', tmux_name: 'codex-root', tool: 'codex', profile: 'general',
    auth_context: 'default', agent_mode: null, provider: 'openai', repository: '/workspace/repo',
    worktree: null, parent_session_id: null, parent_session: null, linked_plan_id: null,
    current_command: 'node', socket_scope: 'canonical', attached_clients: 0, managed: true,
    running: true, live_state: 'agent active', child_count: 1, total_child_count: 1,
    actions: ['archive', 'attach', 'interrupt', 'restart', 'kill'], initial_task: 'Coordinate work',
    attention_state: 'normal', attention_note: null, attention_updated_at: null, attention_updated_by: null,
    last_activity: '2026-07-15T04:00:00+00:00',
    ...overrides,
  };
}

async function mockApi(page) {
  const active = session();
  const child = session({ id: 'sess-child', tmux_name: 'opencode-scout', tool: 'opencode', profile: 'scout', auth_context: 'openrouter-main', agent_mode: 'plan', provider: 'openrouter', parent_session_id: active.id, parent_session: active.tmux_name, current_command: 'opencode', child_count: 0, total_child_count: 0, initial_task: 'Scout only' });
  const stopped = session({ id: 'sess-old', tmux_name: 'old-session', running: false, live_state: 'stopped', current_command: null, actions: ['archive'], child_count: 0, total_child_count: 0 });
  const extras = ['dock-two', 'dock-three', 'dock-four', 'dock-five'].map((name, index) => session({ id: `sess-dock-${index}`, tmux_name: name, child_count: 0, total_child_count: 0, initial_task: null }));
  const sessions = [active, child, ...extras, stopped];
  const requests = [];
  const identity = {
    login: '10.0.0.40', access_surface: 'local-lan', default_tool: 'codex',
    default_agent_modes: { codex: 'auto', opencode: 'plan' }, profiles: [
      { name: 'general', display_name: 'General', read_write_capability: 'write', worktree_requirement: 'none', requires_human_approval: false, status: 'active' },
      { name: 'coder', display_name: 'Coder', read_write_capability: 'write', worktree_requirement: 'preferred', requires_human_approval: false, status: 'active' },
      { name: 'planner', display_name: 'Planner', read_write_capability: 'read_only', worktree_requirement: 'none', requires_human_approval: false, status: 'active' },
      { name: 'scout', display_name: 'Scout', read_write_capability: 'read_only', worktree_requirement: 'none', requires_human_approval: false, status: 'active' },
      { name: 'reviewer', display_name: 'Reviewer', read_write_capability: 'read_only', worktree_requirement: 'none', requires_human_approval: false, status: 'active' },
      { name: 'researcher', display_name: 'Researcher', read_write_capability: 'read_only', worktree_requirement: 'none', requires_human_approval: false, status: 'active' },
      { name: 'bugfix', display_name: 'Bugfix', read_write_capability: 'write', worktree_requirement: 'preferred', requires_human_approval: false, status: 'active' },
    ],
    tool_status: [
      { name: 'codex', status: 'ready', reason: null }, { name: 'opencode', status: 'ready', reason: null },
      { name: 'hermes', status: 'ready', reason: null }, { name: 'claude', status: 'disabled', reason: 'subscription inactive' },
      { name: 'shell', status: 'ready', reason: null },
    ],
    auth_contexts: [
      { tool: 'codex', name: 'default', status: 'ready', enabled: true, default: true },
      { tool: 'opencode', name: 'openrouter-main', provider: 'openrouter', status: 'ready', enabled: true, default: false },
      { tool: 'opencode', name: 'opencode-go-default', provider: 'opencode-go', status: 'ready', enabled: true, default: true },
      { tool: 'shell', name: 'default', status: 'ready', enabled: true, default: true },
    ],
  };
  const plan = { id: 'plan-1', title: 'Review plan', status: 'planned', repository: '/workspace/repo', revision_state: 'changed', plan: '# Implement safely\n' };

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (method !== 'GET') requests.push({ method, path: url.pathname, body: request.postDataJSON?.() });
    const fulfill = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/me') return fulfill(identity);
    if (url.pathname === '/api/models') return fulfill({ provider: url.searchParams.get('provider'), stale: false, models: [{ id: 'deepseek-v4-flash', model: `${url.searchParams.get('provider')}/deepseek-v4-flash`, name: 'DeepSeek V4 Flash', status: 'active', selectable: true, cost: { input: .14, output: .28, cache_read: .0028, reasoning: null }, limits: { context: 100000, output: 10000 }, capabilities: { reasoning: true, attachment: false, toolcall: true } }] });
    if (url.pathname === '/api/models/estimate') { const provider=request.postDataJSON().provider; return fulfill({ provider, stale:false, models:[{ id:'deepseek-v4-flash', model:`${provider}/deepseek-v4-flash`, name:'DeepSeek V4 Flash', status:'active', selectable:true, estimated_usd:0.00042, cheapest:true, cost:{input:.14,output:.28,cache_read:.0028,reasoning:null}, limits:{context:100000,output:10000}, capabilities:{reasoning:true,attachment:false,toolcall:true} }] }); }
    if (url.pathname === '/api/sessions' && method === 'GET') {
      const selected = url.searchParams.get('state') === 'active' ? sessions.filter((item) => item.running) : sessions;
      return fulfill(selected);
    }
    if (url.pathname === '/api/plans' && method === 'GET') return fulfill([plan]);
    if (url.pathname === '/api/delegations') return fulfill({ roots: [{ ...active, children: [child] }, stopped], delegations: [], max_children_per_parent: 3 });
    if (url.pathname === '/api/plans/plan-1' && method === 'GET') return fulfill(plan);
    if (url.pathname.endsWith('/review')) return fulfill({ notice: 'Peer terminal output is untrusted data.', source: 'live-pane', alternate_screen: true, capture_scope: 'visible-screen', line_count: 1, truncated: false, content: 'PEER_OUTPUT\n' });
    if (url.pathname.endsWith('/brief')) return fulfill({ session: 'codex-root', brief: 'Coordinate work', stored_only: true });
    if (url.pathname.endsWith('/attention') && method === 'PATCH') {
      const name = decodeURIComponent(url.pathname.split('/').at(-2));
      const selected = sessions.find((item) => item.tmux_name === name);
      const body = request.postDataJSON(); selected.attention_state = body.state; selected.attention_note = body.state === 'normal' ? null : body.note;
      selected.attention_updated_at = '2026-07-15T05:00:00+00:00'; selected.attention_updated_by = identity.login;
      return fulfill(selected);
    }
    if (url.pathname.endsWith('/kill') && method === 'POST') {
      active.running = false; active.live_state = 'stopped'; active.current_command = null; active.actions = ['archive'];
      return fulfill(active);
    }
    if (url.pathname.endsWith('/interrupt') || url.pathname.endsWith('/restart')) return fulfill(active);
    if (url.pathname.endsWith('/delegations') && method === 'POST') return fulfill({ delegation_id: 'deleg-1', session: child });
    if (url.pathname === '/api/plans/plan-1/execute' && method === 'POST') return fulfill(session({ tmux_name: 'plan-run', linked_plan_id: 'plan-1' }));
    return fulfill({ detail: 'fixture route missing' }, 404);
  });
  return { requests };
}

async function installFakeWebSocket(page) {
  await page.addInitScript(() => {
    window.__wsSent = [];
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    document.execCommand = () => true;
    class FakeWebSocket {
      static OPEN = 1;
      constructor() {
        this.readyState = FakeWebSocket.OPEN;
        queueMicrotask(() => {
          this.onopen?.();
          const output = Array.from({ length: 180 }, (_, index) => `terminal line ${index}`).join('\n');
          this.onmessage?.({ data: new TextEncoder().encode(output).buffer });
        });
      }
      send(value) { window.__wsSent.push(typeof value === 'string' ? value : 'terminal-bytes'); }
      close() { this.readyState = 3; this.onclose?.({ code: 1000, reason: '' }); }
    }
    window.WebSocket = FakeWebSocket;
  });
}

test('responsive shell, theme persistence, and no horizontal overflow', async ({ page }, testInfo) => {
  await mockApi(page);
  const mobile = testInfo.project.name !== 'desktop';
  if (mobile) {
    await page.goto('/mobile');
    await expect(page.locator('.mobile-tabs')).toBeVisible();
  } else {
    await page.goto('/desktop');
    await expect(page.locator('#view-title')).toHaveText('Sessions');
    await expect(page.locator('.bottom-nav')).toBeHidden();
    await expect(page.locator('.sidebar')).toBeVisible();
  }
  const theme = mobile ? page.locator('#mobile-theme') : page.locator('#theme-select');
  await theme.selectOption('dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
});

test('guided orchestration keeps delegation and plan execution explicit', async ({ page }) => {
  const fixture = await mockApi(page);
  await page.goto('/desktop');
  await page.locator('[data-view="orchestration"]:visible').click();
  await expect(page.locator('#session-tree')).toContainText('opencode-scout');
  await page.locator('.tree-node').first().locator('[data-delegate]').first().click();
  await page.locator('#delegate-form textarea[name="task"]').fill('Review the named peer');
  await page.locator('#delegate-form button[type="submit"]').click();
  await expect.poll(() => fixture.requests.some((item) => item.path.endsWith('/delegations'))).toBeTruthy();
  await page.locator('[data-preview]').click();
  await expect(page.locator('#plan-content')).toContainText('Implement safely');
  await expect(page.locator('#revision-warning')).toBeVisible();
  await page.locator('#revision-warning input').check();
});

test('managed kill remains confirmed and state-aware', async ({ page }) => {
  await mockApi(page);
  await page.goto('/desktop');
  const row = page.locator('.session-row').filter({ hasText: 'codex-root' }).first();
  await row.locator('summary').click();
  await row.locator('[data-action="kill"]').click();
  await expect(page.locator('#confirm-dialog')).toBeVisible();
  await page.locator('#confirm-submit').click();
  await page.locator('#filter-state').selectOption('all');
  await expect(page.locator('#session-history')).toContainText('codex-root');
});

test('terminal is scroll-first on mobile and peer insertion never auto-sends', async ({ page }, testInfo) => {
  await installFakeWebSocket(page);
  await mockApi(page);
  await page.goto('/terminal?session=codex-root');
  const mobile = testInfo.project.name !== 'desktop';
  await expect(page.locator('body')).toHaveAttribute('data-terminal-mode', mobile ? 'scroll' : 'type');
  if (mobile) {
    expect(await page.locator('.xterm-viewport').evaluate((node) => getComputedStyle(node).touchAction)).toBe('pan-y');
    await page.locator('[data-mode="type"]').click();
    await page.locator('#composer').fill('line one');
    await page.locator('#composer').press('Enter');
    await expect(page.locator('#composer')).toHaveValue('line one\n');
  }
  await page.locator('#composer').fill('');
  const before = await page.evaluate(() => window.__wsSent.filter((value) => value === 'terminal-bytes').length);
  await page.locator('#peers').click();
  await page.getByRole('button', { name: 'Insert review command' }).first().click();
  await expect(page.locator('#composer')).toHaveValue('agentctl session review opencode-scout');
  expect(await page.evaluate(() => window.__wsSent.filter((value) => value === 'terminal-bytes').length)).toBe(before);
  await page.locator('#paste-device').click();
  await expect(page.locator('#paste-sheet')).toBeVisible();
  const bounds = await page.locator('.composer').boundingBox();
  expect(bounds.y + bounds.height).toBeLessThanOrEqual((await page.viewportSize()).height + 1);
});

test('terminal detach and reconnect remain explicit', async ({ page }) => {
  await installFakeWebSocket(page);
  await mockApi(page);
  await page.goto('/terminal?session=codex-root');
  await page.locator('#detach').click();
  await expect.poll(async () => page.evaluate(() => window.__wsSent.some((value) => value.includes('detach')))).toBeTruthy();
});

test('brief preload, alternate-screen paging, and Text View never auto-send the brief', async ({ page }, testInfo) => {
  await installFakeWebSocket(page); await mockApi(page);
  await page.goto('/terminal?session=codex-root');
  await expect(page.locator('#composer')).toHaveValue('Coordinate work');
  expect(await page.evaluate(() => window.__wsSent.filter((value) => value === 'terminal-bytes').length)).toBe(0);
  await page.locator('#text-view').click();
  await expect(page.locator('#text-dialog')).toBeVisible();
  await expect(page.locator('#text-content')).toContainText('PEER_OUTPUT');
  await expect(page.locator('#text-scope')).toContainText('alternate-screen');
  await page.locator('#text-page-up').click();
  await expect.poll(() => page.evaluate(() => window.__wsSent.filter((value) => value === 'terminal-bytes').length)).toBeGreaterThan(0);
  if (testInfo.project.name !== 'desktop') {
    const before = await page.evaluate(() => window.__wsSent.filter((value) => value === 'terminal-bytes').length);
    await page.locator('#text-dialog').evaluate((dialog) => dialog.close());
    await page.locator('#terminal').dispatchEvent('touchstart', { touches: [{ identifier: 1, clientX: 100, clientY: 500 }] });
    await page.locator('#terminal').dispatchEvent('touchend', { changedTouches: [{ identifier: 1, clientX: 100, clientY: 300 }] });
    await expect.poll(() => page.evaluate(() => window.__wsSent.filter((value) => value === 'terminal-bytes').length)).toBeGreaterThan(before);
  }
});

test('OpenCode model picker estimates the cheapest model on desktop and mobile', async ({ page }, testInfo) => {
  await mockApi(page);
  if (testInfo.project.name === 'desktop') {
    await page.goto('/desktop#new');
    await page.locator('#new-session select[name="tool"]').selectOption('opencode');
    await page.locator('#model-cost-details').evaluate((details) => { details.open = true; });
    await page.locator('#estimate-output').fill('1500');
    await page.locator('#estimate-models').click();
    await expect(page.locator('#new-session select[name="provider"]')).toHaveValue('opencode-go');
    await expect(page.locator('.estimate-field')).toHaveCount(4);
    await expect(page.locator('#model-status')).toContainText('Selected cheapest for this token mix');
  } else {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
    await page.locator('#mobile-new select[name="tool"]').selectOption('opencode');
    await page.getByText('Estimate cheapest model').click();
    await page.locator('#mobile-output').fill('1500');
    await page.locator('#mobile-estimate').click();
    await expect(page.locator('#mobile-new select[name="provider"]')).toHaveValue('opencode-go');
    await expect(page.locator('#mobile-model-status')).toContainText('Selected cheapest:');
  }
});

test('structured attention is explicit, filterable, and separate from mechanical state', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop operations table and inspector coverage.');
  const fixture = await mockApi(page);
  await page.goto('/desktop');
  const row = page.locator('#active-sessions .session-row').filter({ hasText: 'codex-root' });
  await row.click();
  await expect(page.locator('#session-inspector')).toBeVisible();
  await page.locator('#attention-form select[name="state"]').selectOption('blocked');
  await page.locator('#attention-form textarea[name="note"]').fill('Waiting for reviewed deployment choice');
  await page.locator('#attention-form button[type="submit"]').click();
  await expect(page.locator('#session-inspector')).toContainText('Waiting for reviewed deployment choice');
  await expect(row).toContainText('Blocked');
  await page.locator('[data-attention-filter="blocked"]').click();
  await expect(page.locator('#active-sessions')).toContainText('codex-root');
  await expect(page.locator('#active-sessions')).not.toContainText('opencode-scout');
  await expect.poll(() => fixture.requests.some((item) => item.path.endsWith('/attention') && item.body.state === 'blocked')).toBeTruthy();
  const storageKeys = await page.evaluate(() => Object.keys(localStorage));
  expect(storageKeys.every((key) => ['agent-console-theme', 'agent-console-layout'].includes(key))).toBeTruthy();
});

test('desktop terminal dock keeps four tabs connected and rejects a fifth', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop dock coverage.');
  await installFakeWebSocket(page); await mockApi(page); await page.goto('/desktop');
  for (const name of ['codex-root', 'opencode-scout', 'dock-two', 'dock-three']) {
    await page.locator('#active-sessions .session-row').filter({ hasText: name }).locator('[data-attach]').click();
  }
  await expect(page.locator('.terminal-tab')).toHaveCount(4);
  await expect(page.locator('.terminal-embed')).toHaveCount(4);
  const sources = await page.locator('.terminal-embed').evaluateAll((frames) => frames.map((frame) => frame.src));
  await page.locator('.terminal-tab').first().click();
  expect(await page.locator('.terminal-embed').evaluateAll((frames) => frames.map((frame) => frame.src))).toEqual(sources);
  await page.locator('#active-sessions .session-row').filter({ hasText: 'dock-four' }).locator('[data-attach]').click();
  await expect(page.locator('#notice')).toContainText('Four terminal tabs are already open');
  await expect(page.locator('.terminal-tab')).toHaveCount(4);
  await page.locator('.terminal-tab').last().locator('.terminal-tab-close').click();
  await expect(page.locator('.terminal-tab')).toHaveCount(3);
  await page.locator('#terminal-dock-collapse').click();
  await expect(page.locator('#terminal-dock')).toHaveClass(/collapsed/);
});

test('embedded terminal iframe receives resize dispatch and has embedded class', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop only.');
  await page.addInitScript(() => {
    window.__iframeResizeCount = 0;
    window.addEventListener('resize', () => { window.__iframeResizeCount++; });
  });
  await installFakeWebSocket(page); await mockApi(page); await page.goto('/desktop');
  await page.locator('#active-sessions .session-row').filter({ hasText: 'codex-root' }).locator('[data-attach]').click();
  await expect(page.locator('.terminal-embed')).toHaveCount(1);
  const iframe = await page.locator('.terminal-embed').first().elementHandle().then((el) => el.contentFrame());
  expect(iframe).toBeTruthy();
  await expect.poll(() => iframe.evaluate(() => document.body.classList.contains('terminal-embedded'))).toBeTruthy();
  expect(await iframe.evaluate(() => !!document.getElementById('terminal'))).toBeTruthy();
  await page.locator('#terminal-dock-collapse').click();
  await page.waitForTimeout(100);
  await page.locator('#terminal-dock-collapse').click();
  await expect.poll(async () => {
    try { return await iframe.evaluate(() => window.__iframeResizeCount); } catch { return 0; }
  }).toBeGreaterThan(0);
});

test('Codex exposes safe Auto and read-only Plan modes', async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto(testInfo.project.name === 'desktop' ? '/desktop#new' : '/mobile');
  if (testInfo.project.name !== 'desktop') await page.locator('[data-mobile-tab="new"]').click();
  const form = page.locator(testInfo.project.name === 'desktop' ? '#new-session' : '#mobile-new');
  await form.locator('select[name="tool"]').selectOption('codex');
  await expect(form.locator('select[name="agent_mode"]')).toHaveValue('auto');
  await form.locator('select[name="agent_mode"]').selectOption('plan');
  await expect(form.locator('select[name="agent_mode"]')).toHaveValue('plan');
  await form.locator('select[name="profile"]').selectOption('planner');
  await expect(form.locator('select[name="agent_mode"]')).toHaveValue('plan');
  await expect(form.locator('select[name="agent_mode"] option[value="auto"]')).toHaveCount(0);
});

test('profiles view shows profile cards with metadata', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop profiles coverage.');
  await mockApi(page);
  await page.goto('/desktop#profiles');
  await expect(page.locator('#profiles-list')).toBeVisible();
  await expect(page.locator('.profile-card')).not.toHaveCount(0);
  await expect(page.locator('.profile-card').first()).toContainText('General');
});

test('skills view renders skill cards from catalog', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop skills coverage.');
  await page.route('**/api/skills', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ entries: [{ name: 'test-skill', description: 'A test skill', kind: 'standard', tools: ['codex', 'claude'], synced: [{ tool: 'codex', linked: true }, { tool: 'claude', linked: false }], source_present: true }], errors: [] }),
    });
  });
  await mockApi(page);
  await page.goto('/desktop#skills');
  await expect(page.locator('#skills-list')).toBeVisible();
  await expect(page.locator('.skill-card')).not.toHaveCount(0);
  await expect(page.locator('.skill-card').first()).toContainText('test-skill');
});

test('orchestration view shows session groups', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop orchestration coverage.');
  await page.route('**/api/session-groups', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'grp-1', name: 'test-group', purpose: 'coordinate work', status: 'active', sessions: [{ tmux_name: 'child-1', profile: 'planner', tool: 'codex', status: 'detached', attention_state: 'normal' }] }]),
    });
  });
  await mockApi(page);
  await page.goto('/desktop#orchestration');
  await expect(page.locator('#session-tree')).toBeVisible();
});

test('projects view shows project cards', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop projects coverage.');
  await page.route('**/api/projects', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'proj-1', name: 'test-project', repository: '/workspace/repo', description: 'A test project', status: 'active', session_count: 2 }]),
    });
  });
  await mockApi(page);
  await page.goto('/desktop#projects');
  await expect(page.locator('#projects-list')).toBeVisible();
  await expect(page.locator('.profile-card').first()).toContainText('test-project');
});

test('new-output button appears when not at bottom and contextual label works', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Scroll-to-bottom coverage on desktop.');
  await installFakeWebSocket(page);
  await mockApi(page);
  await page.goto('/terminal?session=codex-root');
  // Simulate scroll away from bottom by setting viewportY < baseY via page.evaluate
  await page.evaluate(() => {
    const term = window.__terminal;
    if (term) { term.buffer.active.viewportY = 0; term.buffer.active.baseY = 100; term.scrollLines(10); }
    // Trigger onScroll
    term?.scrollLines(1);
  });
  await expect(page.locator('#new-output')).toBeVisible();
  await expect(page.locator('#new-output')).toContainText('Scroll to bottom');
  // Click to scroll to bottom
  await page.locator('#new-output').click();
  await expect(page.locator('#new-output')).toBeHidden();
});
