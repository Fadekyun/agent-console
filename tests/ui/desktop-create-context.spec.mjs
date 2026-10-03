import {test,expect} from '@playwright/test';
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


for(const succeeds of [true,false])test(`late delegation ${succeeds?'success':'failure'} preserves a reopened child form`,async({page})=>{
  await mockApi(page);let release,completed=false;
  await page.route('**/api/sessions/codex-root/delegations',async route=>{await new Promise(resolve=>release=resolve);await route.fulfill(succeeds?{json:{session:session({tmux_name:'new-child'})}}:{status:409,json:{detail:'Original child could not start'}});completed=true;});
  await page.goto('/desktop#orchestration');await page.locator('.tree-node').first().locator('[data-delegate]').first().click();
  await page.locator('#delegate-form textarea[name=task]').fill('Original child');await page.locator('#delegate-form button[type=submit]').click();await expect.poll(()=>typeof release).toBe('function');
  await page.locator('[data-close=delegate-dialog]').click();await page.locator('.tree-node').first().locator('[data-delegate]').first().click();await page.locator('#delegate-form textarea[name=task]').fill('New draft');
  release();await expect.poll(()=>completed).toBe(true);await page.waitForTimeout(200);
  await expect(page.locator('#delegate-dialog')).toBeVisible();await expect(page.locator('#delegate-form textarea[name=task]')).toHaveValue('New draft');await expect(page.locator('#delegate-form button[type=submit]')).toBeEnabled();await expect(page.locator('#delegate-status')).toBeEmpty();
});
for(const returnToForm of [false,true])test(`late new-session completion respects navigation${returnToForm?' even after returning to the form':''}`,async({page})=>{
  await mockApi(page);let release,completed=false;
  await page.route('**/api/sessions',async route=>{if(route.request().method()!=='POST')return route.fallback();await new Promise(resolve=>release=resolve);await route.fulfill({json:session({id:'new',tmux_name:'newly-created'})});completed=true;});
  await page.goto('/desktop#new');await page.locator('#new-session textarea[name=task]').fill('Create a task');await page.locator('#new-session button[type=submit]').click();await expect.poll(()=>typeof release).toBe('function');
  await page.locator('[data-view=orchestration]:visible').click();if(returnToForm)await page.locator('[data-view=new]:visible').first().click();
  release();await expect.poll(()=>completed).toBe(true);await page.waitForTimeout(200);
  await expect(page.locator('#view-title')).toHaveText(returnToForm?'New session':'Orchestration');await expect(page.locator('#terminal-dock')).toBeHidden();await expect(page.locator('#notice')).toContainText('newly-created');
});

for(const succeeds of [true,false])test(`late review ${succeeds?'output':'error'} stays out of another session's dialog`,async({page})=>{
  await mockApi(page);let release,completed=false;
  await page.route('**/api/sessions/codex-root/review?*',async route=>{expect(new URL(route.request().url()).searchParams.get('session_id')).toBe('sess-root');await new Promise(resolve=>release=resolve);await route.fulfill(succeeds?{json:{notice:'Original root output',source:'live-pane',content:'OLD_ROOT_OUTPUT'}}:{status:503,json:{detail:'Original root review failed'}});completed=true;});
  await page.goto('/desktop#orchestration');await page.locator('.tree-node').first().locator('[data-review]').first().click();await expect.poll(()=>typeof release).toBe('function');await page.locator('#review-dialog button').click();
  await page.locator('.tree-node').filter({has:page.locator('h3',{hasText:'opencode-scout'})}).last().locator('[data-review]').click();await expect(page.locator('#review-content')).toContainText('PEER_OUTPUT');
  release();await expect.poll(()=>completed).toBe(true);await page.waitForTimeout(200);await expect(page.locator('#review-title')).toContainText('opencode-scout');await expect(page.locator('#review-content')).toHaveText('PEER_OUTPUT\n');await expect(page.locator('#review-notice')).not.toContainText('Original root');
});


test('current new-session submission still opens its created session',async({page},info)=>{
  await mockApi(page);
  await page.route('**/api/sessions',route=>route.request().method()==='POST'?route.fulfill({json:session({id:'new',tmux_name:'newly-created'})}):route.fallback());
  await page.goto('/desktop#new');await page.locator('#new-session textarea[name=task]').fill('Create a task');await page.locator('#new-session button[type=submit]').click();
  if(info.project.name==='desktop'){await expect(page.locator('#view-title')).toHaveText('Sessions');await expect(page.locator('#terminal-dock')).toBeVisible();await expect(page.locator('#terminal-dock iframe')).toHaveAttribute('src',/session=newly-created/);}
  else await expect(page).toHaveURL(/\/terminal\?session=newly-created/);
});
