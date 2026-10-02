import { setupLaunches } from '/static/launch-workbench.js';
import { setupOverview } from '/static/work-overview.js';
import { setupResults } from '/static/results-workbench.js';
import { setupSkills } from '/static/skill-workbench.js';
import { initTheme } from '/static/theme.js?v=8';
const $ = (s, root = document) => root.querySelector(s);
const form = $('#create-form');
const state = { sessions: [], me: null, selected: null, work: null, selectedNodeId: null, loading: false, frames: new Map() };
const names = { normal: 'Working', needs_input: 'Needs input', blocked: 'Blocked', ready_for_review: 'Ready for review' };
const pendingSessionActions = new Set();
function renderPendingActions() {
  for (const [selector, action] of [['#interrupt-session','interrupt'],['#stop-session','kill'],['#stop-terminal-session','kill'],['#attention-form button[type=submit]','attention']]) {
    const button=$(selector),pending=pendingSessionActions.has(`${state.selected?.id}:${action}`);
    button.disabled=pending;button.setAttribute('aria-busy',String(pending));
  }
}
function el(tag, text, cls) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; }
function message(text, kind = 'general') { $('#notice').textContent = text; $('#notice').hidden = !text; $('#notice').dataset.kind = kind; }
async function api(path, payload, method = 'POST') {
  const response = await fetch(path, payload === undefined ? { cache: 'no-store' } : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
const skillsView = setupSkills({ api, el, message, profiles: () => state.me?.profiles || [] });
let skillsLoaded = false;
const resultsView = setupResults({api,el,message,sessions:()=>state.sessions,editStep:step=>openCreate(state.sessions.find(s=>s.id===step.root_id)||state.selected,step),openSession:async name=>{await refresh();location.hash=`#session/${encodeURIComponent(name)}`;}});
let resultsSession = null, resultsReady = Promise.resolve();
$('#show-results').onclick = () => { location.hash = `#results/${encodeURIComponent(state.selected.tmux_name)}`; };
function status(s) { return s.attention_state !== 'normal' && s.attention_state ? names[s.attention_state] || s.attention_state : s.running ? 'Working' : 'Stopped'; }
function sessionLink(s) { return `#session/${encodeURIComponent(s.tmux_name)}`; }
const overview=setupOverview({state,el,openCreate});
const launches=setupLaunches({api,el,state,openCreate,getRequest:createRequest,refresh,message,openSession:name=>{location.hash=`#session/${encodeURIComponent(name)}`;route();openTerminal();}});
function renderWork(){overview.renderWork();}
function renderSession() {
  const s = state.selected;
  if (!s) return;
  $('#session-title').textContent = s.tmux_name;
  $('#session-title').title = s.tmux_name;
  $('#session-state').textContent = status(s);
  $('#session-meta').textContent = `${s.tool || 'Terminal'} · ${s.profile || 'Session'} · ${s.repository || ''}`;
  $('#session-meta').title = $('#session-meta').textContent;
  $('#session-brief').textContent = s.initial_task || 'No stored task brief.';
  overview.renderTree(s,state.selectedNodeId);
  const node=overview.nodeFor(s);
  if(node){
    const historical=node.native_id!==s.id?node.attempts.find(a=>a.session_id===s.id):null;
    const displayed=historical?{...node,mechanical:s.running?'running':'stopped',attention:s.attention_state||'normal',result_state:historical.result?.outcome==='pass'?'completed':historical.result?.outcome==='fail'?'failed':'unknown',readiness:{}}:node;
    $('#session-statuses').replaceChildren(overview.statuses(displayed));
    if(historical)$('#session-statuses').append(el('p',`Historical attempt ${historical.generation}. The tree links to the current attempt.`,'small muted'));
  }
  $('#open-terminal').disabled = !s.running;
  $('#continue-session').hidden=!!s.running||!s.managed||s.execution_kind==='integration-plan';
  $('#interrupt-session').hidden = !s.actions?.includes('interrupt');
  $('#stop-session').hidden = !s.actions?.includes('kill');
  $('#stop-terminal-session').hidden = !s.actions?.includes('kill');
  renderPendingActions();
  // Refresh session state without replacing a status note the user is editing.
  const attention = $('#attention-form');
  if (attention.dataset.session !== s.id) {
    $('#session-detail').open = false;
    attention.dataset.session = s.id; attention.elements.state.value = s.attention_state || 'normal'; attention.elements.note.value = s.attention_note || '';
    $('#session-output').hidden = true; $('#session-skills').hidden = true; $('#session-history').hidden=true;$('#session-configuration').hidden=true;
  }
}
function route() {
  const hash = location.hash || '#work', showingStep=hash.startsWith('#step/'), showingResults = hash.startsWith('#results/')||showingStep;
  let sessionName = null;
  try {
    state.selectedNodeId=showingStep?decodeURIComponent(hash.slice(6)):null;
    const step=showingStep?state.work?.nodes.find(n=>n.id===state.selectedNodeId):null;
    sessionName=showingStep?(state.work?.nodes.find(n=>n.id===step?.root_id)?.native_name||'missing-step'):(hash.startsWith('#session/')||showingResults)?decodeURIComponent(hash.slice(9)):null;
  } catch { message('Invalid session link. Return to Work.'); }
  const previous = state.selected?.tmux_name;
  const switchingFromDrawer = $('#sessions-dialog').open && previous !== sessionName;
  if(switchingFromDrawer)$('#sessions-dialog').close();
  state.selected = state.sessions.find(s => s.tmux_name === sessionName) || null;
  $('#work-view').hidden = Boolean(sessionName) || ['#settings', '#skills'].includes(hash);
  $('#skills-view').hidden = hash !== '#skills';
  if (hash === '#skills' && !skillsLoaded && state.me) { skillsLoaded = true; skillsView.load().catch(error => { skillsLoaded = false; message(error.message); }); }
  $('#settings-view').hidden = hash !== '#settings';
  $('#session-view').hidden = !state.selected || showingResults;
  $('#results-view').hidden = !state.selected || !showingResults;
  if (showingResults && state.selected) {
    $('#results-back').href = sessionLink(state.selected);
    $('#results-session').textContent = state.selected.tmux_name;
    if (resultsSession !== state.selected.id) {
      resultsSession = state.selected.id;
      resultsReady = resultsView.load(state.selected).catch(error => message(error.message));
      $('#results-view h1').focus({preventScroll:true});
      window.scrollTo(0, 0);
    }
    if(showingStep)resultsReady.then(()=>{const next=$('#workflow-next-steps');if(next)next.open=true;});
  } else resultsSession = null;
  document.querySelectorAll('.mobile-nav a').forEach(a => a.setAttribute('aria-current', a.hash === (['#settings','#skills'].includes(hash) ? '#settings' : '#work') ? 'page' : 'false'));
  if (sessionName && !state.selected) message('This session is no longer available. Return to Work.', 'route');
  else if ($('#notice').dataset.kind === 'route') message('');
  renderWork(); renderSession();
  for (const [name, frame] of state.frames) {
    if (!state.sessions.some(session => session.tmux_name === name && session.running)) {
      frame.remove(); state.frames.delete(name);
    } else frame.hidden = name !== state.selected?.tmux_name;
  }
  if (!state.selected || !state.frames.has(state.selected.tmux_name)) $('#terminal-panel').hidden = true;
  if (!showingResults && state.selected?.running && previous !== state.selected.tmux_name && (!matchMedia('(max-width:760px)').matches || switchingFromDrawer)) openTerminal();
  syncTerminalVisibility();
}
async function refresh() {
  if (state.loading) return state.loading;
  $('#refresh').disabled=true;$('#work-list').setAttribute('aria-busy','true');
  state.loading = (async () => {
    try { const work=await api('/api/workbench'); if(!Array.isArray(work.nodes)||!Array.isArray(work.sessions))throw new Error('Work summary unavailable');state.work=work;state.sessions=work.sessions;route(); }
    catch (error) { message(`Could not refresh sessions: ${error.message}`); }
    finally { state.loading = null;$('#refresh').disabled=false;$('#work-list').setAttribute('aria-busy','false'); }
  })();
  return state.loading;
}
function openTerminal() {
  const s = state.selected; if (!s?.running) return;
  let frame = state.frames.get(s.tmux_name);
  if (!frame) {
    // Bound browser PTYs; drafts survive eviction in the terminal's sessionStorage.
    if (state.frames.size >= 3) { const [name, old] = state.frames.entries().next().value; old.remove(); state.frames.delete(name); }
    frame = el('iframe'); frame.title = `Terminal: ${s.tmux_name}`; frame.src = `/terminal?session=${encodeURIComponent(s.tmux_name)}&embed=1&mode=type&lifecycle=managed`;
    frame.addEventListener('load', syncTerminalVisibility);
    state.frames.set(s.tmux_name, frame); $('#terminal-frames').append(frame);
  }
  state.frames.forEach(f => { f.hidden = f !== frame; }); $('#terminal-panel').hidden = false;
  $('#terminal-status').textContent = `${s.tmux_name} · ${frame.dataset.status || 'Connecting…'}`;
  $('#terminal-status').title = s.tmux_name;
  syncTerminalVisibility();
}
function syncTerminalVisibility() {
  const visible = !$('#session-view').hidden && !$('#terminal-panel').hidden;
  state.frames.forEach(frame => frame.contentWindow?.postMessage({type:'agent-console:terminal-visibility', visible:visible && !frame.hidden}, location.origin));
  const desktopSession = visible && !matchMedia('(max-width:760px)').matches;
  const enteringSession = desktopSession && !document.body.classList.contains('session-terminal');
  document.body.classList.toggle('session-terminal', desktopSession);
  if (enteringSession) window.scrollTo(0, 0);
  document.documentElement.style.setProperty('--workbench-top', `${Math.round($('.top').getBoundingClientRect().bottom)}px`);
  document.body.classList.toggle('terminal-open', visible && ($('#terminal-panel').classList.contains('expanded') || matchMedia('(max-width:760px)').matches));
  const viewport = window.visualViewport;
  document.documentElement.style.setProperty('--terminal-height', `${Math.round(viewport?.height || innerHeight)}px`);
  document.documentElement.style.setProperty('--terminal-top', `${Math.round(viewport?.offsetTop || 0)}px`);
}
window.visualViewport?.addEventListener('resize', syncTerminalVisibility);
window.visualViewport?.addEventListener('scroll', syncTerminalVisibility);
window.addEventListener('resize', syncTerminalVisibility);

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
function configureSessionFlow() {
  const parent=form.elements.parent.value;
  const scheduled=!!parent&&$('#schedule-step').checked;
  $('#scheduled-options').hidden=!scheduled;
  form.elements.reason.required=scheduled;form.elements.expected_output.required=scheduled;
  form.elements.name.disabled=scheduled;form.elements.name.closest('label').hidden=scheduled;
  $('button[type=submit]',form).textContent=scheduled?'Review scheduled step':parent?'Add session':'Create session';
  $('#create-start-help').textContent=scheduled?'This scheduled task needs one review before it can run automatically.':'Open Input · draft in the terminal to review and send the task when you are ready.';
}
$('#schedule-step').onchange=configureSessionFlow;
function openCreate(parent = null, step = null) {
  if ($('#sessions-dialog').open) $('#sessions-dialog').close();
  if (!state.me) { message('Tool information is still loading. Try again shortly.'); return; }
  form.reset(); launches.reset(parent); form.elements.parent.value = parent?.id || '';
  form.dataset.step = step ? JSON.stringify(step) : '';
  $('#next-step-options').hidden=!parent;
  const nativeParent=parent&&state.sessions.some(s=>s.id===parent.id);
  $('#schedule-step').checked=!!step||!!parent&&!nativeParent;$('#schedule-step').disabled=!!step||!!parent&&!nativeParent;
  form.elements.reason.value=step?.reason||'A separate session for this specific task.';form.elements.expected_output.value=step?.expected_output||'Complete the stated task and report the result, checks and selected artifacts.';
  configureSessionFlow();
  $('#create-title').textContent = parent ? 'Add session' : 'New session';
  $('#create-help').textContent = parent ? `Under ${parent.tmux_name}. Choose a task and tool. This opens a separate terminal without a review chain.` : 'One session can investigate, implement and check a simple task.';
  const available = state.me.tool_status.filter(x => !['disabled','error','setup-required'].includes(x.status));
  options(form.elements.tool, available.map(x => [x.name, x.name]), parent?.tool || 'codex-pro');
  options(form.elements.profile, state.me.profiles.filter(x => x.status !== 'deprecated').map(x => [x.name, x.display_name || x.name]), 'coder');
  form.elements.repository.value = parent?.repository || state.workspace || '';
  $('#create-error').textContent = available.length ? '' : 'No tool is ready. Check Tools & accounts in Settings.';
  configureTool(); configureRole();
  if(step){for(const [key,value] of Object.entries(step.config))if(form.elements[key]&&key!=='worktree')form.elements[key].value=value;configureTool();configureRole();if(step.config.auth_context)form.elements.auth_context.value=step.config.auth_context;form.elements.task.value=step.task;form.elements.worktree.checked=step.config.worktree;form.elements.readiness.value=step.dependencies.find(d=>d.source_id===step.owner_id)?.readiness||'alongside';}
  $('#create-dialog').showModal(); previewSkills();
}
function createRequest(){
  const fields = form.elements;
  const saved=form.dataset.launchConfig?JSON.parse(form.dataset.launchConfig):{};
  const data = { ...saved, tool: fields.tool.value, profile: fields.profile.value, task: fields.task.value, worktree: fields.worktree.checked && !fields.worktree.disabled };
  for (const key of ['name','repository','auth_context','model','reasoning_effort']) {if (fields[key].value.trim() && !fields[key].disabled) data[key] = fields[key].value.trim();else delete data[key];}
  const profile = state.me.profiles.find(x => x.name === data.profile);
  if(saved.tool&&saved.tool!==data.tool){delete data.provider;delete data.agent_mode;delete data.plan_reasoning_effort;}
  if(saved.profile&&saved.profile!==data.profile)delete data.agent_mode;
  if (data.tool === 'opencode'&&!data.agent_mode) data.agent_mode = profile?.read_write_capability === 'read_only' ? 'plan' : 'build';
  return data;
}
form.onsubmit = async event => {
  event.preventDefault(); const submit = $('button[type=submit]', form); if (submit.disabled) return; submit.disabled = true; $('#create-error').textContent = '';
  const fields=form.elements,parent=fields.parent.value,data=createRequest();
  try {
    if(!parent&&form.dataset.review){await launches.reviewLaunch(data,{start:true});return;}
    if(parent&&!$('#schedule-step').checked){
      const owner=state.sessions.find(s=>s.id===parent);if(!owner)throw new Error('Parent session is unavailable. Refresh and try again.');
      const session=await api(`/api/sessions/${encodeURIComponent(owner.tmux_name)}/children`,data);
      $('#create-dialog').close();if(state.loading)await state.loading;await refresh();location.hash=sessionLink(session);route();openTerminal();
    }else if(parent){
      const existing=form.dataset.step?JSON.parse(form.dataset.step):null;
      const owner=existing?.owner_id||parent;
      const config={...data};delete config.task;delete config.name;
      if(existing){for(const key of ['action','target','project_id','agent_mode'])if(existing.config[key]!=null)config[key]=existing.config[key];if(existing.config.profile!==config.profile)delete config.action;if(existing.config.profile!==config.profile||existing.config.tool!==config.tool)delete config.agent_mode;}
      const dependencies=(existing?.dependencies||[]).filter(d=>d.source_id!==owner);dependencies.push({source_id:owner,readiness:fields.readiness.value});
      const proposal={task:data.task,reason:fields.reason.value,expected_output:fields.expected_output.value,config,dependencies};
      const proposed=existing?await api(`/api/workflow/steps/${existing.id}/edit`,{...proposal,expected_version:existing.version}):await api(`/api/sessions/${encodeURIComponent(parent)}/workflow/proposals`,{...proposal,request_key:Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('')});
      $('#create-dialog').close();resultsSession=null;await refresh();location.hash=`#step/${encodeURIComponent(proposed.id)}`;route();await resultsReady;if($('#workflow-next-steps'))$('#workflow-next-steps').open=true;
      message('Next step proposed. Preview its launch and accept it when the scope is right.');
    }else{
      const session=await api('/api/sessions',data);$('#create-dialog').close();if(state.loading)await state.loading;await refresh();location.hash=sessionLink(session);route();openTerminal();
    }
  } catch (error) { $('#create-error').textContent = error.message; }
  finally { submit.disabled = false; }
};
async function sessionAction(action, payload, method='POST') {
  const s=state.selected;if(!s)return;
  const key=`${s.id}:${action}`;if(pendingSessionActions.has(key))return;
  pendingSessionActions.add(key);renderPendingActions();
  try {
    await api(`/api/sessions/${encodeURIComponent(s.tmux_name)}/${action}`,payload,method);
    if(action==='kill') {
      state.frames.get(s.tmux_name)?.remove();state.frames.delete(s.tmux_name);
      if(state.selected?.id===s.id)$('#terminal-panel').hidden=true;
      syncTerminalVisibility();
    }
    // A refresh started before the mutation can still contain the old live state.
    if(state.loading)await state.loading;
    await refresh();
    if(action==='kill')message(`Stopped ${s.tmux_name}. Files and recent output are kept.`);
    if(action==='attention')message(`Status updated for ${s.tmux_name}.`);
  } catch(error) { message(`${s.tmux_name}: ${error.message}`); }
  finally { pendingSessionActions.delete(key);renderPendingActions(); }
}
$('#interrupt-session').onclick = () => { if (confirm(`Interrupt ${state.selected.tmux_name}?`)) sessionAction('interrupt', {}); };
const stopSelectedSession = () => { if (confirm(`Stop ${state.selected.tmux_name}? This ends its running process. Files and recent output will be kept.`)) sessionAction('kill', { confirmed: true }); };
$('#stop-session').onclick = stopSelectedSession;
$('#stop-terminal-session').onclick = stopSelectedSession;
$('#attention-form').onsubmit = event => { event.preventDefault();const f=event.target;sessionAction('attention',{state:f.elements.state.value,note:f.elements.note.value},'PATCH'); };
$('#show-output').onclick = async () => { const s = state.selected; try { const output = await api(`/api/sessions/${encodeURIComponent(s.tmux_name)}/review?lines=500`); if (s.id !== state.selected?.id) return; $('#output-text').textContent = output.content || 'No captured output.'; $('#session-output').hidden = false; } catch (e) { message(e.message); } };
$('#show-skills').onclick = async () => {
  const s = state.selected;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(s.tmux_name)}/skills`);
    if (s.id !== state.selected?.id) return;
    const panel = $('#session-skills');
    panel.replaceChildren(el('h3', 'Delivered skills'), el('p', data.notice, 'small muted'));
    if (data.latest) {
      panel.append(el('p', `${data.latest.at} · ${data.latest.tool} · ${data.latest.profile}`));
      for (const skill of data.latest.skills) panel.append(el('p', `${skill.name} · ${skill.revision} · ${skill.selection}`));
      panel.append(el('p', data.latest.coverage, 'small muted'));
    }
    panel.hidden = false;
  } catch (error) { message(error.message); }
};
$('#show-history').onclick=async()=>{
  const session=state.selected,button=$('#show-history');button.disabled=true;
  try{
    const data=await api(`/api/workbench/sessions/${session.id}/history`);if(session.id!==state.selected?.id)return;
    const panel=$('#session-history');panel.replaceChildren(el('h3','History'),el('p',data.notice,'small muted'));
    function append(events){for(const event of events)panel.append(el('p',`${event.at} · ${event.action}${event.outcome?' · '+event.outcome:''}`,'small'));}
    append(data.events);if(data.events.length===50){const more=el('button','Older workflow events');let before=data.before;more.onclick=async()=>{more.disabled=true;try{const page=await api(`/api/workbench/sessions/${session.id}/history?before=${before}`);append(page.events);before=page.before;if(page.events.length<50)more.remove();}catch(error){message(error.message);}finally{more.disabled=false;}};panel.append(more);}
    panel.append(el('h3','Session audit'));append(data.audit);
    if(data.audit.length===50){const more=el('button','Older session events');let before=data.audit_before;more.onclick=async()=>{more.disabled=true;try{const page=await api(`/api/workbench/sessions/${session.id}/history?audit_before=${before}`);append(page.audit);before=page.audit_before;if(page.audit.length<50)more.remove();}catch(error){message(error.message);}finally{more.disabled=false;}};panel.append(more);}
    panel.hidden=false;
  }catch(error){message(error.message);}finally{button.disabled=false;}
};
let previewSequence = 0;
async function previewSkills() {
  const sequence = ++previewSequence, target = $('#create-skills');
  target.replaceChildren(el('p', 'Checking effective skills…'));
  try {
    const data = await api('/api/skill-registry/preview', { profile: form.elements.profile.value, tool: form.elements.tool.value, repository: form.elements.repository.value || null });
    if (sequence !== previewSequence) return;
    target.replaceChildren(el('p', data.validation.valid ? 'Skill configuration is ready.' : 'Resolve the skill issues below before launch.'));
    for (const skill of data.policies) target.append(el('p', `${skill.name} · ${skill.effective_policy} · ${skill.revision || 'Unknown revision'}${skill.reasons.length ? ' — ' + skill.reasons.join('; ') : ''}`));
    for (const issue of data.validation.issues) target.append(el('p', issue, 'danger'));
    if (!data.policies.length) target.append(el('p', 'No Console-selected skills.'));
    target.append(el('p', data.notice, 'small muted'));
  } catch (error) { if (sequence === previewSequence) target.replaceChildren(el('p', error.message, 'danger')); }
}
$('#preview-skills').onclick = previewSkills;
form.elements.repository.addEventListener('change', previewSkills);
form.elements.profile.addEventListener('change', previewSkills);
form.elements.tool.addEventListener('change', previewSkills);
$('#new-session').onclick = () => openCreate(); $('#cancel-create').onclick = () => $('#create-dialog').close();
form.elements.profile.onchange = configureRole; form.elements.tool.onchange = configureTool;
const treePanel=$('#tree-panel');
try { treePanel.open=localStorage.getItem('workbench-tree-open') !== 'false'; } catch {}
function syncTreePanel(){
  $('.workspace').classList.toggle('tree-collapsed',!treePanel.open);
  try{localStorage.setItem('workbench-tree-open',String(treePanel.open));}catch{}
}
treePanel.addEventListener('toggle',syncTreePanel);syncTreePanel();
$('#terminal-sessions').onclick=()=>{
  $('#tree-drawer').append($('#session-tree'));
  $('#sessions-dialog').showModal();
  $('#session-tree [aria-current="page"]')?.focus();
};
$('#sessions-dialog').addEventListener('click',event=>{
  const link=event.target.closest('a');
  if(link?.hash===location.hash)$('#sessions-dialog').close();
});
$('#close-sessions').onclick=()=>$('#sessions-dialog').close();
$('#sessions-dialog').addEventListener('close',()=>$('#tree-home').append($('#session-tree')));
$('#open-terminal').onclick = openTerminal;
$('#close-terminal').onclick = () => { $('#terminal-panel').hidden = true; $('#terminal-panel').classList.remove('expanded'); $('#expand-terminal').textContent = 'Full screen'; syncTerminalVisibility(); };
$('#expand-terminal').onclick = () => { const expanded = $('#terminal-panel').classList.toggle('expanded'); $('#expand-terminal').textContent = expanded ? 'Restore' : 'Full screen'; syncTerminalVisibility(); };
$('#refresh').onclick = refresh;
window.addEventListener('hashchange', route); window.addEventListener('focus', refresh);
initTheme($('#theme'));
try {
  const [me, instance] = await Promise.all([api('/api/me'), api('/api/interface')]); state.me = me; state.workspace = instance.workspace;
  if(me.session_limits)$('#session-capacity').textContent=`Up to ${me.session_limits.managed} running sessions. Stopped sessions do not use a slot.`;
  $('#instance').textContent = instance.label;
  for (const id of ['#current-version','#settings-current']) if (instance.current_url) { $(id).href = instance.current_url; $(id).hidden = false; }
  $('#tools').replaceChildren(...me.tool_status.map(x => el('p', `${x.name} · ${x.status}${x.reason ? ` — ${x.reason}` : ''}`, 'tool')));
} catch (e) { message(`Could not load settings: ${e.message}`); }
await refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 10000);

window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.data?.type !== 'agent-console:terminal-status' || typeof event.data.status !== 'string') return;
  for (const [name, frame] of state.frames) if (event.source === frame.contentWindow) {
    frame.dataset.status = event.data.status;
    if (name === state.selected?.tmux_name) $('#terminal-status').textContent = `${name} · ${event.data.status}`;
  }
});
