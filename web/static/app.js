import { initTheme } from '/static/theme.js?v=7';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { identity: null, sessions: [], plans: [], tree: { roots: [], delegations: [] }, view: 'sessions', selectedSession: null };
const viewTitles = { sessions: 'Sessions', orchestration: 'Orchestration', new: 'New session' };
const activeEl = $('#active-sessions');
const historyEl = $('#session-history');
const historyCountEl = $('#history-count');
const plansEl = $('#plans');
const treeEl = $('#session-tree');
const newForm = $('#new-session');
let currentModels = [];
const formStatus = $('#form-status');
const noticeEl = $('#notice');
const killDialog = $('#confirm-dialog');
const delegateDialog = $('#delegate-dialog');
const delegateForm = $('#delegate-form');
const planDialog = $('#plan-dialog');
const planForm = $('#plan-form');
const reviewDialog = $('#review-dialog');
const inspector = $('#session-inspector');
const attentionForm = $('#attention-form');
const terminalDock = $('#terminal-dock');
const terminalTabs = new Map();
let activeTerminal = null;

const attentionLabels = {
  normal: 'Normal',
  needs_input: 'Needs input',
  blocked: 'Blocked',
  ready_for_review: 'Ready for review',
};
const attentionPriority = { blocked: 0, needs_input: 1, ready_for_review: 2, normal: 3 };

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

function escapeHtml(value) {
  const node = document.createElement('div'); node.textContent = value ?? ''; return node.innerHTML;
}

function showNotice(message, kind = 'success') {
  noticeEl.textContent = message; noticeEl.dataset.kind = kind; noticeEl.hidden = false;
  clearTimeout(showNotice.timer); showNotice.timer = setTimeout(() => { noticeEl.hidden = true; }, 5000);
}

async function copyText(value) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return true; } catch { /* fallback */ }
  }
  const fallback = $('#clipboard-fallback');
  fallback.value = value; fallback.classList.remove('visually-hidden'); fallback.select();
  const copied = document.execCommand?.('copy') || false;
  fallback.classList.add('visually-hidden'); fallback.value = '';
  return copied;
}

async function copyName(name) {
  const copied = await copyText(name);
  showNotice(copied ? `Copied ${name}` : 'Copy was blocked by the browser', copied ? 'success' : 'error');
}

function formatActivity(value) {
  if (!value) return 'Unknown';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return 'Now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function attentionBadge(session) {
  const value = session.attention_state || 'normal';
  return `<span class="attention-badge ${value}">${escapeHtml(attentionLabels[value] || value)}</span>`;
}

function updateDockLayout() {
  const open = !terminalDock.hidden && terminalTabs.size > 0;
  document.body.classList.toggle('terminal-dock-open', open);
  const height = open ? (terminalDock.classList.contains('collapsed') ? 50 : terminalDock.getBoundingClientRect().height) : 0;
  document.documentElement.style.setProperty('--dock-height', `${Math.round(height)}px`);
}

function activateTerminal(name) {
  if (!terminalTabs.has(name)) return;
  activeTerminal = name;
  terminalDock.hidden = false;
  terminalDock.classList.remove('collapsed');
  terminalTabs.forEach(({ tab, frame }, tabName) => {
    const active = tabName === name;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    frame.hidden = !active;
  });
  updateDockLayout();
}

function closeTerminal(name) {
  const item = terminalTabs.get(name);
  if (!item) return;
  item.frame.src = 'about:blank';
  item.frame.remove(); item.tab.remove(); terminalTabs.delete(name);
  if (activeTerminal === name) {
    const next = [...terminalTabs.keys()].at(-1) || null;
    activeTerminal = null;
    if (next) activateTerminal(next);
  }
  if (!terminalTabs.size) terminalDock.hidden = true;
  updateDockLayout();
}

function openTerminal(name) {
  if (terminalTabs.has(name)) { activateTerminal(name); return; }
  if (terminalTabs.size >= 4) {
    showNotice('Four terminal tabs are already open. Close one before attaching another.', 'error');
    return;
  }
  const tab = document.createElement('button');
  tab.type = 'button'; tab.className = 'terminal-tab'; tab.setAttribute('role', 'tab');
  tab.innerHTML = `<span>${escapeHtml(name)}</span><span class="terminal-tab-close" role="button" aria-label="Close ${escapeHtml(name)}">×</span>`;
  tab.onclick = (event) => {
    if (event.target.closest('.terminal-tab-close')) { closeTerminal(name); return; }
    activateTerminal(name);
  };
  const frame = document.createElement('iframe');
  frame.className = 'terminal-embed'; frame.title = `Terminal ${name}`;
  frame.src = `/terminal?session=${encodeURIComponent(name)}&embed=1`; frame.hidden = true;
  $('#terminal-tabs').append(tab); $('#terminal-frames').append(frame);
  terminalTabs.set(name, { tab, frame }); activateTerminal(name);
}

function renderInspector(session) {
  state.selectedSession = session.tmux_name;
  $('#inspector-name').textContent = session.tmux_name;
  $('#inspector-content').innerHTML = `
    <div class="inspector-state">${attentionBadge(session)}<span class="badge ${session.running ? 'live' : 'stopped'}">${escapeHtml(session.live_state)}</span></div>
    ${session.attention_note ? `<p class="attention-note">${escapeHtml(session.attention_note)}</p>` : ''}
    <dl class="inspector-grid">
      <div><dt>Tool / mode</dt><dd>${escapeHtml(session.tool || 'legacy')} · ${escapeHtml(session.agent_mode || 'native')}</dd></div>
      <div><dt>Profile</dt><dd>${escapeHtml(session.profile || 'legacy')}</dd></div>
      <div><dt>Provider / model</dt><dd>${escapeHtml(session.provider || 'native')} · ${escapeHtml(session.model || 'default')}</dd></div>
      <div><dt>Account context</dt><dd>${escapeHtml(session.auth_context || 'legacy')}</dd></div>
      <div><dt>Repository</dt><dd class="mono">${escapeHtml(session.repository || 'None')}</dd></div>
      <div><dt>Worktree</dt><dd class="mono">${escapeHtml(session.worktree || 'None')}</dd></div>
      <div><dt>Parent / plan</dt><dd>${escapeHtml(session.parent_session || 'Root')} · ${escapeHtml(session.linked_plan_id || 'No plan')}</dd></div>
      <div><dt>Process / socket</dt><dd>${escapeHtml(session.current_command || 'None')} · ${escapeHtml(session.socket_scope)}</dd></div>
      <div><dt>Clients</dt><dd>${session.attached_clients} attached</dd></div>
      <div><dt>Last activity</dt><dd title="${escapeHtml(session.last_activity || '')}">${formatActivity(session.last_activity)}</dd></div>
      <div><dt>Attention updated</dt><dd>${escapeHtml(session.attention_updated_by || 'Never')} · ${formatActivity(session.attention_updated_at)}</dd></div>
    </dl>
    <div class="brief-block"><h3>Stored brief</h3><p>${escapeHtml(session.initial_task || 'No brief recorded.')}</p></div>`;
  attentionForm.elements.state.value = session.attention_state || 'normal';
  attentionForm.elements.note.value = session.attention_note || '';
  attentionForm.dataset.session = session.tmux_name; $('#attention-status').textContent = '';
  const actions = $('#inspector-actions'); actions.replaceChildren();
  const addButton = (label, handler, className = '') => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.className = className; button.onclick = handler; actions.append(button); return button;
  };
  if (session.actions.includes('attach')) addButton('Open terminal dock', () => openTerminal(session.tmux_name), 'primary');
  if (session.actions.includes('attach')) addButton('Open dedicated terminal', () => window.open(`/terminal?session=${encodeURIComponent(session.tmux_name)}`, '_blank', 'noopener'));
  addButton('Copy name', () => copyName(session.tmux_name));
  addButton('Review output', () => showReview(session.tmux_name));
  if (session.running) addButton('Delegate', () => openDelegate(session));
  for (const [operation, label] of [['interrupt', 'Interrupt'], ['restart', 'Restart agent'], ['kill', 'Kill']]) {
    if (!session.actions.includes(operation)) continue;
    const button = addButton(label, () => lifecycle(session, operation, button), operation === 'kill' ? 'danger' : '');
  }
  inspector.hidden = false;
  renderSessions();
}

function closeInspector() {
  inspector.hidden = true; state.selectedSession = null; renderSessions();
}

function selectView(view, updateHash = true) {
  state.view = viewTitles[view] ? view : 'sessions';
  $$('[data-view-panel]').forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== state.view; });
  $$('[data-view]').forEach((button) => {
    if (button.dataset.view === state.view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('#view-title').textContent = viewTitles[state.view];
  if (updateHash && location.hash !== `#${state.view}`) history.replaceState(null, '', `#${state.view}`);
}

function sessionMatches(session) {
  const search = $('#filter-search').value.trim().toLowerCase();
  if ($('#filter-tool').value && session.tool !== $('#filter-tool').value) return false;
  if ($('#filter-profile').value && session.profile !== $('#filter-profile').value) return false;
  if ($('#filter-state').value === 'active' && !session.running) return false;
  if ($('#filter-state').value === 'stopped' && session.running) return false;
  if ($('#filter-attention').value && (session.attention_state || 'normal') !== $('#filter-attention').value) return false;
  return !search || [session.tmux_name, session.repository, session.worktree, session.initial_task, session.attention_note, session.tool, session.profile]
    .some((value) => String(value || '').toLowerCase().includes(search));
}

function actionButton(session, operation, label, className = '') {
  return session.actions.includes(operation) ? `<button data-action="${operation}" class="${className}">${label}</button>` : '';
}

function confirmKill(session) {
  $('#confirm-title').textContent = 'Confirm tmux session kill';
  $('#confirm-text').textContent = `${session.tmux_name} · ${session.current_command || 'no pane process'} · ${session.attached_clients} attached client(s). The tmux session and child process will stop; worktrees, transcripts, plans, and history are preserved.`;
  const warning = $('#unmanaged-warning'); const check = $('#unmanaged-check'); const submit = $('#confirm-submit');
  warning.hidden = session.managed; check.checked = false; submit.disabled = !session.managed;
  check.onchange = () => { submit.disabled = !check.checked; };
  killDialog.showModal(); submit.focus();
  return new Promise((resolve) => killDialog.addEventListener('close', () => resolve(killDialog.returnValue === 'confirm'), { once: true }));
}

async function lifecycle(session, operation, button) {
  let body = {};
  if (operation === 'kill') {
    if (!await confirmKill(session)) return;
    body = { confirmed: true, allow_unmanaged: !session.managed, understand_unmanaged: !session.managed };
  }
  button.disabled = true; button.setAttribute('aria-busy', 'true');
  try {
    const result = await api(`/api/sessions/${encodeURIComponent(session.tmux_name)}/${operation}`, { method: 'POST', body: JSON.stringify(body) });
    if (operation === 'kill' && result.running !== false) throw new Error('Backend did not confirm session exit');
    if (operation === 'kill') closeTerminal(session.tmux_name);
    await refresh(); showNotice(`${session.tmux_name}: ${operation} completed`);
  } catch (error) {
    showNotice(error.message || String(error), 'error'); button.disabled = false; button.removeAttribute('aria-busy');
  }
}

async function showReview(name) {
  $('#review-title').textContent = `Review · ${name}`;
  $('#review-notice').textContent = 'Loading bounded read-only terminal output…';
  $('#review-content').textContent = ''; reviewDialog.showModal();
  try {
    const review = await api(`/api/sessions/${encodeURIComponent(name)}/review?lines=200`);
    $('#review-notice').textContent = `${review.notice} Source: ${review.source}${review.truncated ? ' · byte limit applied' : ''}.`;
    $('#review-content').textContent = review.content || 'No captured output is available.';
  } catch (error) { $('#review-notice').textContent = error.message || String(error); }
}

function updateContextSelect(toolSelect, contextSelect) {
  const contexts = state.identity.auth_contexts.filter((item) => item.tool === toolSelect.value && item.enabled !== false);
  contextSelect.replaceChildren(...contexts.map((item) => new Option(`${item.name} · ${item.status}`, item.name, false, item.default)));
}

function openDelegate(session) {
  delegateForm.reset(); delegateForm.elements.parent.value = session.tmux_name;
  delegateForm.elements.repository.value = session.repository || '';
  $('#delegate-parent').textContent = `Parent: ${session.tmux_name} · ${session.child_count}/${state.tree.max_children_per_parent || 3} children`;
  delegateForm.elements.profile.replaceChildren(...state.identity.profiles.filter((p) => p.read_write_capability === 'read_only').map((p) => new Option(p.display_name || p.name, p.name, false, p.name === 'planner')));
  const toolSelect = delegateForm.elements.tool;
  toolSelect.replaceChildren(...state.identity.tool_status.filter((item) => item.status === 'ready' && item.name !== 'claude').map((item) => new Option(item.name, item.name, false, item.name === state.identity.default_tool)));
  updateContextSelect(toolSelect, delegateForm.elements.auth_context);
  $('#delegate-mode-field').hidden = toolSelect.value !== 'opencode'; $('#delegate-status').textContent = '';
  delegateDialog.showModal();
}

function renderSession(session, historyRow = false) {
  const row = document.createElement('tr'); row.className = 'session-row'; row.tabIndex = 0;
  if (state.selectedSession === session.tmux_name) row.classList.add('selected');
  const attach = session.actions.includes('attach') ? '<button class="primary compact" data-attach>Attach</button>' : '';
  row.innerHTML = `<td><div class="state-stack">${attentionBadge(session)}<span class="badge ${session.running ? 'live' : 'stopped'}">${escapeHtml(session.live_state || (session.running ? 'tmux live' : 'stopped'))}</span></div></td><th scope="row"><span class="session-name">${escapeHtml(session.tmux_name)}</span>${session.attention_note ? `<span class="row-note">${escapeHtml(session.attention_note)}</span>` : ''}</th><td>${escapeHtml(session.tool || 'legacy')}<span class="subtle">${escapeHtml(session.agent_mode || session.provider || 'native')}</span></td><td>${escapeHtml(session.profile || 'legacy')}</td><td class="repo-cell" title="${escapeHtml(session.repository || session.worktree || 'No repository')}">${escapeHtml(session.repository || session.worktree || '—')}</td><td title="${escapeHtml(session.last_activity || '')}">${formatActivity(session.last_activity)}</td><td>${session.attached_clients}</td><td><div class="session-actions">${attach}<details class="action-menu"><summary aria-label="More actions">•••</summary><div class="action-menu-popover"><button data-copy-name>Copy name</button><button data-review>Review output</button>${session.running ? '<button data-delegate>Delegate</button>' : ''}${actionButton(session, 'interrupt', 'Interrupt')}${actionButton(session, 'restart', 'Restart agent')}${actionButton(session, 'kill', 'Kill', 'danger')}</div></details></div></td>`;
  $('[data-attach]', row)?.addEventListener('click', () => openTerminal(session.tmux_name));
  $('[data-copy-name]', row).onclick = () => copyName(session.tmux_name);
  $('[data-review]', row).onclick = () => showReview(session.tmux_name);
  $('[data-delegate]', row)?.addEventListener('click', () => openDelegate(session));
  $$('[data-action]', row).forEach((button) => button.addEventListener('click', () => lifecycle(session, button.dataset.action, button)));
  row.addEventListener('click', (event) => { if (!event.target.closest('button,a,summary,details')) renderInspector(session); });
  row.addEventListener('keydown', (event) => { if (event.key === 'Enter') renderInspector(session); });
  if (historyRow) row.classList.add('history-row');
  return row;
}

function renderSessions() {
  const filtered = state.sessions.filter(sessionMatches).sort((a, b) => {
    if (a.running !== b.running) return a.running ? -1 : 1;
    const attention = (attentionPriority[a.attention_state || 'normal'] ?? 9) - (attentionPriority[b.attention_state || 'normal'] ?? 9);
    if (attention) return attention;
    return String(b.last_activity || '').localeCompare(String(a.last_activity || ''));
  });
  const active = filtered.filter((session) => session.running); const stopped = filtered.filter((session) => !session.running);
  activeEl.replaceChildren(...active.map((session) => renderSession(session)));
  if (!active.length) activeEl.innerHTML = '<tr><td colspan="8" class="empty">No active sessions match these filters.</td></tr>';
  historyEl.replaceChildren(...stopped.map((session) => renderSession(session, true)));
  if (!stopped.length) historyEl.innerHTML = '<tr><td colspan="8" class="empty">No stopped sessions match these filters.</td></tr>';
  historyCountEl.textContent = String(stopped.length);
  const counts = { normal: 0, blocked: 0, needs_input: 0, ready_for_review: 0 };
  state.sessions.filter((session) => session.running).forEach((session) => { counts[session.attention_state || 'normal'] += 1; });
  $('#count-blocked').textContent = counts.blocked; $('#count-needs-input').textContent = counts.needs_input;
  $('#count-ready-review').textContent = counts.ready_for_review; $('#count-normal').textContent = counts.normal;
}

function renderTreeNode(session) {
  const node = document.createElement('article'); node.className = 'tree-node';
  node.innerHTML = `<div class="session-title"><h3>${escapeHtml(session.tmux_name)}</h3>${attentionBadge(session)}<span class="badge ${session.running ? 'live' : 'stopped'}">${escapeHtml(session.live_state)}</span></div><p class="meta">${escapeHtml(session.tool || 'legacy')} · ${escapeHtml(session.profile || 'legacy')} · ${escapeHtml(session.current_command || 'no process')}</p><p class="meta">${escapeHtml(session.repository || 'No repository')}${session.linked_plan_id ? ` · plan ${escapeHtml(session.linked_plan_id)}` : ''}</p><div class="tree-node-actions">${session.running ? '<button class="primary" data-attach>Attach</button>' : ''}<button data-copy>Copy name</button><button data-review>Review</button>${session.running ? '<button data-delegate>Delegate</button>' : ''}</div>`;
  $('[data-attach]', node)?.addEventListener('click', () => openTerminal(session.tmux_name));
  $('[data-copy]', node).onclick = () => copyName(session.tmux_name);
  $('[data-review]', node).onclick = () => showReview(session.tmux_name);
  $('[data-delegate]', node)?.addEventListener('click', () => openDelegate(session));
  if (session.children?.length) {
    const children = document.createElement('div'); children.className = 'tree-node-children';
    children.replaceChildren(...session.children.map(renderTreeNode)); node.append(children);
  }
  return node;
}

function renderOrchestration() {
  treeEl.replaceChildren(...state.tree.roots.map(renderTreeNode));
  if (!state.tree.roots.length) treeEl.innerHTML = '<p class="empty">No sessions discovered.</p>';
  plansEl.replaceChildren(...state.plans.map((plan) => {
    const row = document.createElement('article'); row.className = 'plan-row';
    row.innerHTML = `<h3>${escapeHtml(plan.title || plan.id)}</h3><p class="meta">${escapeHtml(plan.status)} · ${escapeHtml(plan.repository || 'repository in metadata')}</p><button data-preview>Preview and execute</button>`;
    $('[data-preview]', row).onclick = () => openPlan(plan.id); return row;
  }));
  if (!state.plans.length) plansEl.innerHTML = '<p class="empty">No shared plans found.</p>';
}

async function openPlan(planId) {
  planForm.reset(); $('#plan-title').textContent = 'Loading plan…'; $('#plan-meta').textContent = ''; $('#plan-content').textContent = ''; $('#plan-status').textContent = '';
  planDialog.showModal();
  try {
    const plan = await api(`/api/plans/${encodeURIComponent(planId)}`);
    planForm.elements.plan_id.value = plan.id; $('#plan-title').textContent = plan.title || plan.id;
    $('#plan-meta').textContent = `${plan.status} · ${plan.repository || 'No repository'} · revision ${plan.revision_state}`;
    $('#plan-content').textContent = plan.plan; $('#revision-warning').hidden = plan.revision_state !== 'changed';
  } catch (error) { $('#plan-status').textContent = error.message || String(error); }
}

function updateAgentModeField() {
  const tool = newForm.elements.tool.value;
  const profile = newForm.elements.profile.value;
  const field = $('#agent-mode-field');
  const select = newForm.elements.agent_mode;
  const p = state.identity.profiles.find(x => x.name === profile);
  const readOnly = p ? p.read_write_capability === 'read_only' : false;
  const visible = tool === 'codex' || tool === 'opencode';
  field.hidden = !visible; select.disabled = !visible;
  if (!visible) return;
  const choices = tool === 'codex'
    ? (readOnly ? [['plan', 'Plan']] : [['auto', 'Auto'], ['plan', 'Plan']])
    : (readOnly ? [['plan', 'Plan']] : [['plan', 'Plan'], ['build', 'Build']]);
  const previous = select.value;
  select.replaceChildren(...choices.map(([value, label]) => new Option(label, value)));
  const fallback = tool === 'codex' && !readOnly ? 'auto' : 'plan';
  const preserve = field.dataset.tool === tool && choices.some(([value]) => value === previous);
  select.value = preserve ? previous : fallback; field.dataset.tool = tool;
  $('#agent-mode-label').textContent = `${tool === 'codex' ? 'Codex' : 'OpenCode'} mode`;
  $('#agent-mode-help').textContent = tool === 'codex'
    ? (select.value === 'plan' ? 'Read-only planning; no file or system changes.' : 'Workspace-write with approvals on request; not unrestricted host access.')
    : (select.value === 'plan' ? 'Planning is the default.' : 'Build is explicitly write-capable.');
  select.onchange = updateAgentModeField;
}

function updateNewToolFields() {
  const tool = newForm.elements.tool.value;
  const catalog = state.identity.tool_status.find((item) => item.name === tool);
  updateContextSelect(newForm.elements.tool, newForm.elements.auth_context);
  updateAgentModeField();
  $('#provider-field').hidden = tool !== 'opencode'; newForm.elements.provider.disabled = tool !== 'opencode';
  $('#model-field').hidden = tool !== 'opencode'; newForm.elements.model.disabled = tool !== 'opencode';
  if (tool === 'opencode') loadModels();
  formStatus.textContent = catalog?.status === 'ready' ? '' : `${catalog?.status || 'unknown'}: ${catalog?.reason || 'configuration required'}`;
}

async function loadModels() {
  const provider = newForm.elements.provider.value || 'opencode-go';
  $('#model-status').textContent = 'Loading current catalogue…';
  try {
    const catalogue = await api(`/api/models?provider=${encodeURIComponent(provider)}`);
    currentModels = catalogue.models; renderModelOptions(currentModels);
    const preferred = catalogue.models.find((model) => model.selectable)?.model || '';
    const current = catalogue.models.find((model) => model.model === newForm.elements.model.value && model.selectable);
    if (!current) newForm.elements.model.value = preferred;
    const contexts = state.identity.auth_contexts.filter((item) => item.tool === 'opencode' && item.provider === provider);
    newForm.elements.auth_context.replaceChildren(...contexts.map((item) => new Option(`${item.name} · ${item.status}`, item.name, false, item.default)));
    $('#model-status').textContent = `${catalogue.models.length} models, lowest output-token cost first${catalogue.stale ? ' · cached catalogue' : ''}. Default: ${preferred || 'none available'}.`;
  } catch (error) { $('#model-status').textContent = error.message; }
}

function renderModelOptions(models) {
  $('#model-options').replaceChildren(...models.map((model) => {
    const cost = model.estimated_usd == null
      ? (model.cost.output == null ? 'unknown output cost' : `$${model.cost.output}/M output`)
      : `est. $${model.estimated_usd.toFixed(6)}${model.cheapest ? ' · CHEAPEST' : ''}`;
    const option = new Option(`${model.name} · ${cost}${model.selectable ? '' : ` · ${model.status}`}`, model.model);
    option.disabled = !model.selectable; return option;
  }));
}

async function estimateModelUsage() {
  const payload = {
    provider: newForm.elements.provider.value,
    uncached_input_tokens: Number($('#estimate-uncached').value || 0),
    cached_input_tokens: Number($('#estimate-cached').value || 0),
    output_tokens: Number($('#estimate-output').value || 0),
    reasoning_tokens: Number($('#estimate-reasoning').value || 0),
  };
  try {
    const result = await api('/api/models/estimate', { method: 'POST', body: JSON.stringify(payload) });
    currentModels = result.models; renderModelOptions(currentModels);
    const cheapest = currentModels.find((model) => model.cheapest);
    if (cheapest) newForm.elements.model.value = cheapest.model;
    $('#model-status').textContent = cheapest ? `Selected cheapest for this token mix: ${cheapest.model} · estimated $${cheapest.estimated_usd.toFixed(6)}. Actual session usage varies.` : 'No priced selectable model can be estimated for this token mix.';
  } catch (error) { $('#model-status').textContent = error.message; }
}

async function refresh() {
  const [sessions, plans, tree] = await Promise.all([api('/api/sessions?state=all'), api('/api/plans'), api('/api/delegations')]);
  state.sessions = sessions; state.plans = plans; state.tree = tree;
  for (const name of [...terminalTabs.keys()]) {
    const session = sessions.find((item) => item.tmux_name === name);
    if (!session?.running) closeTerminal(name);
  }
  renderSessions(); renderOrchestration();
  if (state.selectedSession) {
    const selected = sessions.find((item) => item.tmux_name === state.selectedSession);
    if (selected) renderInspector(selected); else closeInspector();
  }
}

async function start() {
  initTheme([$('#theme-select'), $('#mobile-theme-select')]);
  const layout = localStorage.getItem('agent-console-layout') || 'auto'; $('#layout-select').value = layout;
  $('#layout-select').onchange = () => { const value=$('#layout-select').value; localStorage.setItem('agent-console-layout', value); if(value==='mobile') location.href='/mobile'; };
  state.identity = await api('/api/me'); $('#identity').textContent = `${state.identity.login} · ${state.identity.access_surface}`;
  const readyCount = state.identity.tool_status.filter((item) => item.status === 'ready').length;
  $('#provider-summary').textContent = `${readyCount}/${state.identity.tool_status.length} providers ready`;
  $('#provider-status').replaceChildren(...state.identity.tool_status.map((item) => {
    const pill = document.createElement('span'); pill.className = `status-pill ${item.status}`; pill.textContent = `${item.name}: ${item.status}`; pill.title = item.reason || ''; return pill;
  }));
  newForm.elements.tool.replaceChildren(...state.identity.tool_status.map((item) => {
    const option = new Option(`${item.name} · ${item.status}`, item.name, false, item.name === state.identity.default_tool); option.disabled = item.status === 'disabled'; return option;
  }));
  newForm.elements.profile.replaceChildren(...state.identity.profiles.map((p) => new Option(p.display_name || p.name, p.name, false, p.name === 'general')));
  $('#filter-tool').append(...state.identity.tool_status.map((item) => new Option(item.name, item.name)));
  $('#filter-profile').append(...state.identity.profiles.map((p) => new Option(p.display_name || p.name, p.name)));
  newForm.elements.tool.onchange = updateNewToolFields;
  newForm.elements.provider.onchange = loadModels;
  $('#estimate-models').onclick = estimateModelUsage;
  const presets = {
    small: [4000, 500, 1000, 0],
    coding: [25000, 10000, 4000, 1000],
    review: [100000, 50000, 3000, 1000],
    custom: [0, 0, 0, 0],
  };
  $$('[data-cost-preset]').forEach((button) => button.onclick = () => {
    const values = presets[button.dataset.costPreset];
    [$('#estimate-uncached'), $('#estimate-cached'), $('#estimate-output'), $('#estimate-reasoning')].forEach((input, index) => { input.value = values[index]; });
    if (button.dataset.costPreset !== 'custom') estimateModelUsage(); else $('#estimate-uncached').focus();
  });
  newForm.elements.profile.onchange = () => { const p = state.identity.profiles.find(x => x.name === newForm.elements.profile.value); newForm.elements.worktree.checked = p ? p.worktree_requirement !== 'none' : false; updateAgentModeField(); };
  delegateForm.elements.tool.onchange = () => { updateContextSelect(delegateForm.elements.tool, delegateForm.elements.auth_context); $('#delegate-mode-field').hidden = delegateForm.elements.tool.value !== 'opencode'; };
  updateNewToolFields(); selectView(location.hash.slice(1) || 'sessions', false); await refresh();
  setInterval(() => { if (!document.hidden && !$$('dialog').some((dialog) => dialog.open)) refresh().catch((error) => showNotice(error.message, 'error')); }, 10000);
}

$$('[data-view]').forEach((button) => button.addEventListener('click', () => selectView(button.dataset.view)));
window.addEventListener('hashchange', () => selectView(location.hash.slice(1), false));
$('#rail-toggle').onclick = () => { document.body.classList.toggle('rail-collapsed'); const collapsed = document.body.classList.contains('rail-collapsed'); $('#rail-toggle').setAttribute('aria-label', collapsed ? 'Expand navigation' : 'Collapse navigation'); };
$$('[data-close]').forEach((button) => button.addEventListener('click', () => document.getElementById(button.dataset.close).close()));
$$('#filter-search, #filter-tool, #filter-profile, #filter-state, #filter-attention').forEach((control) => control.addEventListener('input', renderSessions));
$$('[data-attention-filter]').forEach((button) => button.onclick = () => { $('#filter-attention').value = button.dataset.attentionFilter; renderSessions(); });
$('#inspector-close').onclick = closeInspector;

attentionForm.onsubmit = async (event) => {
  event.preventDefault(); const name = attentionForm.dataset.session; const submit = $('button[type="submit"]', attentionForm);
  submit.disabled = true; $('#attention-status').textContent = 'Updating…';
  try {
    const session = await api(`/api/sessions/${encodeURIComponent(name)}/attention`, { method: 'PATCH', body: JSON.stringify({ state: attentionForm.elements.state.value, note: attentionForm.elements.note.value || null }) });
    const index = state.sessions.findIndex((item) => item.tmux_name === name); if (index >= 0) state.sessions[index] = session;
    renderInspector(session); renderSessions(); renderOrchestration(); $('#attention-status').textContent = 'State updated';
  } catch (error) { $('#attention-status').textContent = error.message; } finally { submit.disabled = false; }
};

$('#terminal-dock-collapse').onclick = () => { terminalDock.classList.toggle('collapsed'); updateDockLayout(); };
$('#terminal-dock-fullscreen').onclick = async () => {
  if (document.fullscreenElement === terminalDock) await document.exitFullscreen();
  else if (terminalDock.requestFullscreen) await terminalDock.requestFullscreen();
  else terminalDock.classList.toggle('forced-fullscreen');
};
$('#terminal-dock-handle').addEventListener('pointerdown', (event) => {
  const startY = event.clientY; const startHeight = terminalDock.getBoundingClientRect().height;
  const move = (moveEvent) => {
    const height = Math.max(240, Math.min(window.innerHeight * 0.82, startHeight + startY - moveEvent.clientY));
    terminalDock.style.height = `${Math.round(height)}px`;
    updateDockLayout();
  };
  const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop, { once: true });
});

window.addEventListener('keydown', (event) => {
  const target = event.target;
  if (target.closest('input,textarea,select,button,dialog,[contenteditable="true"],.terminal-dock')) return;
  const active = state.sessions.filter(sessionMatches).filter((session) => session.running);
  const currentIndex = Math.max(0, active.findIndex((session) => session.tmux_name === state.selectedSession));
  if (event.key === '/') { event.preventDefault(); $('#filter-search').focus(); }
  else if (event.key.toLowerCase() === 'n') selectView('new');
  else if (event.key.toLowerCase() === 't' && state.selectedSession) openTerminal(state.selectedSession);
  else if (event.key === 'Escape' && !inspector.hidden) closeInspector();
  else if (event.key === 'ArrowDown' && active.length) { event.preventDefault(); renderInspector(active[Math.min(active.length - 1, currentIndex + 1)]); }
  else if (event.key === 'ArrowUp' && active.length) { event.preventDefault(); renderInspector(active[Math.max(0, currentIndex - 1)]); }
  else if (event.key === 'Enter' && state.selectedSession) {
    const session = state.sessions.find((item) => item.tmux_name === state.selectedSession); if (session) renderInspector(session);
  }
});

newForm.onsubmit = async (event) => {
  event.preventDefault(); formStatus.textContent = 'Creating…'; const submit = $('button[type="submit"]', newForm); submit.disabled = true;
  const data = Object.fromEntries(new FormData(newForm)); data.worktree = newForm.elements.worktree.checked;
  for (const key of ['name', 'task', 'agent_mode', 'provider', 'model']) if (!data[key]) data[key] = null;
  try { const session = await api('/api/sessions', { method: 'POST', body: JSON.stringify(data) }); await refresh(); selectView('sessions'); renderInspector(session); openTerminal(session.tmux_name); submit.disabled = false; }
  catch (error) { formStatus.textContent = error.message; submit.disabled = false; }
};

delegateForm.onsubmit = async (event) => {
  event.preventDefault(); const submit = $('button[type="submit"]', delegateForm); submit.disabled = true; $('#delegate-status').textContent = 'Creating read-only child…';
  const data = Object.fromEntries(new FormData(delegateForm)); const parent = data.parent; delete data.parent;
  for (const key of ['name', 'repository', 'agent_mode', 'auth_context']) if (!data[key]) data[key] = null;
  try { const result = await api(`/api/sessions/${encodeURIComponent(parent)}/delegations`, { method: 'POST', body: JSON.stringify(data) }); delegateDialog.close(); await refresh(); showNotice(`Created ${result.session.tmux_name}`); }
  catch (error) { $('#delegate-status').textContent = error.message; } finally { submit.disabled = false; }
};

planForm.onsubmit = async (event) => {
  event.preventDefault(); const submit = $('button[type="submit"]', planForm); submit.disabled = true; $('#plan-status').textContent = 'Creating isolated implementation session…';
  const data = Object.fromEntries(new FormData(planForm)); const planId = data.plan_id; delete data.plan_id; data.confirmed = true;
  data.allow_revision_change = planForm.elements.allow_revision_change.checked; if (!data.name) data.name = null;
  try { const session = await api(`/api/plans/${encodeURIComponent(planId)}/execute`, { method: 'POST', body: JSON.stringify(data) }); planDialog.close(); await refresh(); selectView('sessions'); renderInspector(session); openTerminal(session.tmux_name); }
  catch (error) { $('#plan-status').textContent = error.message; submit.disabled = false; }
};

$('#refresh').onclick = async (event) => {
  event.currentTarget.disabled = true;
  try { await refresh(); showNotice('Console refreshed'); } catch (error) { showNotice(error.message, 'error'); }
  finally { event.currentTarget.disabled = false; }
};

start().catch((error) => showNotice(error.message || String(error), 'error'));
