import { initTheme } from '/static/theme.js?v=8';
import { skillActionMessage, skillToolDiagnostic } from '/static/skill-diagnostics.js?v=1';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { identity: null, sessions: [], plans: [], tree: { roots: [], delegations: [] }, view: 'sessions', selectedSession: null };
const viewTitles = { sessions: 'Sessions', projects: 'Projects', profiles: 'Profiles', skills: 'Skills', orchestration: 'Orchestration', new: 'New session' };
const activeEl = $('#active-sessions');
const historyEl = $('#session-history');
const historyCountEl = $('#history-count');
const plansEl = $('#plans');
const treeEl = $('#session-tree');
const newForm = $('#new-session');
let currentModels = [];
let loadModelsReq = 0;
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
const groupDialog = $('#group-dialog');
const groupForm = $('#group-form');
const groupDetailDialog = $('#group-detail-dialog');
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
  if (!response.ok) {
    const detail = body.detail;
    const message = Array.isArray(detail)
      ? detail.map(e => e.msg || JSON.stringify(e)).join('; ')
      : detail || `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
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
  if (open && activeTerminal) {
    const active = terminalTabs.get(activeTerminal);
    if (active?.frame && !active.frame.hidden) {
      requestAnimationFrame(() => { try { active.frame.contentWindow?.dispatchEvent(new Event('resize')); } catch { /* cross-origin guard, unreachable same-origin */ } });
    }
  }
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
  requestAnimationFrame(() => { requestTerminalFocus(name); });
}

function requestTerminalFocus(name) {
  const item = terminalTabs.get(name);
  if (!item) return;
  if (activeTerminal !== name || item.frame.hidden) return;
  try {
    item.frame.contentWindow?.postMessage({ type: 'agent-console:focus-terminal' }, window.location.origin);
  } catch { /* same-origin, unreachable */ }
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
  frame.addEventListener('load', () => {
    requestTerminalFocus(name);
  });
  $('#terminal-tabs').append(tab); $('#terminal-frames').append(frame);
  terminalTabs.set(name, { tab, frame }); activateTerminal(name);
}

function renderWaitStatus(session) {
  const el = $('#inspector-wait-status');
  if (!session.total_child_count) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '<h3>Wait cycle</h3><p class="muted">Loading wait status…</p>';
  api(`/api/sessions/${encodeURIComponent(session.tmux_name)}/wait-status`).then((data) => {
    if (!data) {
      el.innerHTML = '<h3>Wait cycle</h3><p class="muted">No wait initiated yet.</p>';
      return;
    }
    const outcome = data.outcome || 'unknown';
    const outcomeClass = outcome === 'success' ? 'live' : outcome === 'timeout' ? 'stopped' : 'danger';
    const summary = data.summary || {};
    const childrenHtml = (summary.children || []).map((c) =>
      `<span class="wait-child">${escapeHtml(c.tmux_name)}: <strong>${c.wait_status || 'unknown'}</strong></span>`
    ).join('');
    el.innerHTML = `<h3>Wait cycle</h3>
      <p>Outcome: <strong class="badge ${outcomeClass}">${outcome}</strong>${data.completed_at ? ` · ${formatActivity(data.completed_at)}` : ''}</p>
      ${childrenHtml ? `<div class="wait-children">${childrenHtml}</div>` : ''}`;
  }).catch(() => {
    el.innerHTML = '<h3>Wait cycle</h3><p class="muted">Unable to load wait status.</p>';
  });
}

async function waitForChildren(name, button) {
  button.disabled = true; button.textContent = 'Waiting…';
  try {
    const result = await api(`/api/sessions/${encodeURIComponent(name)}/wait-for-children`, {
      method: 'POST', body: JSON.stringify({ timeout: 120, poll_interval: 5 }),
    });
    const outcome = result.outcome || 'unknown';
    showNotice(`Wait outcome: ${outcome} (exit code ${result.exit_code})`, outcome === 'success' ? 'success' : outcome === 'intervention' ? 'warning' : 'error');
    await refresh();
  } catch (error) {
    showNotice(error.message || String(error), 'error');
  } finally {
    button.disabled = false; button.textContent = 'Wait for children';
  }
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
      <div><dt>Children</dt><dd>${session.child_count || 0} / ${session.total_child_count || 0} active</dd></div>
      <div><dt>Last activity</dt><dd title="${escapeHtml(session.last_activity || '')}">${formatActivity(session.last_activity)}</dd></div>
      <div><dt>Attention updated</dt><dd>${escapeHtml(session.attention_updated_by || 'Never')} · ${formatActivity(session.attention_updated_at)}</dd></div>
    </dl>
    <div class="brief-block"><h3>Stored brief</h3><p>${escapeHtml(session.initial_task || 'No brief recorded.')}</p></div>
    <div id="inspector-wait-status"></div>`;
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
  if (session.total_child_count) {
    const waitBtn = addButton('Wait for children', () => waitForChildren(session.tmux_name, waitBtn));
  }
  for (const [operation, label] of [['interrupt', 'Interrupt'], ['restart', 'Restart agent'], ['kill', 'Kill']]) {
    if (!session.actions.includes(operation)) continue;
    const button = addButton(label, () => lifecycle(session, operation, button), operation === 'kill' ? 'danger' : '');
  }
  inspector.hidden = false;
  renderWaitStatus(session);
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
  if (state.view === 'profiles') renderProfiles();
  if (state.view === 'skills') renderSkills();
  if (state.view === 'projects') renderProjects();
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

async function openDelegate(session) {
  delegateForm.reset(); delegateForm.elements.parent.value = session.tmux_name;
  delegateForm.elements.repository.value = session.repository || '';
  $('#delegate-parent').textContent = `Parent: ${session.tmux_name} · ${session.child_count}/${state.tree.max_children_per_parent || 3} children`;
  try {
    const allProfiles = await api('/api/profiles');
    const parentProfile = session.profile || 'general';
    const parentMeta = allProfiles.find((p) => p.name === parentProfile) || {};
    const allowed = parentMeta.allowed_delegation_profiles || [];
    delegateForm.elements.profile.replaceChildren(...allowed.map((name) => {
      const p = state.identity.profiles.find((x) => x.name === name) || {};
      return new Option(p.display_name || name, name, false, name === 'planner');
    }));
  } catch {
    delegateForm.elements.profile.replaceChildren(...state.identity.profiles.filter((p) => p.read_write_capability === 'read_only').map((p) => new Option(p.display_name || p.name, p.name, false, p.name === 'planner')));
  }
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

async function renderOrchestration() {
  const [groups, tree] = await Promise.all([
    api('/api/session-groups').catch(() => []),
    Promise.resolve(state.tree),
  ]);
  const groupEl = document.createElement('div'); groupEl.className = 'panel';
  groupEl.innerHTML = '<div class="panel-heading"><h3>Session groups</h3><button id="new-group-btn" class="compact">New group</button></div>';
  const groupList = document.createElement('div'); groupList.className = 'tree-list';
  if (groups.length) {
    groupList.replaceChildren(...groups.map(renderGroupCard));
  } else {
    groupList.innerHTML = '<p class="empty">No session groups yet. Create one to coordinate multiple sessions.</p>';
  }
  groupEl.append(groupList);
  $('#new-group-btn')?.addEventListener('click', openNewGroup);
  treeEl.replaceChildren(groupEl, ...state.tree.roots.map(renderTreeNode));
  if (!state.tree.roots.length && !groups.length) treeEl.innerHTML = '<p class="empty">No sessions discovered.</p>';
  const activePlans = state.plans.filter(p => p.status === 'planned' || p.status === 'executing');
  plansEl.replaceChildren(...activePlans.map((plan) => {
    const row = document.createElement('article'); row.className = 'plan-row';
    row.innerHTML = `<h3>${escapeHtml(plan.title || plan.id)}</h3><p class="meta">${escapeHtml(plan.status)} · ${escapeHtml(plan.repository || 'repository in metadata')}</p><button data-preview>Preview and execute</button>`;
    $('[data-preview]', row).onclick = () => openPlan(plan.id); return row;
  }));
  if (!activePlans.length) plansEl.innerHTML = '<p class="empty">No shared plans found.</p>';
}

function openNewGroup() {
  groupForm.reset();
  groupForm.elements.group_name.value = '';
  groupForm.elements.group_purpose.value = '';
  groupForm.dataset.edit = '';
  $('#group-dialog-title').textContent = 'New session group';
  $('#group-dialog-status').textContent = '';
  groupDialog.showModal();
}

groupForm.onsubmit = async (event) => {
  event.preventDefault(); const status = $('#group-dialog-status');
  const submit = $('button[type="submit"]', groupForm); submit.disabled = true;
  status.textContent = 'Creating…';
  try {
    const payload = { name: groupForm.elements.group_name.value };
    const purpose = groupForm.elements.group_purpose.value?.trim();
    if (purpose) payload.purpose = purpose;
    await api('/api/session-groups', { method: 'POST', body: JSON.stringify(payload) });
    status.textContent = 'Created.';
    groupDialog.close();
    await renderOrchestration();
    showNotice(`Group "${payload.name}" created`);
  } catch (error) { status.textContent = error.message; }
  finally { submit.disabled = false; }
};

function renderGroupCard(group) {
  const card = document.createElement('article'); card.className = 'tree-node';
  const statusBadge = group.status === 'active' ? '<span class="badge live">active</span>' : '<span class="badge stopped">completed</span>';
  const memberInfo = `<span class="muted">${group.member_count || 0} session${group.member_count === 1 ? '' : 's'}</span>`;
  card.innerHTML = `<div class="session-title"><h3>${escapeHtml(group.name)}</h3>${statusBadge}${memberInfo}</div><p class="meta">${escapeHtml(group.purpose || 'No purpose set')}</p><div class="tree-node-actions"><button class="compact" data-group-detail>Details</button><button class="compact primary" data-group-open>Open terminals</button></div>`;
  if (group.sessions && group.sessions.length) {
    const children = document.createElement('div'); children.className = 'tree-node-children';
    children.replaceChildren(...group.sessions.map((s) => {
      const child = document.createElement('div'); child.className = 'tree-node';
      const liveClass = s.running ? 'live' : 'stopped';
      child.innerHTML = `<div class="session-title"><span>${escapeHtml(s.tmux_name || 'unknown')}</span><span class="badge ${liveClass}">${escapeHtml(s.profile || '')}</span></div><p class="meta">${escapeHtml(s.tool || '')} · ${escapeHtml(s.attention_state || 'normal')}</p>`;
      return child;
    }));
    card.append(children);
  }
  $('[data-group-detail]', card).onclick = () => openGroupDetail(group.id, group.name);
  $('[data-group-open]', card).onclick = () => openGroupTerminals(group.id, group.name);
  return card;
}

async function openGroupDetail(groupId, groupName) {
  try {
    const group = await api(`/api/session-groups/${encodeURIComponent(groupId)}`);
    $('#group-detail-title').textContent = groupName;
    $('#group-detail-purpose').textContent = group.purpose || 'No purpose set';
    $('#group-detail-meta').textContent = `${group.member_count} session${group.member_count === 1 ? '' : 's'} · status: ${group.status}`;
    const sessionList = $('#group-detail-sessions');
    sessionList.replaceChildren();
    const allSessions = state.sessions.filter((s) => s.running && s.managed);
    const members = group.sessions || [];
    if (members.length) {
      const memberNames = new Set(members.map((m) => m.tmux_name));
      members.forEach((s) => {
        const el = document.createElement('div'); el.className = 'member-row';
        const liveClass = s.running ? 'live' : 'stopped';
        el.innerHTML = `<span class="session-name">${escapeHtml(s.tmux_name)}</span><span class="badge ${liveClass}">${escapeHtml(s.profile || '')}</span><span class="muted">${escapeHtml(s.tool || '')}</span><button class="compact danger" data-remove="${escapeHtml(s.tmux_name)}">Remove</button>`;
        $('[data-remove]', el).onclick = async () => {
          try {
            await api(`/api/session-groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(s.tmux_name)}`, { method: 'DELETE' });
            showNotice(`Removed ${s.tmux_name} from group`);
            openGroupDetail(groupId, groupName);
          } catch (e) { showNotice(e.message, 'error'); }
        };
        sessionList.append(el);
      });
      const addSection = document.createElement('div'); addSection.className = 'group-add-section';
      addSection.innerHTML = '<hr><p class="muted">Add a running session to this group:</p><div class="group-add-controls"><select id="group-add-select"><option value="">Select a session…</option></select><button id="group-add-btn" class="primary compact" disabled>Add</button></div>';
      const addSelect = addSection.querySelector('#group-add-select');
      const addBtn = addSection.querySelector('#group-add-btn');
      const eligible = allSessions.filter((s) => !memberNames.has(s.tmux_name));
      eligible.forEach((s) => {
        const opt = document.createElement('option');
        opt.value = s.tmux_name;
        opt.textContent = `${s.tmux_name} · ${s.tool || 'legacy'} · ${s.profile || 'legacy'}`;
        addSelect.append(opt);
      });
      if (eligible.length) {
        addSelect.onchange = () => { addBtn.disabled = !addSelect.value; };
        addBtn.onclick = async () => {
          const name = addSelect.value; if (!name) return;
          try {
            await api(`/api/session-groups/${encodeURIComponent(groupId)}/members`, { method: 'POST', body: JSON.stringify({ session_name: name }) });
            showNotice(`Added ${name} to group`);
            openGroupDetail(groupId, groupName);
          } catch (e) { showNotice(e.message, 'error'); }
        };
      } else {
        addSection.innerHTML += '<p class="empty">No additional running managed sessions available.</p>';
      }
      sessionList.append(addSection);
    } else {
      sessionList.innerHTML = '<p class="empty">No sessions in this group yet.</p>';
      const addAll = document.createElement('div'); addAll.className = 'group-add-section';
      addAll.innerHTML = '<hr><p class="muted">Add a running session to this group:</p><div class="group-add-controls"><select id="group-add-select"><option value="">Select a session…</option></select><button id="group-add-btn" class="primary compact" disabled>Add</button></div>';
      const addSelect = addAll.querySelector('#group-add-select');
      const addBtn = addAll.querySelector('#group-add-btn');
      allSessions.forEach((s) => {
        const opt = document.createElement('option');
        opt.value = s.tmux_name;
        opt.textContent = `${s.tmux_name} · ${s.tool || 'legacy'} · ${s.profile || 'legacy'}`;
        addSelect.append(opt);
      });
      if (allSessions.length) {
        addSelect.onchange = () => { addBtn.disabled = !addSelect.value; };
        addBtn.onclick = async () => {
          const name = addSelect.value; if (!name) return;
          try {
            await api(`/api/session-groups/${encodeURIComponent(groupId)}/members`, { method: 'POST', body: JSON.stringify({ session_name: name }) });
            showNotice(`Added ${name} to group`);
            openGroupDetail(groupId, groupName);
          } catch (e) { showNotice(e.message, 'error'); }
        };
      } else {
        addAll.innerHTML += '<p class="empty">No running managed sessions available.</p>';
      }
      sessionList.append(addAll);
    }
    $('#group-detail-dialog').showModal();
  } catch (e) { showNotice(e.message, 'error'); }
}

async function openGroupTerminals(groupId, groupName) {
  try {
    const result = await api(`/api/session-groups/${encodeURIComponent(groupId)}/open`, { method: 'POST' });
    const available = result.available || [];
    const unavailable = result.unavailable || [];
    let opened = 0;
    for (const s of available) {
      if (terminalTabs.size >= 4) break;
      openTerminal(s.tmux_name);
      opened++;
    }
    const skipped = available.length - opened;
    const parts = [];
    if (opened) parts.push(`Opened ${opened} terminal${opened === 1 ? '' : 's'} for "${groupName}"`);
    if (skipped) parts.push(`${skipped} session${skipped === 1 ? '' : 's'} not opened due to tab limit (max 4)`);
    const closedNames = unavailable.map((s) => s.tmux_name).join(', ');
    if (closedNames) parts.push(`Not running: ${closedNames}`);
    showNotice(parts.join('. ') || `No available sessions in group "${groupName}"`);
  } catch (e) { showNotice(e.message, 'error'); }
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

async function renderProfiles() {
  const entries = await api('/api/profiles');
  $('#profiles-list').replaceChildren(...entries.map(profileCard));
}

function profileCard(entry) {
  const card = document.createElement('div'); card.className = 'profile-card';
  const header = document.createElement('div'); header.className = 'profile-card-header';
  const h3 = document.createElement('h3'); h3.textContent = entry.display_name || entry.name;
  const badge = document.createElement('span'); badge.className = `badge ${entry.status === 'active' ? 'live' : 'stopped'}`; badge.textContent = entry.status;
  header.append(h3, badge);
  const meta = document.createElement('div'); meta.className = 'meta';
  meta.innerHTML = `<span>${entry.read_write_capability}</span><span>worktree: ${entry.worktree_requirement}</span><span>delegation: ${(entry.delegation_permissions || []).join(', ') || 'none'}</span>${entry.requires_human_approval ? '<span>requires approval</span>' : ''}`;
  const desc = document.createElement('p'); desc.textContent = entry.description || '';
  const actions = document.createElement('div'); actions.className = 'dialog-actions';
  const editBtn = document.createElement('button'); editBtn.textContent = 'Edit instructions'; editBtn.onclick = () => openProfileEditor(entry.name);
  actions.append(editBtn);
  card.append(header, meta, desc, actions);
  return card;
}

async function openProfileEditor(name) {
  const form = $('#profile-editor-form'); form.reset();
  form.elements.profile_name.value = name;
  $('#profile-editor-title').textContent = `Edit: ${name}`;
  $('#profile-editor-status').textContent = 'Loading…';
  $('#profile-editor-dialog').showModal();
  try {
    const data = await api(`/api/profiles/${encodeURIComponent(name)}`);
    form.elements.content.value = data.content || '';
    const metaHtml = [
      `<span>${data.read_write_capability}</span>`,
      `<span>worktree: ${data.worktree_requirement}</span>`,
      `<span>status: ${data.status}</span>`,
      data.requires_human_approval ? '<span>requires approval</span>' : '',
      data.replacement_profile ? `<span>replaces: ${data.replacement_profile}</span>` : '',
    ].filter(Boolean).join(' ');
    $('#profile-editor-meta').innerHTML = metaHtml;
    $('#profile-editor-status').textContent = '';
  } catch (e) { $('#profile-editor-status').textContent = e.message; }
}

$('#profile-editor-form').onsubmit = async (event) => {
  event.preventDefault(); const submit = $('button[type="submit"]', event.target); submit.disabled = true;
  const status = $('#profile-editor-status'); status.textContent = 'Saving…';
  try {
    await api(`/api/profiles/${encodeURIComponent(event.target.elements.profile_name.value)}`, {
      method: 'PUT', body: JSON.stringify({ content: event.target.elements.content.value }),
    });
    status.textContent = 'Saved.';
    setTimeout(() => { $('#profile-editor-dialog').close(); }, 800);
    renderProfiles();
  } catch (e) { status.textContent = e.message; } finally { submit.disabled = false; }
};

$('#refresh-profiles').onclick = renderProfiles;

async function renderSkills() {
  const data = await api('/api/skills');
  const status = $('#skills-status');
  if (data.errors && data.errors.length) {
    status.textContent = `Catalog errors: ${data.errors.join(', ')}`;
    status.className = 'muted';
  } else {
    status.textContent = '';
  }
  $('#skills-list').replaceChildren(...data.entries.map((entry) => skillCard(entry, data.providers)));
}

function skillCard(entry, providers = []) {
  const card = document.createElement('div'); card.className = 'skill-card';
  const header = document.createElement('div'); header.className = 'skill-card-header';
  const h3 = document.createElement('h3'); h3.textContent = entry.name;
  const kind = document.createElement('span'); kind.className = `skill-kind ${entry.kind}`; kind.textContent = entry.kind;
  header.append(h3, kind);
  const desc = document.createElement('p'); desc.textContent = entry.description || 'No description';
  const tools = document.createElement('div'); tools.className = 'skill-tools';
  (entry.synced || []).forEach((s) => {
    tools.append(skillToolDiagnostic(s, providers.find((provider) => provider.tool === s.tool)));
  });
  const sourceBadge = document.createElement('span'); sourceBadge.className = `skill-tool-badge ${entry.source_present ? 'linked' : 'missing'}`;
  sourceBadge.textContent = entry.source_present ? 'source present' : 'source missing';
  tools.append(sourceBadge);

  card.append(header, desc, tools);

  const approvalInfo = document.createElement('div'); approvalInfo.className = 'skill-approval-info';
  if (entry.kind === 'superpower') {
    const allowed = entry.allowed_profiles && entry.allowed_profiles.length ? entry.allowed_profiles.join(', ') : 'all profiles';
    approvalInfo.textContent = entry.requires_approval ? `Superpower · approval required · profiles: ${allowed}` : `Superpower · profiles: ${allowed}`;
  } else {
    approvalInfo.textContent = 'Standard skill · no approval gate';
  }
  card.append(approvalInfo);

  const assignedList = document.createElement('div'); assignedList.className = 'skill-assigned-list';
  if (entry.assigned_to && entry.assigned_to.length) {
    const label = document.createElement('span'); label.className = 'skill-assigned-label'; label.textContent = 'Assigned to:';
    assignedList.append(label);
    entry.assigned_to.forEach((a) => {
      const tag = document.createElement('span'); tag.className = 'skill-assigned-tag';
      tag.textContent = a.profile;
      tag.title = `Assigned at ${a.assigned_at} by ${a.assigned_by}`;
      assignedList.append(tag);
    });
  }
  card.append(assignedList);

  const actions = document.createElement('div'); actions.className = 'dialog-actions';
  const assignBtn = document.createElement('button'); assignBtn.textContent = 'Assign to profile'; assignBtn.className = 'compact';
  assignBtn.onclick = () => openAssignSkillDialog(entry.name);
  actions.append(assignBtn);
  if (entry.assigned_to && entry.assigned_to.length) {
    entry.assigned_to.forEach((a) => {
      const unassignBtn = document.createElement('button'); unassignBtn.textContent = `Remove from ${a.profile}`; unassignBtn.className = 'compact danger';
      unassignBtn.onclick = () => unassignSkill(entry.name, a.profile);
      actions.append(unassignBtn);
    });
  }
  card.append(actions);

  return card;
}

async function openAssignSkillDialog(skillName) {
  const form = $('#assign-skill-form'); form.reset();
  form.elements.skill_name.value = skillName;
  $('#assign-skill-title').textContent = `Assign: ${skillName}`;
  const profiles = await api('/api/profiles');
  form.elements.profile.replaceChildren(...profiles.map((p) => new Option(p.display_name || p.name, p.name)));
  $('#assign-skill-status').textContent = '';
  $('#assign-skill-dialog').showModal();
}

$('#assign-skill-form').onsubmit = async (event) => {
  event.preventDefault(); const submit = event.target.querySelector('button[type="submit"]'); submit.disabled = true;
  const status = $('#assign-skill-status'); status.textContent = 'Assigning…';
  try {
    await api('/api/skills/assign', {
      method: 'POST',
      body: JSON.stringify({ profile: event.target.elements.profile.value, skill_name: event.target.elements.skill_name.value }),
    });
    status.textContent = 'Assigned.';
    setTimeout(() => { $('#assign-skill-dialog').close(); }, 800);
    renderSkills();
  } catch (e) { status.textContent = e.message; } finally { submit.disabled = false; }
};

async function unassignSkill(skillName, profile) {
  const status = $('#skills-status'); status.textContent = `Removing ${skillName} from ${profile}…`;
  try {
    await api('/api/skills/unassign', {
      method: 'POST',
      body: JSON.stringify({ profile, skill_name: skillName }),
    });
    status.textContent = `Removed ${skillName} from ${profile}.`;
    renderSkills();
  } catch (e) { status.textContent = e.message; }
}

async function runSkillsSync() {
  const status = $('#skills-status'); status.textContent = 'Syncing…';
  try {
    const result = await api('/api/skills/sync', { method: 'POST', body: '{}' });
    await renderSkills();
    status.textContent = skillActionMessage('sync', result);
  } catch (e) { status.textContent = `Sync failed: ${e.message}`; }
}

async function runSkillsDoctor() {
  const status = $('#skills-status'); status.textContent = 'Running doctor…';
  try {
    const result = await api('/api/skills/doctor', { method: 'POST', body: '{}' });
    await renderSkills();
    status.textContent = skillActionMessage('doctor', result);
  } catch (e) { status.textContent = `Doctor failed: ${e.message}`; }
}

$('#skills-sync').onclick = runSkillsSync;
$('#skills-doctor').onclick = runSkillsDoctor;

async function renderProjects() {
  const projects = await api('/api/projects');
  $('#projects-list').replaceChildren(...projects.map(projectCard));
}

function projectCard(project) {
  const card = document.createElement('div'); card.className = 'profile-card';
  const header = document.createElement('div'); header.className = 'profile-card-header';
  const h3 = document.createElement('h3'); h3.textContent = project.name;
  const badge = document.createElement('span'); badge.className = `badge ${project.status === 'active' ? 'live' : 'stopped'}`; badge.textContent = project.status;
  header.append(h3, badge);
  const meta = document.createElement('div'); meta.className = 'meta';
  meta.innerHTML = `<span>sessions: ${project.session_count || 0}</span>${project.repository ? `<span>${escapeHtml(project.repository)}</span>` : ''}`;
  const desc = document.createElement('p'); desc.textContent = project.description || '';
  const actions = document.createElement('div'); actions.className = 'dialog-actions';
  const viewBtn = document.createElement('button'); viewBtn.textContent = 'View sessions'; viewBtn.onclick = () => openProjectDetail(project.id, project.name);
  actions.append(viewBtn);
  card.append(header, meta, desc, actions);
  return card;
}

async function openProjectDetail(id, name) {
  try {
    const project = await api(`/api/projects/${encodeURIComponent(id)}`);
    $('#project-detail-title').textContent = name;
    $('#project-detail-repo').textContent = project.repository ? `Repository: ${project.repository}` : 'No repository';
    $('#project-detail-status').textContent = `Status: ${project.status} · ${(project.sessions || []).length} session(s)`;
    const sessions = project.sessions || [];
    const list = $('#project-detail-sessions'); list.innerHTML = '';
    sessions.forEach((s) => {
      const el = document.createElement('article'); el.className = 'tree-node';
      el.innerHTML = `<div class="session-title"><span>${escapeHtml(s.tmux_name || 'unknown')}</span><span class="badge ${s.status === 'detached' ? 'live' : 'stopped'}">${escapeHtml(s.profile || '')}</span></div><p class="meta">${escapeHtml(s.tool || '')} · ${escapeHtml(s.attention_state || 'normal')}${s.initial_task ? ` · ${escapeHtml(s.initial_task)}` : ''}</p>`;
      const unassignBtn = document.createElement('button'); unassignBtn.textContent = 'Unassign'; unassignBtn.className = 'danger';
      unassignBtn.onclick = async () => {
        try {
          await api(`/api/projects/${encodeURIComponent(id)}/unassign`, { method: 'POST', body: JSON.stringify({ session_name: s.tmux_name }) });
          openProjectDetail(id, name);
        } catch (e) { showNotice(e.message, 'error'); }
      };
      el.append(unassignBtn);
      list.append(el);
    });
    if (!sessions.length) list.innerHTML = '<p class="empty">No sessions assigned to this project.</p>';
    const assignSelect = $('#project-assign-select');
    const availSessions = (state.sessions || []).filter((s) => s.running && !sessions.find((ps) => ps.tmux_name === s.tmux_name));
    assignSelect.replaceChildren(...availSessions.map((s) => new Option(s.tmux_name, s.tmux_name)));
    assignSelect.prepend(new Option('Select session…', ''));
    assignSelect.dataset.projectId = id;
    $('#project-detail-dialog').showModal();
  } catch (e) { showNotice(e.message, 'error'); }
}

$('#new-project-form').onsubmit = async (event) => {
  event.preventDefault(); const submit = $('button[type="submit"]', event.target); submit.disabled = true;
  const status = $('#new-project-status'); status.textContent = 'Creating…';
  try {
    await api('/api/projects', { method: 'POST', body: JSON.stringify({ name: event.target.elements.name.value, repository: event.target.elements.repository.value || null, description: event.target.elements.description.value || null }) });
    status.textContent = 'Created.'; $('#new-project-dialog').close();
    renderProjects();
  } catch (e) { status.textContent = e.message; } finally { submit.disabled = false; }
};
$('#new-project-btn').onclick = () => { $('#new-project-form').reset(); $('#new-project-status').textContent = ''; $('#new-project-dialog').showModal(); };
$('#project-assign-btn').onclick = async () => {
  const select = $('#project-assign-select');
  const sessionName = select.value;
  const projectId = select.dataset.projectId;
  if (!sessionName || !projectId) return;
  try {
    await api(`/api/projects/${encodeURIComponent(projectId)}/assign`, { method: 'POST', body: JSON.stringify({ session_name: sessionName }) });
    openProjectDetail(projectId, $('#project-detail-title').textContent);
  } catch (e) { showNotice(e.message, 'error'); }
};

function updateAgentModeField() {
  const tool = newForm.elements.tool.value;
  const profile = newForm.elements.profile.value;
  const field = $('#agent-mode-field');
  const select = newForm.elements.agent_mode;
  const p = state.identity.profiles.find(x => x.name === profile);
  const readOnly = p ? p.read_write_capability === 'read_only' : false;
  const codexLike = tool === 'codex' || tool === 'codex-pro';
  const visible = codexLike || tool === 'opencode';
  field.hidden = !visible; select.disabled = !visible;
  if (!visible) return;
  const choices = codexLike
    ? (readOnly ? [['plan', 'Plan']] : [['auto', 'Auto'], ['plan', 'Plan']])
    : (readOnly ? [['plan', 'Plan']] : [['plan', 'Plan'], ['build', 'Build']]);
  const previous = select.value;
  select.replaceChildren(...choices.map(([value, label]) => new Option(label, value)));
  const fallback = codexLike && !readOnly ? 'auto' : 'plan';
  const preserve = field.dataset.tool === tool && choices.some(([value]) => value === previous);
  select.value = preserve ? previous : fallback; field.dataset.tool = tool;
  $('#agent-mode-label').textContent = `${codexLike ? (tool === 'codex-pro' ? 'Codex Pro' : 'Codex') : 'OpenCode'} mode`;
  $('#agent-mode-help').textContent = codexLike
    ? (select.value === 'plan' ? 'Read-only planning; no file or system changes.' : 'Workspace-write with approvals on request; not unrestricted host access.')
    : (select.value === 'plan' ? 'Planning is the default.' : 'Build is explicitly write-capable.');
  select.onchange = updateAgentModeField;
}

function updateNewToolFields() {
  const tool = newForm.elements.tool.value;
  const catalog = state.identity.tool_status.find((item) => item.name === tool);
  const codexLike = tool === 'codex' || tool === 'codex-pro';
  if (tool !== 'opencode') { loadModelsReq++; currentModels = []; $('#model-options').replaceChildren(); newForm.elements.model.value = ''; newForm.elements.auth_context.replaceChildren(); }
  updateContextSelect(newForm.elements.tool, newForm.elements.auth_context);
  updateAgentModeField();
  $('#provider-field').hidden = tool !== 'opencode'; newForm.elements.provider.disabled = tool !== 'opencode';
  $('#model-field').hidden = tool !== 'opencode'; newForm.elements.model.disabled = tool !== 'opencode';
  $('#codex-model-field').hidden = !codexLike; newForm.elements.codex_model.disabled = !codexLike;
  $('#codex-effort-field').hidden = !codexLike; newForm.elements.codex_effort.disabled = !codexLike;
  $('#codex-plan-effort-field').hidden = !codexLike; newForm.elements.codex_plan_effort.disabled = !codexLike;
  if (tool === 'opencode') loadModels();
  formStatus.textContent = catalog?.status === 'ready' ? '' : `${catalog?.status || 'unknown'}: ${catalog?.reason || 'configuration required'}`;
}

function renderProviderContexts() {
  const provider = newForm.elements.provider.value;
  if (!provider) { newForm.elements.auth_context.replaceChildren(); return; }
  const contexts = state.identity.auth_contexts.filter((item) => item.tool === 'opencode' && item.provider === provider && item.enabled !== false);
  newForm.elements.auth_context.replaceChildren(...contexts.map((item) => new Option(`${item.name} · ${item.status}`, item.name, false, item.default)));
}

function isValidGeneration(req, provider) {
  return req === loadModelsReq && newForm.elements.tool.value === 'opencode' && newForm.elements.provider.value === provider;
}

async function loadModels() {
  const provider = newForm.elements.provider.value || 'opencode-go';
  const req = ++loadModelsReq;
  renderProviderContexts();
  newForm.elements.model.value = '';
  currentModels = [];
  renderModelOptions([]);
  $('#model-options').replaceChildren();
  $('#model-status').textContent = 'Loading current catalogue…';
  try {
    const catalogue = await api(`/api/models?provider=${encodeURIComponent(provider)}`);
    if (!isValidGeneration(req, provider)) return;
    currentModels = catalogue.models; renderModelOptions(currentModels);
    const preferred = catalogue.models.find((model) => model.selectable)?.model || '';
    const current = catalogue.models.find((model) => model.model === newForm.elements.model.value && model.selectable);
    if (!current) newForm.elements.model.value = preferred;
    renderProviderContexts();
    $('#model-status').textContent = `${catalogue.models.length} models, lowest output-token cost first${catalogue.stale ? ' · cached catalogue' : ''}. Default: ${preferred || 'none available'}.`;
  } catch (error) { if (isValidGeneration(req, provider)) { newForm.elements.model.value = ''; currentModels = []; renderModelOptions([]); $('#model-options').replaceChildren(); newForm.elements.auth_context.replaceChildren(); $('#model-status').textContent = error.message; } }
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
  const provider = newForm.elements.provider.value;
  const req = ++loadModelsReq;
  const payload = {
    provider,
    uncached_input_tokens: Number($('#estimate-uncached').value || 0),
    cached_input_tokens: Number($('#estimate-cached').value || 0),
    output_tokens: Number($('#estimate-output').value || 0),
    reasoning_tokens: Number($('#estimate-reasoning').value || 0),
  };
  try {
    const result = await api('/api/models/estimate', { method: 'POST', body: JSON.stringify(payload) });
    if (!isValidGeneration(req, provider)) return;
    currentModels = result.models; renderModelOptions(currentModels);
    const cheapest = currentModels.find((model) => model.cheapest);
    if (cheapest) newForm.elements.model.value = cheapest.model;
    $('#model-status').textContent = cheapest ? `Selected cheapest for this token mix: ${cheapest.model} · estimated $${cheapest.estimated_usd.toFixed(6)}. Actual session usage varies.` : 'No priced selectable model can be estimated for this token mix.';
  } catch (error) { if (isValidGeneration(req, provider)) $('#model-status').textContent = error.message; }
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
  (async () => {
    try {
      const projects = await api('/api/projects');
      const projSelect = newForm.elements.project_id;
      projSelect.replaceChildren(...projects.map((p) => new Option(`${p.name}${p.repository ? ' · ' + p.repository : ''}`, p.id)));
      projSelect.prepend(new Option('None', ''));
    } catch {}
  })();
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
  const maxDock = window.innerHeight - 56;
  const move = (moveEvent) => {
    const height = Math.max(240, Math.min(maxDock, startHeight + startY - moveEvent.clientY));
    terminalDock.style.height = `${Math.round(height)}px`;
    updateDockLayout();
  };
  const stop = () => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop);
    try { localStorage.setItem('agent-console-dock-height', String(Math.round(terminalDock.getBoundingClientRect().height))); } catch {}
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop, { once: true });
});
(function restoreDock() {
  const saved = localStorage.getItem('agent-console-dock-height');
  if (saved) {
    const h = parseInt(saved, 10);
    if (h > 0 && h <= window.innerHeight - 56) { terminalDock.style.height = `${h}px`; updateDockLayout(); }
  }
})();

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
  if (data.tool === 'codex' || data.tool === 'codex-pro') {
    data.model = data.codex_model || null;
    data.reasoning_effort = data.codex_effort || null;
    data.plan_reasoning_effort = data.codex_plan_effort || null;
  }
  delete data.codex_model; delete data.codex_effort; delete data.codex_plan_effort;
  for (const key of ['name', 'task', 'agent_mode', 'provider', 'model', 'reasoning_effort', 'plan_reasoning_effort', 'project_id']) if (!data[key]) data[key] = null;
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
