import { expect, test } from '@playwright/test';

const SKILLS_RESPONSE = {
  entries: [
    { name: 'test-skill', description: 'A test skill', kind: 'standard', tools: ['codex', 'claude'], allowed_profiles: null, requires_approval: false, source_present: true, synced: [{ tool: 'codex', linked: true }, { tool: 'claude', linked: false }], assigned_to: [{ profile: 'coder', assigned_at: '2026-07-20T00:00:00', assigned_by: 'test' }] },
    { name: 'super-skill', description: 'A superpower', kind: 'superpower', tools: ['codex'], allowed_profiles: ['general', 'coder'], requires_approval: true, source_present: true, synced: [{ tool: 'codex', linked: true }], assigned_to: [] },
  ],
  errors: [],
};

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
      { tool: 'opencode', name: 'opencode-zen-default', provider: 'opencode', status: 'ready', enabled: true, default: false },
      { tool: 'opencode', name: 'opencode-go-disabled', provider: 'opencode-go', status: 'disabled', enabled: false, default: false },
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
    if (url.pathname === '/api/profiles' && method === 'GET') return fulfill(identity.profiles);
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
    window.__fakeWs = null;
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    document.execCommand = () => true;
    class FakeWebSocket {
      static OPEN = 1;
      constructor() {
        this.readyState = FakeWebSocket.OPEN;
        window.__fakeWs = this;
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
  if (mobile) {
    await page.goto('/desktop');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy();
  }
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
  await expect(page.locator('#connection')).toHaveText('Connected');
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

test('provider dropdown shows identical GO / ZEN / OpenRouter options on desktop and mobile', async ({ page }, testInfo) => {
  await mockApi(page);
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  const providerSelect = form.locator('select[name="provider"]');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  await form.locator('select[name="tool"]').selectOption('opencode');
  const options = await providerSelect.locator('option').evaluateAll((opts) => opts.map((o) => ({ value: o.value, label: o.label, selected: o.selected })));
  expect(options).toEqual([
    { value: 'opencode-go', label: 'OpenCode GO (default, paid)', selected: true },
    { value: 'opencode', label: 'OpenCode ZEN (free)', selected: false },
    { value: 'openrouter', label: 'OpenRouter', selected: false },
  ]);
});

test('provider switching filters auth contexts and excludes disabled contexts', async ({ page }, testInfo) => {
  await mockApi(page);
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  await form.locator('select[name="tool"]').selectOption('opencode');
  // GO provider shows GO contexts only, excluding disabled ones
  await form.locator('select[name="provider"]').selectOption('opencode-go');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"]')).toContainText('opencode-go-default');
  // ZEN provider shows ZEN context only
  await form.locator('select[name="provider"]').selectOption('opencode');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"]')).toContainText('opencode-zen-default');
  // OpenRouter provider shows OpenRouter context
  await form.locator('select[name="provider"]').selectOption('openrouter');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"]')).toContainText('openrouter-main');
});

const sameCat = (u, p) => u.pathname === '/api/models' && u.searchParams.get('provider') === p;
const flushFrames = (p) => p.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

test('delayed catalogue response does not overwrite newer provider selection', async ({ page }, testInfo) => {
  await mockApi(page);
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  await form.locator('select[name="tool"]').selectOption('opencode');
  let goHold;
  const goHoldP = new Promise(r => { goHold = r; });
  await page.route('**/api/models**', async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname !== '/api/models') return route.fallback();
    if (u.searchParams.get('provider') === 'opencode-go') { await goHoldP; return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ provider: 'opencode-go', stale: false, models: [{ id: 'go-model', model: 'opencode-go/go-model', name: 'GO Model', status: 'active', selectable: true, cost: { input: 1, output: 2, cache_read: null, reasoning: null }, limits: { context: 1000, output: 100 }, capabilities: { reasoning: false, attachment: false, toolcall: true } }] }) }); }
    return route.fallback();
  });
  // 1. Register exact waitForRequest for GO catalogue
  const goReq = page.waitForRequest(req => { try { return sameCat(new URL(req.url()), 'opencode-go'); } catch { return false; } });
  // 2. Select GO provider → GO catalogue blocks at goHoldP
  await form.locator('select[name="provider"]').selectOption('opencode-go');
  // 3. Await request observation
  await goReq;
  // 4. Switch to ZEN (immediate, no hold) and wait for success
  const zenCatRes = page.waitForResponse(res => { try { return sameCat(new URL(res.url()), 'opencode'); } catch { return false; } });
  await form.locator('select[name="provider"]').selectOption('opencode');
  await zenCatRes;
  await flushFrames(page);
  // Verify ZEN state
  await expect(form.locator('input[name="model"]')).toHaveValue('opencode/deepseek-v4-flash');
  const datalist = testInfo.project.name === 'desktop' ? page.locator('#model-options') : page.locator('#mobile-models');
  await expect(datalist.locator('option')).toHaveCount(1);
  await expect(datalist.locator('option').first()).toHaveValue('opencode/deepseek-v4-flash');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('opencode-zen-default');
  await expect(form.locator('select[name="provider"]')).toHaveValue('opencode');
  await expect(form.locator('select[name="tool"]')).toHaveValue('opencode');
  // 5. Register waitForResponse for stale GO catalogue
  const goRes = page.waitForResponse(res => { try { return sameCat(new URL(res.url()), 'opencode-go'); } catch { return false; } });
  // 6. Release stale route
  goHold();
  // 7. Await response directly
  await goRes;
  await flushFrames(page);
  // 9. Assert ZEN state unchanged
  await expect(form.locator('input[name="model"]')).toHaveValue('opencode/deepseek-v4-flash');
  await expect(datalist.locator('option')).toHaveCount(1);
  await expect(datalist.locator('option').first()).toHaveValue('opencode/deepseek-v4-flash');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('opencode-zen-default');
  await expect(form.locator('select[name="provider"]')).toHaveValue('opencode');
  await expect(form.locator('select[name="tool"]')).toHaveValue('opencode');
});

test('stale GO estimate does not overwrite ZEN catalogue selection', async ({ page }, testInfo) => {
  await mockApi(page);
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  await form.locator('select[name="tool"]').selectOption('opencode');
  await form.locator('select[name="provider"]').selectOption('opencode-go');
  let goEstHold;
  const goEstHoldP = new Promise(r => { goEstHold = r; });
  await page.route('**/api/models/estimate**', async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname !== '/api/models/estimate') return route.fallback();
    const prov = route.request().postDataJSON().provider;
    if (prov === 'opencode-go') { await goEstHoldP; return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ provider: 'opencode-go', stale: false, models: [{ id: 'go-model', model: 'opencode-go/go-model', name: 'GO Model', status: 'active', selectable: true, estimated_usd: 0.5, cheapest: true, cost: { input: 1, output: 2, cache_read: null, reasoning: null }, limits: { context: 1000, output: 100 }, capabilities: { reasoning: false, attachment: false, toolcall: true } }] }) }); }
    return route.fallback();
  });
  // 1. Register exact waitForRequest for GO estimate
  const goEstReq = page.waitForRequest(req => { try { const u = new URL(req.url()); return u.pathname === '/api/models/estimate' && req.postDataJSON().provider === 'opencode-go'; } catch { return false; } });
  // 2. Fire GO estimate (blocks at goEstHoldP)
  if (testInfo.project.name === 'desktop') {
    await page.locator('#model-cost-details').evaluate(d => d.open = true);
    await page.locator('#estimate-output').fill('500');
    await page.locator('#estimate-models').click();
  } else {
    await page.getByText('Estimate cheapest model').click();
    await page.locator('#mobile-output').fill('500');
    await page.locator('#mobile-estimate').click();
  }
  // 3. Await request observation
  await goEstReq;
  // 4. Switch to ZEN — triggers ZEN catalogue (immediate, no hold)
  const zenCatRes = page.waitForResponse(res => { try { const u = new URL(res.url()); return u.pathname === '/api/models' && u.searchParams.get('provider') === 'opencode'; } catch { return false; } });
  await form.locator('select[name="provider"]').selectOption('opencode');
  await zenCatRes;
  await flushFrames(page);
  // Verify ZEN model
  await expect(form.locator('input[name="model"]')).toHaveValue('opencode/deepseek-v4-flash');
  const datalist = testInfo.project.name === 'desktop' ? page.locator('#model-options') : page.locator('#mobile-models');
  await expect(datalist.locator('option')).toHaveCount(1);
  await expect(datalist.locator('option').first()).toHaveValue('opencode/deepseek-v4-flash');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('opencode-zen-default');
  await expect(form.locator('select[name="provider"]')).toHaveValue('opencode');
  // 5. Register waitForResponse for stale GO estimate
  const goEstRes = page.waitForResponse(res => { try { const u = new URL(res.url()); return u.pathname === '/api/models/estimate' && res.request().postDataJSON().provider === 'opencode-go'; } catch { return false; } });
  // 6. Release stale route
  goEstHold();
  // 7. Await response
  await goEstRes;
  await flushFrames(page);
  // 9. Assert ZEN state unchanged
  await expect(form.locator('input[name="model"]')).toHaveValue('opencode/deepseek-v4-flash');
  await expect(datalist.locator('option')).toHaveCount(1);
  await expect(datalist.locator('option').first()).toHaveValue('opencode/deepseek-v4-flash');
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('opencode-zen-default');
  await expect(form.locator('select[name="provider"]')).toHaveValue('opencode');
});

test('stale GO estimate failure while leaving opencode does not show error', async ({ page }, testInfo) => {
  await mockApi(page);
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  await form.locator('select[name="tool"]').selectOption('opencode');
  await form.locator('select[name="provider"]').selectOption('opencode-go');
  let goEstHold;
  const goEstHoldP = new Promise(r => { goEstHold = r; });
  await page.route('**/api/models/estimate**', async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname !== '/api/models/estimate') return route.fallback();
    const prov = route.request().postDataJSON().provider;
    if (prov === 'opencode-go') { await goEstHoldP; return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'stale failure' }) }); }
    return route.fallback();
  });
  // Register request wait, fire estimate, await request
  const estReq = page.waitForRequest(req => { try { const u = new URL(req.url()); return u.pathname === '/api/models/estimate' && req.postDataJSON().provider === 'opencode-go'; } catch { return false; } });
  if (testInfo.project.name === 'desktop') {
    await page.locator('#model-cost-details').evaluate(d => d.open = true);
    await page.locator('#estimate-output').fill('500');
    await page.locator('#estimate-models').click();
  } else {
    await page.getByText('Estimate cheapest model').click();
    await page.locator('#mobile-output').fill('500');
    await page.locator('#mobile-estimate').click();
  }
  await estReq;
  await flushFrames(page);
  // Switch to Codex while estimate is in flight
  await form.locator('select[name="tool"]').selectOption('codex');
  await flushFrames(page);
  // Verify Codex state
  await expect(form.locator('select[name="tool"]')).toHaveValue('codex');
  const statusEl = testInfo.project.name === 'desktop' ? page.locator('#model-status') : page.locator('#mobile-model-status');
  await expect(statusEl).not.toContainText('stale failure');
  await expect(form.locator('input[name="model"]')).toHaveValue('');
  const datalist = testInfo.project.name === 'desktop' ? page.locator('#model-options') : page.locator('#mobile-models');
  await expect(datalist.locator('option')).toHaveCount(0);
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('default');
  if (testInfo.project.name === 'desktop') {
    await expect(page.locator('#provider-field')).toBeHidden();
    await expect(page.locator('#model-field')).toBeHidden();
  } else {
    const controls = form.locator('.mobile-opencode');
    for (let i = 0; i < await controls.count(); i++) await expect(controls.nth(i)).toBeHidden();
  }
  // Register response wait, release stale, await response
  const estRes = page.waitForResponse(res => { try { const u = new URL(res.url()); return u.pathname === '/api/models/estimate' && res.request().postDataJSON().provider === 'opencode-go'; } catch { return false; } });
  goEstHold();
  await estRes;
  await flushFrames(page);
  // State unchanged
  await expect(form.locator('select[name="tool"]')).toHaveValue('codex');
  await expect(statusEl).not.toContainText('stale failure');
  await expect(form.locator('input[name="model"]')).toHaveValue('');
  await expect(datalist.locator('option')).toHaveCount(0);
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('default');
  if (testInfo.project.name === 'desktop') {
    await expect(page.locator('#provider-field')).toBeHidden();
    await expect(page.locator('#model-field')).toBeHidden();
  } else {
    const controls = form.locator('.mobile-opencode');
    for (let i = 0; i < await controls.count(); i++) await expect(controls.nth(i)).toBeHidden();
  }
});

test('catalogue failure clears model value, datalist, and context options', async ({ page }, testInfo) => {
  await mockApi(page);
  await page.route('**/api/models**', async (route) => {
    await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'catalogue error' }) });
  });
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  const catRes = page.waitForResponse(res => { try { return new URL(res.url()).pathname === '/api/models'; } catch { return false; } });
  await form.locator('select[name="tool"]').selectOption('opencode');
  await catRes;
  await flushFrames(page);
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(0);
  await expect(form.locator('input[name="model"]')).toHaveValue('');
  const datalist = testInfo.project.name === 'desktop' ? page.locator('#model-options') : page.locator('#mobile-models');
  await expect(datalist.locator('option')).toHaveCount(0);
  const statusEl = testInfo.project.name === 'desktop' ? page.locator('#model-status') : page.locator('#mobile-model-status');
  await expect(statusEl).toContainText('catalogue error');
});

test('leaving OpenCode while catalogue request is pending clears stale state', async ({ page }, testInfo) => {
  await mockApi(page);
  const form = testInfo.project.name === 'desktop' ? page.locator('#new-session') : page.locator('#mobile-new');
  if (testInfo.project.name !== 'desktop') {
    await page.goto('/mobile');
    await page.locator('[data-mobile-tab="new"]').click();
  } else {
    await page.goto('/desktop#new');
  }
  let goHold;
  const goHoldP = new Promise(r => { goHold = r; });
  await page.route('**/api/models**', async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname !== '/api/models') return route.fallback();
    if (u.searchParams.get('provider') === 'opencode-go') { await goHoldP; return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ provider: 'opencode-go', stale: false, models: [{ id: 'go-model', model: 'opencode-go/go-model', name: 'GO Model', status: 'active', selectable: true, cost: { input: 1, output: 2, cache_read: null, reasoning: null }, limits: { context: 1000, output: 100 }, capabilities: { reasoning: false, attachment: false, toolcall: true } }] }) }); }
    return route.fallback();
  });
  // Register request wait, select opencode, await request
  const catReq = page.waitForRequest(req => { try { const u = new URL(req.url()); return u.pathname === '/api/models' && u.searchParams.get('provider') === 'opencode-go'; } catch { return false; } });
  await form.locator('select[name="tool"]').selectOption('opencode');
  await catReq;
  // Switch to Codex while request is in flight
  await form.locator('select[name="tool"]').selectOption('codex');
  await flushFrames(page);
  // Verify Codex state
  await expect(form.locator('select[name="tool"]')).toHaveValue('codex');
  await expect(form.locator('input[name="model"]')).toHaveValue('');
  const datalist = testInfo.project.name === 'desktop' ? page.locator('#model-options') : page.locator('#mobile-models');
  await expect(datalist.locator('option')).toHaveCount(0);
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('default');
  await expect(form.locator('select[name="auth_context"]')).not.toContainText('opencode');
  if (testInfo.project.name === 'desktop') {
    await expect(page.locator('#provider-field')).toBeHidden();
    await expect(page.locator('#model-field')).toBeHidden();
  } else {
    const controls = form.locator('.mobile-opencode');
    for (let i = 0; i < await controls.count(); i++) await expect(controls.nth(i)).toBeHidden();
  }
  // Register response wait, release stale, await response
  const goRes = page.waitForResponse(res => { try { const u = new URL(res.url()); return u.pathname === '/api/models' && u.searchParams.get('provider') === 'opencode-go'; } catch { return false; } });
  goHold();
  await goRes;
  await flushFrames(page);
  // State remains unchanged after stale response arrives
  await expect(form.locator('select[name="tool"]')).toHaveValue('codex');
  await expect(form.locator('input[name="model"]')).toHaveValue('');
  await expect(datalist.locator('option')).toHaveCount(0);
  await expect(form.locator('select[name="auth_context"] option')).toHaveCount(1);
  await expect(form.locator('select[name="auth_context"] option').first()).toHaveValue('default');
  await expect(form.locator('select[name="auth_context"]')).not.toContainText('opencode');
  if (testInfo.project.name === 'desktop') {
    await expect(page.locator('#provider-field')).toBeHidden();
    await expect(page.locator('#model-field')).toBeHidden();
  } else {
    const controls = form.locator('.mobile-opencode');
    for (let i = 0; i < await controls.count(); i++) await expect(controls.nth(i)).toBeHidden();
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

test('embedded terminal renders content, has visible layout, and transmits changed resize payloads on dock resize', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop only.');
  await installFakeWebSocket(page); await mockApi(page); await page.goto('/desktop');
  await page.locator('#active-sessions .session-row').filter({ hasText: 'codex-root' }).locator('[data-attach]').click();
  await expect(page.locator('.terminal-embed')).toHaveCount(1);
  const iframe = await page.locator('.terminal-embed').first().elementHandle().then((el) => el.contentFrame());
  expect(iframe).toBeTruthy();
  await expect.poll(() => iframe.evaluate(() => document.body.classList.contains('terminal-embedded'))).toBeTruthy();
  const computedGrid = await iframe.evaluate(() => getComputedStyle(document.body).gridTemplateRows);
  expect(computedGrid.split(/\s+/).length).toBe(3);
  await expect.poll(() => iframe.evaluate(() => {
    const f = document.querySelector('.terminal-frame');
    return f ? f.getBoundingClientRect().height : 0;
  })).toBeGreaterThan(100);
  await expect.poll(() => iframe.evaluate(() => {
    const t = window.__terminal;
    return t ? t.buffer.active.length : 0;
  })).toBeGreaterThan(1);
  await expect.poll(() => iframe.evaluate(() => {
    const t = window.__terminal; if (!t) return false;
    for (let y = 0; y < Math.min(t.buffer.active.length, 20); y++) {
      if ((t.buffer.active.getLine(y)?.translateToString() || '').includes('terminal line')) return true;
    }
    return false;
  })).toBeTruthy();
  const initialResize = await iframe.evaluate(() => {
    const msgs = window.__wsSent || [];
    const s = msgs.find((m) => typeof m === 'string' && m.includes('"type":"resize"'));
    return s ? JSON.parse(s) : null;
  });
  expect(initialResize).toBeTruthy();
  expect(initialResize.cols).toBeGreaterThan(0);
  expect(initialResize.rows).toBeGreaterThan(0);
  const dockBefore = await page.locator('#terminal-dock').evaluate((el) => el.getBoundingClientRect().height);
  const handle = page.locator('#terminal-dock-handle');
  await expect(handle).toBeVisible();
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y - 120, { steps: 20 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const dockAfter = await page.locator('#terminal-dock').evaluate((el) => el.getBoundingClientRect().height);
  expect(dockAfter).not.toBe(dockBefore);
  await expect.poll(async () => {
    const msgs = await iframe.evaluate(() => window.__wsSent || []);
    const resizeStrs = msgs.filter((m) => typeof m === 'string' && m.includes('"type":"resize"'));
    const last = resizeStrs.length ? JSON.parse(resizeStrs[resizeStrs.length - 1]) : null;
    if (!last) return false;
    return last.rows !== initialResize.rows || last.cols !== initialResize.cols;
  }).toBeTruthy();
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

test('skills view renders skill cards from catalog with assignments and approval info', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop skills coverage.');
  await mockApi(page);
  await page.route('**/api/skills', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SKILLS_RESPONSE) });
  });
  await page.goto('/desktop#skills');
  await expect(page.locator('#skills-list')).toBeVisible();
  await expect(page.locator('.skill-card')).not.toHaveCount(0);
  const firstCard = page.locator('.skill-card').first();
  await expect(firstCard).toContainText('test-skill');
  await expect(firstCard).toContainText('standard');
  await expect(firstCard).toContainText('Standard skill · no approval gate');
  await expect(firstCard).toContainText('Assigned to:');
  await expect(firstCard).toContainText('coder');
  await expect(firstCard.getByRole('button', { name: 'Assign to profile', exact: true })).toBeVisible();
  await expect(firstCard.getByRole('button', { name: 'Remove from coder', exact: true })).toBeVisible();
  const superCard = page.locator('.skill-card').nth(1);
  await expect(superCard).toContainText('super-skill');
  await expect(superCard).toContainText('superpower');
  await expect(superCard).toContainText('Superpower · approval required');
});

test('skill card DOM order: header, description, tools before approval, assigned, actions', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop skills coverage.');
  await mockApi(page);
  await page.route('**/api/skills', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SKILLS_RESPONSE) });
  });
  await page.goto('/desktop#skills');
  await expect(page.locator('#skills-list')).toBeVisible();
  const order = await page.evaluate(() => {
    const card = document.querySelector('.skill-card');
    const children = Array.from(card.children);
    const classNames = children.map((el) => el.className);
    const tagNames = children.map((el) => el.tagName);
    const headerIdx = classNames.indexOf('skill-card-header');
    const descIdx = tagNames.indexOf('P');
    const toolsIdx = classNames.indexOf('skill-tools');
    const approvalIdx = classNames.indexOf('skill-approval-info');
    const assignedIdx = classNames.indexOf('skill-assigned-list');
    const actionsIdx = classNames.indexOf('dialog-actions');
    return { headerIdx, descIdx, toolsIdx, approvalIdx, assignedIdx, actionsIdx };
  });
  expect(order.headerIdx).not.toBe(-1);
  expect(order.descIdx).not.toBe(-1);
  expect(order.toolsIdx).not.toBe(-1);
  expect(order.approvalIdx).not.toBe(-1);
  expect(order.assignedIdx).not.toBe(-1);
  expect(order.actionsIdx).not.toBe(-1);
  expect(order.headerIdx).toBeLessThan(order.descIdx);
  expect(order.descIdx).toBeLessThan(order.toolsIdx);
  expect(order.toolsIdx).toBeLessThan(order.approvalIdx);
  expect(order.approvalIdx).toBeLessThan(order.assignedIdx);
  expect(order.assignedIdx).toBeLessThan(order.actionsIdx);
});

test('skill assign dialog opens and dispatches assign API call', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop skills coverage.');
  await mockApi(page);
  await page.route('**/api/skills', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SKILLS_RESPONSE) });
  });
  const assignRequest = page.waitForRequest((r) => r.url().includes('/api/skills/assign') && r.method() === 'POST');
  await page.route('**/api/skills/assign', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ profile: 'planner', skill_name: 'super-skill' }) });
  });
  await page.goto('/desktop#skills');
  await page.locator('.skill-card').nth(1).getByRole('button', { name: 'Assign to profile' }).click();
  await expect(page.locator('#assign-skill-dialog')).toBeVisible();
  await expect(page.locator('#assign-skill-title')).toContainText('Assign: super-skill');
  await page.locator('#assign-skill-form select[name="profile"]').selectOption('planner');
  await page.locator('#assign-skill-form button[type="submit"]').click();
  await assignRequest;
});

test('skill unassign button dispatches unassign API call', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop skills coverage.');
  await mockApi(page);
  await page.route('**/api/skills', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SKILLS_RESPONSE) });
  });
  const unassignRequest = page.waitForRequest((r) => r.url().includes('/api/skills/unassign') && r.method() === 'POST');
  await page.route('**/api/skills/unassign', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ profile: 'coder', skill_name: 'test-skill' }) });
  });
  await page.goto('/desktop#skills');
  await page.locator('.skill-card').first().getByRole('button', { name: 'Remove from coder' }).click();
  await unassignRequest;
});

test('orchestration view shows session groups with interactive controls', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop orchestration coverage.');
  await mockApi(page);
  await page.route('**/api/session-groups**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (method === 'GET') {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify([{ id: 'grp-1', name: 'test-group', purpose: 'coordinate work', status: 'active', member_count: 1, sessions: [{ tmux_name: 'child-1', profile: 'planner', tool: 'codex', status: 'detached', attention_state: 'normal', running: true }] }]),
      });
    } else if (url.pathname.endsWith('/members') && method === 'POST') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'grp-1', name: 'test-group', member_count: 2, sessions: [] }) });
    } else if (url.pathname.includes('/open') && method === 'POST') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ member_count: 1, available: [{ tmux_name: 'child-1' }], unavailable: [] }) });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'grp-1', name: 'test-group', member_count: 1, sessions: [{ tmux_name: 'child-1', profile: 'planner', tool: 'codex', status: 'detached', attention_state: 'normal', running: true }] }) });
    }
  });
  await page.goto('/desktop#orchestration');
  await expect(page.locator('#session-tree')).toBeVisible();
  await expect(page.locator('#session-tree')).toContainText('test-group');
  await expect(page.locator('#new-group-btn')).toBeVisible();
  await expect(page.locator('[data-group-open]').first()).toBeVisible();
  await page.locator('[data-group-open]').first().click();
  await expect(page.locator('#notice')).toContainText('Opened');
});

test('orchestration view shows session groups', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop orchestration coverage.');
  await mockApi(page);
  await page.route('**/api/session-groups', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'grp-1', name: 'test-group', purpose: 'coordinate work', status: 'active', sessions: [{ tmux_name: 'child-1', profile: 'planner', tool: 'codex', status: 'detached', attention_state: 'normal' }] }]),
    });
  });
  await page.goto('/desktop#orchestration');
  await expect(page.locator('#session-tree')).toBeVisible();
});

test('projects view shows project cards', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop projects coverage.');
  await mockApi(page);
  await page.route('**/api/projects', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'proj-1', name: 'test-project', repository: '/workspace/repo', description: 'A test project', status: 'active', session_count: 2 }]),
    });
  });
  await page.goto('/desktop#projects');
  await expect(page.locator('#projects-list')).toBeVisible();
  await expect(page.locator('.profile-card').first()).toContainText('test-project');
});

test('terminal displays session name in header, page title, and aria-label', async ({ page }, testInfo) => {
  await installFakeWebSocket(page);
  await mockApi(page);
  await page.goto('/terminal?session=codex-root');
  await expect(page).toHaveTitle(/Agent Terminal - codex-root/);
  await expect(page.locator('#session-name')).toHaveText('codex-root');
  await expect(page.locator('.terminal-frame')).toHaveAttribute('aria-label', 'Terminal session codex-root');
  // Dashboard terminal dock uses encoded name in iframe src (desktop only)
  if (testInfo.project.name === 'desktop') {
    await page.goto('/desktop');
    await page.locator('#active-sessions .session-row').filter({ hasText: 'codex-root' }).locator('[data-attach]').click();
    const dockFrame = page.locator('.terminal-embed').first();
    await expect(dockFrame).toHaveAttribute('src', /session=codex-root/);
    await expect(dockFrame).toHaveAttribute('title', 'Terminal codex-root');
  }
});

test('project detail dialog shows sessions with unassign', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop project detail coverage.');
  await mockApi(page);
  await page.route('**/api/projects', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'proj-1', name: 'detail-project', repository: '/workspace/repo', status: 'active', session_count: 1 }]),
    });
  });
  await page.route('**/api/projects/proj-1', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        id: 'proj-1', name: 'detail-project', repository: '/workspace/repo',
        description: '', status: 'active',
        sessions: [{ tmux_name: 'sess-1', tool: 'shell', profile: 'general', attention_state: 'normal', status: 'detached' }],
      }),
    });
  });
  await page.goto('/desktop#projects');
  await page.locator('button:has-text("View sessions")').click();
  await expect(page.locator('#project-detail-dialog')).toBeVisible();
  await expect(page.locator('#project-detail-dialog')).toContainText('sess-1');
  await expect(page.locator('#project-detail-dialog')).toContainText('Unassign');
});

test('new session form includes project selector', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Desktop new session coverage.');
  await mockApi(page);
  await page.route('**/api/projects', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([{ id: 'proj-1', name: 'selector-project', repository: '/repo', status: 'active', session_count: 0 }]),
    });
  });
  await page.goto('/desktop#new');
  const select = page.locator('select[name="project_id"]');
  await expect(select).toBeVisible();
  await expect(select).toContainText('selector-project');
});

test('terminal scroll-follow: connect, scroll-away, unread, manual-jump, resize', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Scroll-to-bottom coverage on desktop.');
  await installFakeWebSocket(page);
  await mockApi(page);
  await page.goto('/terminal?session=codex-root');

  // 1. Initial connect/reconnect scrolls to bottom
  await expect(page.locator('#new-output')).toBeHidden();
  expect(await page.evaluate(() => {
    const buf = window.__terminal?.buffer.active;
    return buf ? buf.viewportY >= buf.baseY : false;
  })).toBe(true);

  // 2. Deliberate scroll-away → button shows "Scroll to bottom"
  await page.waitForFunction(() => window.__terminal?.buffer.active.baseY > 0, { timeout: 5000 });
  await page.evaluate(() => {
    const term = window.__terminal;
    if (term) {
      term.buffer.active.viewportY = 0;
      term.scrollToBottom();
      term.scrollLines(-1);
    }
  });
  await expect(page.locator('#new-output')).toBeVisible();
  await expect(page.locator('#new-output')).toContainText('Scroll to bottom');
  expect(await page.evaluate(() => {
    const buf = window.__terminal?.buffer.active;
    return buf ? buf.viewportY < buf.baseY : false;
  })).toBe(true);

  // 3. New output while scrolled away → unread indicator, viewport preserved
  await page.evaluate(() => {
    const encoder = new TextEncoder();
    window.__fakeWs?.onmessage?.({ data: encoder.encode('\nnew unread output\n').buffer });
  });
  await expect(page.locator('#new-output')).toContainText('New output');
  await expect(page.locator('#new-output')).toContainText('Scroll to bottom');
  const btnClass = await page.locator('#new-output').getAttribute('class');
  expect(btnClass).toContain('has-unread');
  expect(await page.evaluate(() => {
    const buf = window.__terminal?.buffer.active;
    return buf ? buf.viewportY < buf.baseY : false;
  })).toBe(true);

  // 4. Manual jump (click) → scrolls to bottom, resumes following
  await page.locator('#new-output').click();
  await expect(page.locator('#new-output')).toBeHidden();
  expect(await page.evaluate(() => {
    const buf = window.__terminal?.buffer.active;
    return buf ? buf.viewportY === buf.baseY : false;
  })).toBe(true);
  // Following active — new output auto-scrolls, viewport stays at bottom
  await page.evaluate(() => {
    const encoder = new TextEncoder();
    window.__fakeWs?.onmessage?.({ data: encoder.encode('\nauto-followed output\n').buffer });
  });
  await expect(page.locator('#new-output')).toBeHidden();
  expect(await page.evaluate(() => {
    const buf = window.__terminal?.buffer.active;
    return buf ? buf.viewportY === buf.baseY : false;
  })).toBe(true);

  // 5. Window resize while following keeps at bottom
  await page.setViewportSize({ width: 800, height: 600 });
  await page.waitForTimeout(200);
  await expect(page.locator('#new-output')).toBeHidden();
  expect(await page.evaluate(() => {
    const buf = window.__terminal?.buffer.active;
    return buf ? buf.viewportY === buf.baseY : false;
  })).toBe(true);
});
