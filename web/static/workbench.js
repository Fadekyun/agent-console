import { initTheme } from '/static/theme.js?v=8';
const $ = (s, root = document) => root.querySelector(s);
const form = $('#create-form');
const state = { sessions: [], me: null, selected: null, loading: false, frames: new Map() };
const names = { normal: 'Working', needs_input: 'Needs input', blocked: 'Blocked', ready_for_review: 'Ready for review' };
function el(tag, text, cls) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; }
function message(text) { $('#notice').textContent = text; $('#notice').hidden = !text; }
async function api(path, payload, method = 'POST') {
  const response = await fetch(path, payload === undefined ? { cache: 'no-store' } : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function status(s) { return s.attention_state !== 'normal' && s.attention_state ? names[s.attention_state] || s.attention_state : s.running ? 'Working' : 'Stopped'; }
function rootOf(s) { const visited = new Set(); while (s.parent_session_id && !visited.has(s.id)) { visited.add(s.id); const p = state.sessions.find(x => x.id === s.parent_session_id); if (!p) break; s = p; } return s; }
function family(s) { const root = rootOf(s); return state.sessions.filter(x => rootOf(x).id === root.id); }
function sessionLink(s) { return `#session/${encodeURIComponent(s.tmux_name)}`; }
function renderWork() {
  const q = $('#search').value.toLowerCase().trim(), filter = $('#state-filter').value;
  const roots = state.sessions.filter(s => rootOf(s).id === s.id).filter(s => {
    const members = family(s);
    return members.some(x => (!q || `${x.tmux_name} ${x.initial_task || ''} ${x.repository || ''}`.toLowerCase().includes(q)) && (filter === 'all' || (filter === 'active' ? x.running : ['needs_input','blocked','ready_for_review'].includes(x.attention_state))));
  });
  $('#work-list').replaceChildren(...roots.map(s => {
    const members = family(s), attention = members.find(x => ['blocked','needs_input','ready_for_review'].includes(x.attention_state));
    const card = el('article', null, 'card');
    card.append(el('span', attention ? status(attention) : members.some(x => x.running) ? 'Working' : 'Stopped', `badge${attention ? ' attention' : ''}`), el('h2', s.tmux_name), el('p', s.initial_task || 'Open this session to continue your work.', 'brief muted'), el('p', `${members.length} session${members.length === 1 ? '' : 's'} · ${s.repository || 'No repository'}`, 'meta'));
    const link = el('a', 'Open work', 'button'); link.href = sessionLink(attention || s); card.append(link); return card;
  }));
  if (!roots.length) $('#work-list').append(el('p', state.sessions.length ? 'No work matches these filters.' : 'Your staging workspace is ready. Start a session to begin.', 'empty'));
}
function renderSession() {
  const s = state.selected;
  if (!s) return;
  $('#session-title').textContent = s.tmux_name;
  $('#session-state').textContent = status(s);
  $('#session-meta').textContent = `${s.tool || 'Terminal'} · ${s.profile || 'Session'} · ${s.repository || ''}`;
  $('#session-brief').textContent = s.initial_task || 'No stored task brief.';
  const members = family(s), root = rootOf(s), nodes = [], visited = new Set();
  function visit(node, depth) {
    if (visited.has(node.id)) return; visited.add(node.id);
    const item = el('div', null, `node${node.id === s.id ? ' active' : ''}`); item.style.setProperty('--depth', depth);
    const link = el('a', node.tmux_name); link.href = sessionLink(node); if (node.id === s.id) link.setAttribute('aria-current','page');
    item.append(link, el('small', `${node.profile || 'Session'} · ${status(node)}`));
    if (node.managed && node.execution_kind !== 'integration-plan') { const add = el('button', '+ Add session'); add.onclick = () => openCreate(node); item.append(add); }
    nodes.push(item); members.filter(x => x.parent_session_id === node.id).forEach(child => visit(child, depth + 1));
  }
  visit(root, 0); $('#session-tree').replaceChildren(...nodes);
  $('#open-terminal').disabled = !s.running;
  $('#interrupt-session').hidden = !s.actions?.includes('interrupt');
  $('#stop-session').hidden = !s.actions?.includes('kill');
  // Refresh session state without replacing a status note the user is editing.
  const attention = $('#attention-form');
  if (attention.dataset.session !== s.id) {
    attention.dataset.session = s.id; attention.elements.state.value = s.attention_state || 'normal'; attention.elements.note.value = s.attention_note || '';
    $('#session-output').hidden = true; $('#session-skills').hidden = true;
  }
}
function route() {
  const hash = location.hash || '#work';
  let sessionName = null;
  try { sessionName = hash.startsWith('#session/') ? decodeURIComponent(hash.slice(9)) : null; } catch { message('Invalid session link. Return to Work.'); }
  state.selected = state.sessions.find(s => s.tmux_name === sessionName) || null;
  $('#work-view').hidden = Boolean(sessionName) || hash === '#settings';
  $('#settings-view').hidden = hash !== '#settings';
  $('#session-view').hidden = !state.selected;
  document.querySelectorAll('.mobile-nav a').forEach(a => a.setAttribute('aria-current', a.hash === (hash === '#settings' ? '#settings' : '#work') ? 'page' : 'false'));
  if (sessionName && !state.selected) message('This session is no longer available. Return to Work.');
  renderWork(); renderSession();
  for (const [name, frame] of state.frames) frame.hidden = name !== state.selected?.tmux_name;
  if (!state.selected || !state.frames.has(state.selected.tmux_name)) $('#terminal-panel').hidden = true;
}
async function refresh() {
  if (state.loading) return state.loading;
  state.loading = (async () => {
    try { state.sessions = await api('/api/sessions'); route(); }
    catch (error) { message(`Could not refresh sessions: ${error.message}`); }
    finally { state.loading = null; }
  })();
  return state.loading;
}
function openTerminal() {
  const s = state.selected; if (!s?.running) return;
  let frame = state.frames.get(s.tmux_name);
  if (!frame) {
    // Bound browser PTYs; drafts survive eviction in the terminal's sessionStorage.
    if (state.frames.size >= 3) { const [name, old] = state.frames.entries().next().value; old.remove(); state.frames.delete(name); }
    frame = el('iframe'); frame.title = `Terminal: ${s.tmux_name}`; frame.src = `/terminal?session=${encodeURIComponent(s.tmux_name)}&embed=1`;
    state.frames.set(s.tmux_name, frame); $('#terminal-frames').append(frame);
  }
  state.frames.forEach(f => { f.hidden = f !== frame; }); $('#terminal-panel').hidden = false;
}
function options(select, entries, preferred) {
  select.replaceChildren(...entries.map(([value, label]) => { const option = el('option', label); option.value = value; return option; }));
  if (entries.some(([value]) => value === preferred)) select.value = preferred;
}
function configureRole() {
  const p = state.me.profiles.find(x => x.name === form.elements.profile.value);
  $('#role-description').textContent = p?.description || '';
  const writes = p?.read_write_capability === 'write';
  form.elements.worktree.checked = writes && ['coder','bugfix'].includes(p.name);
  form.elements.worktree.disabled = !writes;
}
function configureTool() {
  const tool = form.elements.tool.value;
  options(form.elements.auth_context, state.me.auth_contexts.filter(x => x.tool === tool && !['disabled','error','setup-required'].includes(x.status)).map(x => [x.name, `${x.name} · ${x.provider}`]));
  form.elements.reasoning_effort.disabled = !['codex','codex-pro'].includes(tool);
}
function openCreate(parent = null) {
  if (!state.me) { message('Tool information is still loading. Try again shortly.'); return; }
  form.reset(); form.elements.parent.value = parent?.tmux_name || '';
  $('#create-title').textContent = parent ? 'Add session' : 'New session';
  $('#create-help').textContent = parent ? `Under ${parent.tmux_name}. Choose a role and a bounded task. This leaves the parent’s permissions unchanged.` : 'One session can investigate, implement and check a simple task.';
  const available = state.me.tool_status.filter(x => !['disabled','error','setup-required'].includes(x.status));
  options(form.elements.tool, available.map(x => [x.name, x.name]), parent?.tool || 'codex-pro');
  options(form.elements.profile, state.me.profiles.filter(x => x.status !== 'deprecated').map(x => [x.name, x.display_name || x.name]), 'coder');
  form.elements.repository.value = parent?.repository || state.workspace || '';
  $('#create-error').textContent = available.length ? '' : 'No tool is ready. Check Tools & accounts in Settings.';
  configureTool(); configureRole(); $('#create-dialog').showModal();
}
form.onsubmit = async event => {
  event.preventDefault(); const submit = $('button[type=submit]', form); if (submit.disabled) return; submit.disabled = true; $('#create-error').textContent = '';
  const fields = form.elements, parent = fields.parent.value;
  const data = { tool: fields.tool.value, profile: fields.profile.value, task: fields.task.value, worktree: fields.worktree.checked && !fields.worktree.disabled };
  for (const key of ['name','repository','auth_context','model','reasoning_effort']) if (fields[key].value.trim() && !fields[key].disabled) data[key] = fields[key].value.trim();
  const profile = state.me.profiles.find(x => x.name === data.profile);
  if (data.tool === 'opencode') data.agent_mode = profile?.read_write_capability === 'read_only' ? 'plan' : 'build';
  try {
    const session = await api(parent ? `/api/sessions/${encodeURIComponent(parent)}/children` : '/api/sessions', data);
    $('#create-dialog').close(); if (state.loading) await state.loading; await refresh(); location.hash = sessionLink(session); route(); openTerminal();
  } catch (error) { $('#create-error').textContent = error.message; }
  finally { submit.disabled = false; }
};
async function sessionAction(action, payload) { const s = state.selected; if (!s) return; try { await api(`/api/sessions/${encodeURIComponent(s.tmux_name)}/${action}`, payload); await refresh(); } catch (error) { message(error.message); } }
$('#interrupt-session').onclick = () => { if (confirm(`Interrupt ${state.selected.tmux_name}?`)) sessionAction('interrupt', {}); };
$('#stop-session').onclick = () => { if (confirm(`Stop ${state.selected.tmux_name}? Its files and transcript will be kept.`)) sessionAction('kill', { confirmed: true }); };
$('#attention-form').onsubmit = async event => { event.preventDefault(); const f = event.target; try { await api(`/api/sessions/${encodeURIComponent(state.selected.tmux_name)}/attention`, { state: f.elements.state.value, note: f.elements.note.value }, 'PATCH'); await refresh(); message('Session status updated.'); } catch (e) { message(e.message); } };
$('#show-output').onclick = async () => { const s = state.selected; try { const output = await api(`/api/sessions/${encodeURIComponent(s.tmux_name)}/review?lines=500`); if (s.id !== state.selected?.id) return; $('#output-text').textContent = output.content || 'No captured output.'; $('#session-output').hidden = false; } catch (e) { message(e.message); } };
$('#show-skills').onclick = async () => { const s = state.selected; try { const skills = await api('/api/skills/effective', { profile: s.profile }); if (s.id !== state.selected?.id) return; const panel = $('#session-skills'); panel.replaceChildren(el('h3', 'Current role skill assignments'), el('p', 'These apply to newly created sessions. Existing sessions retain their launch-time skill snapshot.', 'small muted'), ...skills.effective.map(x => el('p', `${x.name} — ${x.description}`)), ...skills.issues.map(x => el('p', x, 'danger'))); if (!skills.effective.length) panel.append(el('p','No role-specific assignments. Shared and tool-provided skills may also be available.')); panel.hidden = false; } catch (e) { message(e.message); } };
$('#new-session').onclick = () => openCreate(); $('#cancel-create').onclick = () => $('#create-dialog').close();
form.elements.profile.onchange = configureRole; form.elements.tool.onchange = configureTool;
$('#open-terminal').onclick = openTerminal;
$('#close-terminal').onclick = () => { $('#terminal-panel').hidden = true; $('#terminal-panel').classList.remove('expanded'); $('#expand-terminal').textContent = 'Full screen'; };
$('#expand-terminal').onclick = () => { const expanded = $('#terminal-panel').classList.toggle('expanded'); $('#expand-terminal').textContent = expanded ? 'Restore' : 'Full screen'; };
$('#refresh').onclick = refresh; $('#search').oninput = renderWork; $('#state-filter').onchange = renderWork;
window.addEventListener('hashchange', route); window.addEventListener('focus', refresh);
initTheme($('#theme'));
try {
  const [me, instance] = await Promise.all([api('/api/me'), api('/api/interface')]); state.me = me; state.workspace = instance.workspace;
  $('#instance').textContent = instance.label;
  for (const id of ['#current-version','#settings-current']) if (instance.current_url) { $(id).href = instance.current_url; $(id).hidden = false; }
  $('#tools').replaceChildren(...me.tool_status.map(x => el('p', `${x.name} · ${x.status}${x.reason ? ` — ${x.reason}` : ''}`, 'tool')));
} catch (e) { message(`Could not load settings: ${e.message}`); }
await refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 10000);
