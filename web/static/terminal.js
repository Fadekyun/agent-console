import { Terminal } from '/vendor/xterm.mjs';
import { FitAddon } from '/vendor/addon-fit.mjs';
import { initTheme, xtermTheme } from '/static/theme.js?v=10';

// Clipboard controls keep their accessible text if icon enhancement is unavailable.
import('/static/icons.js').catch(() => {});

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let name = new URLSearchParams(location.search).get('session');
const initialName = name;
let sessionId = new URLSearchParams(location.search).get('session_id');
let identityRequest = null, connectionGeneration = 0, draftInitialized = false;
let draftKey = null;
if (!name) location.href = '/';
$('#session-name').textContent = name;
document.title = `Agent Terminal - ${name}`;
const terminalFrame = $('.terminal-frame');
if (terminalFrame) terminalFrame.setAttribute('aria-label', `Terminal session ${name}`);

const isEmbedded = location.search.includes('embed=1');
const managedVisibility = isEmbedded && new URLSearchParams(location.search).get('lifecycle') === 'managed';
let viewVisible = !managedVisibility;
const coarsePointer = matchMedia('(pointer: coarse)').matches;
if (isEmbedded) document.body.classList.add('terminal-embedded');
const terminal = new Terminal({ cursorBlink: true, scrollback: 10000, fontSize: coarsePointer ? 13 : 14, macOptionClickForcesSelection: true, theme: xtermTheme() });
const fit = new FitAddon();
terminal.loadAddon(fit); terminal.open($('#terminal'));

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const connection = $('#connection');
const reconnect = $('#reconnect');
const composer = $('#composer');
const newOutput = $('#new-output');
let socket;
let mode = new URLSearchParams(location.search).get('mode') || 'type';
let focusOnConnect = !coarsePointer && !isEmbedded;
let resizeFrame;
let alternateScreen = false;
let touchStartY = null;
let following = true;
let serverHistory = false;
let historyMode = false;
let nativeScrolled = false;
let pendingScroll = 0, scrollTimer = null;
let hasUnread = false;
let briefLoaded = false;
let draftRevision = 0, clipboardContext = 0, pastePending = false;
let reconnectTimer = null;
let reconnectAttempt = 0;
const MAX_RECONNECT_ATTEMPTS = 30;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 30000;
let autoReconnectEnabled = true;
function saveDraft() { draftRevision++; $('#toggle-composer').textContent = composer.value ? 'Input · draft' : 'Input'; try { if (draftKey) sessionStorage.setItem(draftKey, composer.value); } catch { /* continue without persistence */ } }
function showComposer(open, focus = false) {
  if (!open) clipboardContext++;
  $('#input-drawer').hidden = !open;
  $('#toggle-composer').setAttribute('aria-expanded', String(open));
  try { if (draftKey) sessionStorage.setItem(`${draftKey}:open`, String(open)); } catch {}
  autoSizeComposer();
  if (focus) { if (open) composer.focus(); else if (mode === 'type') terminal.focus(); }
}
$('#resume-typing').onclick = () => setMode('type');
$('#toggle-composer').onclick = () => showComposer($('#input-drawer').hidden, true);
new MutationObserver(() => { reconnect.hidden = reconnect.disabled; }).observe(reconnect, {attributes:true, attributeFilter:['disabled']});
const more = $('#terminal-more');
const moreMenu = $('.terminal-menu');
function fitMoreMenu() {
  $('#terminal-more > summary').setAttribute('aria-expanded', String(more.open));
  if (!more.open) return;
  const viewport = window.visualViewport;
  const bottom = (viewport?.offsetTop || 0) + (viewport?.height || innerHeight);
  const bounds = $('.terminal-controls').getBoundingClientRect();
  const available = Math.max(0, bottom - bounds.bottom);
  // Leave a margin where possible, without clipping a 44px control in a short iframe.
  const gap = Math.min(7, Math.max(0, available - 46));
  moreMenu.style.top = `${bounds.bottom}px`;
  moreMenu.style.maxHeight = `${Math.max(0, available - gap)}px`;
  moreMenu.style.padding = `${Math.min(12, Math.max(0, (available - gap - 46) / 2))}px`;
}
function closeMore(restoreFocus = false) {
  more.open = false;
  if (restoreFocus) $('#terminal-more > summary').focus();
}
more.addEventListener('toggle', fitMoreMenu);
// Capture before xterm: dismissing an open menu must not send Escape to the agent.
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && more.open && !document.querySelector('dialog[open]')) {
    event.preventDefault(); event.stopPropagation(); closeMore(true);
  }
}, true);
more.addEventListener('focusout', event => {
  if (event.relatedTarget && !more.contains(event.relatedTarget)) closeMore();
});
document.addEventListener('pointerdown', event => {
  if (!more.contains(event.target)) closeMore();
});
window.addEventListener('blur', () => closeMore());
new ResizeObserver(() => { fitMoreMenu(); autoSizeComposer(); }).observe($('.terminal-controls'));
window.addEventListener('pagehide', saveDraft);
window.addEventListener('focus', () => {
  if (draftInitialized && viewVisible) refreshIdentity().catch(error => setStatus(error.message));
});

async function refreshIdentity() {
  if (identityRequest) return identityRequest;
  identityRequest = (async () => {
    const response = await fetch('/api/sessions?state=all', { cache: 'no-store' });
    if (!response.ok) throw new Error('Session identity unavailable; retry when the connection recovers');
    const sessions = await response.json();
    if (!Array.isArray(sessions)) throw new Error('Session identity unavailable; retry when the connection recovers');
    const current = sessions.find(session => sessionId ? session.id === sessionId : session.tmux_name === name);
    if (!current?.id || !current.tmux_name) throw new Error('Original session is no longer available');
    sessionId = current.id;
    name = current.tmux_name;
    $('#session-name').textContent = name;
    document.title = `Agent Terminal - ${name}`;
    terminalFrame?.setAttribute('aria-label', `Terminal session ${name}`);
    const url = new URL(location.href); url.searchParams.set('session', name); url.searchParams.set('session_id', sessionId);
    history.replaceState(history.state, '', url);
    return current;
  })();
  try { return await identityRequest; } finally { identityRequest = null; }
}
function sessionPath(action) {
  return `/api/sessions/${encodeURIComponent(name)}/${action}${action.includes('?') ? '&' : '?'}session_id=${encodeURIComponent(sessionId)}`;
}
function initializeDraft() {
  if (draftInitialized) return;
  draftInitialized = true;
  draftKey = `agent-console:composer:id:${sessionId}`;
  try {
    const draft = sessionStorage.getItem(draftKey);
    if (draft !== null && !composer.value) { composer.value = draft; briefLoaded = true; }
    const originalKey = sessionStorage.getItem(`${draftKey}:legacy-source`) || `agent-console:composer:${initialName}`;
    const legacyKey = sessionStorage.getItem(originalKey) ? originalKey : `agent-console:composer:${name}`;
    const legacy = sessionStorage.getItem(legacyKey);
    const restore = $('#restore-legacy-draft');
    if (!draft && legacy) {
      sessionStorage.setItem(`${draftKey}:legacy-source`, legacyKey);
      restore.hidden = false;
      restore.title = `Restore an older draft saved as ${legacyKey.slice('agent-console:composer:'.length)}; review before sending`;
      restore.onclick = () => {
        insertComposer(legacy); briefLoaded = true; restore.hidden = true;
        try { sessionStorage.removeItem(legacyKey); sessionStorage.removeItem(`${legacyKey}:open`); sessionStorage.removeItem(`${draftKey}:legacy-source`); } catch { /* storage may become unavailable */ }
        setStatus('Older draft restored; review before sending');
      };
    }
    showComposer(sessionStorage.getItem(`${draftKey}:open`) === 'true' || !$('#input-drawer').hidden);
  } catch { /* drafts still work when storage is unavailable */ }
  saveDraft();
}


function setStatus(message) {
  connection.textContent = message;
  if (isEmbedded) window.parent.postMessage({ type: 'agent-console:terminal-status', status: message }, location.origin);
}

window.addEventListener('message', (event) => {
  if (!isEmbedded) return;
  if (event.origin !== window.location.origin) return;
  if (event.source !== window.parent) return;
  if (event.data?.type === 'agent-console:refresh-identity' && event.data.session_id === sessionId) {
    refreshIdentity().catch(error => setStatus(error.message)); return;
  }
  if (managedVisibility && event.data?.type === 'agent-console:terminal-visibility' && typeof event.data.visible === 'boolean') {
    if (viewVisible === event.data.visible) return;
    viewVisible = event.data.visible;
    if (!viewVisible) cancelDragSelection();
    if (viewVisible) { autoReconnectEnabled = true; cancelReconnect(); connect(); }
    else {
      connectionGeneration++; clipboardContext++;
      saveDraft(); if (historyMode || nativeScrolled) leaveHistory(); autoReconnectEnabled = false; cancelReconnect();
      if (socket) { socket.onclose = null; socket.onerror = null; socket.onmessage = null; socket.close(); socket = null; }
      setStatus('Terminal closed · session still running');
    }
    return;
  }
  if (event.data?.type !== 'agent-console:focus-terminal') return;
  if (mode === 'type') terminal.focus();
});

function leaveHistory() {
  clearTimeout(scrollTimer); scrollTimer = null; pendingScroll = 0;
  if ((historyMode || nativeScrolled) && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'scroll', lines: 0 }));
  historyMode = false; nativeScrolled = false; following = true; newOutput.hidden = true;
}
function scrollHistory(lines) {
  if (!lines || socket?.readyState !== WebSocket.OPEN) return;
  // Coalesce wheel/touch bursts instead of queuing a tmux subprocess per event.
  pendingScroll = Math.max(-200, Math.min(200, pendingScroll + lines));
  if (!scrollTimer) scrollTimer = setTimeout(flushScroll, 32);
  historyMode = true; following = false; newOutput.hidden = false;
  newOutput.textContent = 'Latest output';
}
function flushScroll() {
  scrollTimer = null;
  if (!pendingScroll || socket?.readyState !== WebSocket.OPEN) { pendingScroll = 0; return; }
  const lines = Math.max(-50, Math.min(50, pendingScroll));
  pendingScroll -= lines;
  socket.send(JSON.stringify({type:'scroll', lines}));
  if (pendingScroll) scrollTimer = setTimeout(flushScroll, 32);
}
function send(value, mouse = false) {
  if (socket?.readyState !== WebSocket.OPEN) throw new Error('Terminal is disconnected');
  if (!mouse && (historyMode || nativeScrolled)) leaveHistory();
  socket.send(encoder.encode(value));
}

function atBottom() {
  const buffer = terminal.buffer.active;
  return buffer.viewportY >= buffer.baseY;
}

function resize() {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    try {
      if (!viewVisible || !terminalFrame.clientWidth || !terminalFrame.clientHeight) return;
      fit.fit();
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }));
      if (following) terminal.scrollToBottom();
    } catch { /* the container can be between viewport sizes */ }
  });
}

function syncVisualViewport() {
  const height = window.visualViewport?.height || window.innerHeight;
  document.documentElement.style.setProperty('--visual-height', `${Math.round(height)}px`);
  autoSizeComposer(); fitMoreMenu();
}

function scheduleReconnect() {
  if (!autoReconnectEnabled) return;
  if (reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
    setStatus('Max reconnection attempts reached. Click Reconnect to retry.');
    reconnect.disabled = false;
    return;
  }
  reconnectAttempt++;
  const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt - 1), RECONNECT_MAX_MS);
  const jitter = delay * (0.5 + Math.random() * 0.5);
  const seconds = Math.round(jitter / 100) / 10;
  setStatus(`Reconnecting in ${seconds}s (attempt ${reconnectAttempt}/${MAX_RECONNECT_ATTEMPTS})…`);
  reconnect.disabled = true;
  reconnectTimer = setTimeout(() => { connect(); }, jitter);
}

function cancelReconnect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  reconnectAttempt = 0;
}

async function connect() {
  if (!viewVisible) return;
  const generation = ++connectionGeneration;
  reconnect.disabled = true;
  try { await refreshIdentity(); }
  catch (error) {
    if (generation === connectionGeneration && viewVisible) { setStatus(error.message); reconnect.disabled = false; }
    return;
  }
  if (generation !== connectionGeneration || !viewVisible) return;
  const firstConnection = !draftInitialized;
  initializeDraft();
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (socket) { socket.onopen = null; socket.onclose = null; socket.onerror = null; socket.onmessage = null; socket.close(); }
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const connectedName = name;
  socket = new WebSocket(`${protocol}//${location.host}/ws/sessions/${encodeURIComponent(name)}?session_id=${encodeURIComponent(sessionId)}`);
  socket.binaryType = 'arraybuffer'; setStatus('Connecting…'); reconnect.disabled = true;
  socket.onopen = () => { cancelReconnect(); socket.send(JSON.stringify({type:'scroll',lines:0})); nativeScrolled = false; historyMode = false; setStatus('Connected'); resize(); if (following) terminal.scrollToBottom(); if (focusOnConnect && mode === 'type' && !more.open && !document.querySelector('dialog[open]') && (document.activeElement === document.body || $('#terminal').contains(document.activeElement))) terminal.focus(); focusOnConnect = false; };
  socket.onmessage = (event) => {
    const output = typeof event.data === 'string' ? event.data : decoder.decode(event.data, { stream: true });
    terminal.write(output, () => {
      if (historyMode) {
        newOutput.hidden = false; newOutput.textContent = 'Latest output';
      } else if (following) {
        terminal.scrollToBottom();
      } else {
        hasUnread = true;
        newOutput.hidden = false;
        newOutput.textContent = 'New output \u00b7 Scroll to bottom';
        newOutput.classList.add('has-unread');
      }
    });
  };
  socket.onclose = (event) => {
    if (event.code === 4404 || event.code === 4409) {
      autoReconnectEnabled = false; reconnect.disabled = true;
      refreshIdentity().then(() => {
        if (generation !== connectionGeneration || !viewVisible) return;
        if (name !== connectedName) { autoReconnectEnabled = true; connect(); }
        else { setStatus('Original session is not running'); reconnect.disabled = false; }
      }).catch(error => { if (generation === connectionGeneration && viewVisible) { setStatus(error.message); reconnect.disabled = false; } });
    } else if (event.code === 4001) {
      autoReconnectEnabled = false; setStatus(event.reason || 'Session unavailable; reconnect to retry');
      reconnect.disabled = false;
    } else if (event.code === 4000) {
      autoReconnectEnabled = false; setStatus('Detached by user; tmux is still running');
      reconnect.disabled = false;
    } else if ([4400, 4403, 4429].includes(event.code)) {
      autoReconnectEnabled = false; setStatus(event.reason || 'Connection unavailable'); reconnect.disabled = false;
    } else {
      setStatus(`Detached (${event.code}${event.reason ? `: ${event.reason}` : ''})`);
      reconnect.disabled = true;
      scheduleReconnect();
    }
  };
  socket.onerror = () => { setStatus('Connection error'); }; // close schedules exactly one retry
  if (firstConnection) loadBrief(true);
}

function setMode(selected, focus = true) {
  cancelDragSelection();
  mode = ['scroll', 'type', 'select'].includes(selected) ? selected : 'scroll';
  document.body.dataset.terminalMode = mode;
  terminal.options.disableStdin = mode !== 'type';
  $('#resume-typing').hidden = mode === 'type';
  $$('[data-mode]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
  if (mode === 'type') { if (historyMode) leaveHistory(); if (focus) terminal.focus(); }
  else terminal.blur();
}

function insertComposer(text, focus = true) {
  const start = composer.selectionStart ?? composer.value.length;
  const end = composer.selectionEnd ?? start;
  composer.setRangeText(text, start, end, 'end');
  saveDraft(); if (focus) showComposer(true, true); else autoSizeComposer();
}

function autoSizeComposer() {
  const drawer = $('#input-drawer');
  composer.style.height = 'auto';
  if (drawer.hidden) { resize(); return; }
  drawer.style.maxHeight = 'none';
  const style = getComputedStyle(composer);
  const number = value => Number.parseFloat(value) || 0;
  const lineHeight = number(style.lineHeight) || number(style.fontSize) * 1.4;
  const border = number(style.borderTopWidth) + number(style.borderBottomWidth);
  const padding = number(style.paddingTop) + number(style.paddingBottom);
  const viewport = window.visualViewport;
  const bottom = (viewport?.offsetTop || 0) + (viewport?.height || innerHeight);
  const available = Math.max(0, bottom - $('.terminal-controls').getBoundingClientRect().bottom);
  const chrome = drawer.getBoundingClientRect().height - composer.getBoundingClientRect().height;
  const outputSpace = Math.min(64, Math.max(24, available * .2));
  const maximum = Math.max(number(style.minHeight), available - chrome - outputSpace);
  composer.style.height = `${Math.min(composer.scrollHeight + border, lineHeight * 4 + padding + border, maximum)}px`;
  // An exceptionally short iframe can scroll the drawer without hiding Send permanently.
  drawer.style.maxHeight = `${available}px`;
  resize();
}

function submit(addEnter) {
  try {
    send(composer.value + (addEnter ? '\r' : ''));
    composer.value = ''; saveDraft(); autoSizeComposer();
    if (mode === 'type') terminal.focus();
  } catch (error) { setStatus(error.message); showComposer(true); }
}

async function copyText(value, stillCurrent) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return true; } catch { /* fallback */ }
  }
  if (!stillCurrent()) return null;
  const fallback = $('#terminal-clipboard-fallback');
  const originalParent = fallback.parentNode, originalNext = fallback.nextSibling;
  const active = document.activeElement, selection = window.getSelection();
  const ranges = Array.from({ length: selection?.rangeCount || 0 }, (_, index) => selection.getRangeAt(index).cloneRange());
  // A modal makes elements outside it inert, including the legacy copy textarea.
  const modal = $('dialog[open]');
  if (modal) modal.append(fallback);
  fallback.value = value; fallback.classList.remove('visually-hidden'); fallback.focus({ preventScroll: true }); fallback.select();
  try { return document.execCommand?.('copy') || false; }
  catch { return false; }
  finally {
    fallback.classList.add('visually-hidden'); fallback.value = '';
    originalParent.insertBefore(fallback, originalNext);
    active?.focus({ preventScroll: true });
    if (selection) { selection.removeAllRanges(); ranges.forEach(range => selection.addRange(range)); }
  }
}

function showCopySheet(value, title = 'Copy terminal selection') {
  $('#copy-title').textContent = title;
  $('#copy-sheet-text').setAttribute('aria-label', title === 'Copy session name' ? 'Copy session name' : 'Copy terminal text');
  $('#copy-sheet-text').value = value;
  $('#copy-sheet').showModal();
  $('#copy-sheet-text').focus(); $('#copy-sheet-text').select();
}

// Permission prompts may outlive their originating dialog or terminal view.
function clipboardIsCurrent() {
  const context = clipboardContext, modal = $('dialog[open]');
  return () => context === clipboardContext && viewVisible && modal === $('dialog[open]');
}

async function copyAndReport(value, message, title) {
  const stillCurrent = clipboardIsCurrent();
  const copied = await copyText(value, stillCurrent);
  if (!stillCurrent()) return;
  if (copied) setStatus(message);
  else showCopySheet(value, title);
}

async function copySelection() {
  const selected = terminal.getSelection();
  if (!selected) { await refreshTextView(); return; }
  await copyAndReport(selected, 'Selection copied');
}

async function loadBrief(silent = false) {
  if (briefLoaded && silent) return;
  try {
    await refreshIdentity();
    const response = await fetch(sessionPath('brief'), { cache: 'no-store' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || response.statusText);
    if (body.brief && (!composer.value || !silent)) {
      if (!composer.value || !silent) insertComposer(body.brief, !silent);
      briefLoaded = true;
      if (!silent) setStatus('Brief loaded into composer; review and send when ready');
    } else if (!silent) setStatus('This session has no stored brief');
  } catch (error) { if (!silent) setStatus(error.message); }
}

let textViewRequest = 0;
async function refreshTextView(direction = null) {
  const request = ++textViewRequest, dialog = $('#text-dialog');
  if (!dialog.open) dialog.showModal();
  $('#text-content').textContent = '';
  $('#text-scope').textContent = 'Loading terminal text…';
  const controls = ['#text-refresh', '#text-page-up', '#text-page-down', '#copy-visible', '#copy-dom-selection'];
  controls.forEach(selector => { $(selector).disabled = true; });
  try {
    if (direction) send(direction === 'up' ? '\x1b[5~' : '\x1b[6~');
    if (direction) await new Promise((resolve) => setTimeout(resolve, 180));
    await refreshIdentity();
    const response = await fetch(sessionPath('review?lines=1000'), { cache: 'no-store' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || response.statusText);
    if (request !== textViewRequest || !dialog.open) return;
    alternateScreen = body.alternate_screen;
    $('#text-content').textContent = body.content || '(no captured output)';
    $('#text-scope').textContent = body.capture_scope === 'visible-screen'
      ? 'Current alternate-screen TUI view only. Page, then refresh to inspect more.'
      : `${body.line_count} captured lines · ${body.capture_scope}${body.truncated ? ' · truncated' : ''}`;
  } catch (error) {
    if (request === textViewRequest && dialog.open) $('#text-scope').textContent = error.message;
  } finally {
    if (request === textViewRequest) controls.forEach(selector => { $(selector).disabled = false; });
  }
}

async function copyDomSelection() {
  const selected = window.getSelection()?.toString() || '';
  if (!selected) { setStatus('Select text in Text View first'); return; }
  await copyAndReport(selected, 'Selection copied');
}

async function pasteFromDevice() {
  if (pastePending) return;
  const revision = draftRevision, sameContext = clipboardIsCurrent();
  const start = composer.selectionStart, end = composer.selectionEnd;
  const stillCurrent = () => {
    if (!sameContext()) return false;
    if (revision !== draftRevision || start !== composer.selectionStart || end !== composer.selectionEnd) {
      setStatus('Draft changed while reading clipboard; paste again when ready'); return false;
    }
    return true;
  };
  pastePending = true;
  ['#paste-clipboard', '#paste-device'].forEach(selector => { $(selector).disabled = true; });
  try {
    if (window.isSecureContext && navigator.clipboard?.readText) {
      try {
        const value = await navigator.clipboard.readText();
        if (!stillCurrent()) return;
        insertComposer(value); setStatus('Pasted into composer; review before sending'); return;
      } catch { /* manual fallback */ }
    }
    if (!stillCurrent()) return;
    $('#paste-sheet-text').value = '';
    $('#paste-sheet').showModal();
    $('#paste-sheet-text').focus();
  } finally {
    pastePending = false;
    ['#paste-clipboard', '#paste-device'].forEach(selector => { $(selector).disabled = false; });
  }
}

function makePeerRow(session) {
  const row = document.createElement('article'); row.className = 'session-row';
  const details = document.createElement('div');
  const title = document.createElement('h3'); title.textContent = session.tmux_name;
  const meta = document.createElement('p'); meta.className = 'meta'; meta.textContent = `${session.tool || 'legacy'} · ${session.profile || 'legacy'} · ${session.live_state || 'tmux live'} · ${session.current_command || 'no process'}`;
  details.append(title, meta);
  const actions = document.createElement('div'); actions.className = 'tree-node-actions';
  const choices = [
    ['Insert name', () => insertComposer(session.tmux_name)],
    ['Insert review command', () => insertComposer(`agentctl session review ${session.tmux_name}`)],
    ['Copy name', () => copyAndReport(session.tmux_name, 'Session name copied', 'Copy session name')],
    ['Open terminal', () => window.open(`/terminal?session=${encodeURIComponent(session.tmux_name)}`, '_blank', 'noopener')],
  ];
  for (const [label, handler] of choices) {
    const button = document.createElement('button'); button.textContent = label;
    button.onclick = () => { handler(); if (label.startsWith('Insert')) { $('#peers-dialog').close(); showComposer(true, true); } };
    actions.append(button);
  }
  row.append(details, actions); return row;
}

async function openPeers() {
  closeMore(true);
  const list = $('#peer-list'); list.innerHTML = '<p class="empty">Loading peer sessions…</p>';
  $('#peers-dialog').showModal();
  try {
    const sessions = await fetch('/api/sessions?state=active').then(async (response) => {
      const body = await response.json(); if (!response.ok) throw new Error(body.detail || response.statusText); return body;
    });
    const peers = sessions.filter((session) => session.tmux_name !== name);
    list.replaceChildren(...peers.map(makePeerRow));
    if (!peers.length) list.innerHTML = '<p class="empty">No other active sessions.</p>';
  } catch (error) { list.innerHTML = `<p class="empty"></p>`; $('p', list).textContent = error.message || String(error); }
}

// Mouse reports are terminal input, not typing. Do not cancel tmux copy mode
// between wheel events; tmux routes them to the application that requested them.
terminal.onData((value) => { if (mode === 'type') { try { send(value, /^\x1b\[(?:<|M)/.test(value)); } catch (error) { setStatus(error.message); showComposer(true); } } });
terminal.onBinary((value) => {
  if (mode === 'type' && socket?.readyState === WebSocket.OPEN) {
    socket.send(Uint8Array.from(value, character => character.charCodeAt(0)));
  }
});
// Let xterm handle native paste in typing mode, including bracketed paste and
// HTTP origins. The toolbar Paste action remains an explicit draft workflow.
// Read-only interaction modes stage native paste instead of silently dropping it.
document.addEventListener('paste', event => {
  if (!event.target.closest?.('#terminal') || !event.clipboardData) return;
  if (mode === 'type') return;
  const text = event.clipboardData.getData('text/plain');
  event.preventDefault(); event.stopImmediatePropagation();
  if (text) { insertComposer(text); setStatus('Pasted into composer; review before sending'); }
}, true);
// Let the browser's native Copy command run when output is selected; Ctrl-C
// without a selection keeps its normal terminal interrupt meaning.
terminal.attachCustomKeyEventHandler(event => {
  if (event.ctrlKey && event.shiftKey && !event.altKey && event.key.toLowerCase() === 'c') {
    event.preventDefault();
    if (event.type === 'keydown') copySelection();
    return false;
  }
  if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'v') return false;
  if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'c' && terminal.hasSelection()) return false;
  return true;
});
// A TUI can own mouse reporting even when stdin is disabled. Explicit Select
// mode uses xterm's public cell-selection API so drag never reaches the TUI.
const selectionSurface = $('#terminal');
let selectionDrag = null;
function cancelDragSelection() {
  const pointer = selectionDrag?.id;
  selectionDrag = null;
  if (pointer !== undefined && selectionSurface.hasPointerCapture(pointer)) selectionSurface.releasePointerCapture(pointer);
}
function selectionCell(event) {
  const bounds = $('.xterm-screen').getBoundingClientRect();
  const column = Math.max(0, Math.min(terminal.cols, Math.round((event.clientX - bounds.left) * terminal.cols / bounds.width)));
  const row = Math.max(0, Math.min(terminal.rows - 1, Math.floor((event.clientY - bounds.top) * terminal.rows / bounds.height)));
  return (terminal.buffer.active.viewportY + row) * terminal.cols + column;
}
function updateDragSelection(event) {
  const end = selectionCell(event), start = Math.min(selectionDrag.anchor, end);
  terminal.select(start % terminal.cols, Math.floor(start / terminal.cols), Math.abs(end - selectionDrag.anchor));
}
selectionSurface.addEventListener('pointerdown', event => {
  if (mode !== 'select' || event.button !== 0 || !event.isPrimary) return;
  // Ordinary mouse selection retains xterm's word/line selection and auto-scroll.
  if (event.pointerType === 'mouse' && terminal.modes.mouseTrackingMode === 'none') return;
  event.preventDefault(); event.stopImmediatePropagation();
  selectionDrag = {id: event.pointerId, anchor: selectionCell(event)};
  selectionSurface.setPointerCapture(event.pointerId);
  terminal.focus(); updateDragSelection(event);
}, true);
selectionSurface.addEventListener('pointermove', event => {
  if (!selectionDrag || selectionDrag.id !== event.pointerId) return;
  event.preventDefault(); event.stopImmediatePropagation(); updateDragSelection(event);
}, true);
selectionSurface.addEventListener('pointerup', event => {
  if (!selectionDrag || selectionDrag.id !== event.pointerId) return;
  event.preventDefault(); event.stopImmediatePropagation(); updateDragSelection(event);
  selectionDrag = null;
  if (selectionSurface.hasPointerCapture(event.pointerId)) selectionSurface.releasePointerCapture(event.pointerId);
}, true);
for (const type of ['pointercancel', 'lostpointercapture']) selectionSurface.addEventListener(type, cancelDragSelection);
window.addEventListener('blur', cancelDragSelection);
terminal.onScroll(() => {
  if (historyMode) return;
  const atBottomNow = atBottom();
  newOutput.hidden = atBottomNow;
  if (atBottomNow) {
    following = true;
    hasUnread = false;
    newOutput.classList.remove('has-unread');
  } else {
    following = false;
    newOutput.textContent = 'Scroll to bottom';
  }
});
window.__terminal = terminal;
// Own scroll-mode touch gestures in one place. xterm 6 uses a virtual
// scrollbar, so native overflow scrolling is not enough in a nested iframe.
let touchViewport = 0;
let touchAlternate = false;
let touchLines = 0;
let touchNative = false;
let touchLastY = null;
$('#terminal').addEventListener('wheel', (event) => {
  if (event.ctrlKey) return;
  if (mode === 'type' && terminal.modes.mouseTrackingMode !== 'none') {
    // Let xterm normalize high-resolution trackpad deltas and encode native mouse
    // input. Full-screen agents own their history; tmux copy mode freezes that UI.
    nativeScrolled = true;
    return;
  }
  if (!['scroll', 'type'].includes(mode) || !serverHistory) return;
  event.preventDefault(); event.stopPropagation();
  const lines = Math.sign(event.deltaY) * Math.max(1, Math.round(Math.abs(event.deltaY) / (event.deltaMode === 0 ? 20 : 1)));
  if (lines < 0 || historyMode) scrollHistory(lines);
}, { passive: false, capture: true });
$('#terminal').addEventListener('touchstart', (event) => {
  if (!['scroll', 'type'].includes(mode) || event.touches.length !== 1) { touchStartY = null; return; }
  touchStartY = event.touches[0].clientY; touchLastY = touchStartY;
  touchNative = mode === 'type' && terminal.modes.mouseTrackingMode !== 'none';
  touchViewport = terminal.buffer.active.viewportY; touchLines = 0;
  touchAlternate = terminal.buffer.active.type === 'alternate' || alternateScreen;
  event.stopPropagation();
}, { passive: true, capture: true });
$('#terminal').addEventListener('touchmove', (event) => {
  if (touchStartY === null || event.touches.length !== 1) return;
  event.preventDefault(); event.stopPropagation();
  if (touchNative) {
    const touch = event.touches[0], deltaY = touchLastY - touch.clientY;
    touchLastY = touch.clientY;
    $('.xterm-screen').dispatchEvent(new WheelEvent('wheel', {deltaY, clientX:touch.clientX, clientY:touch.clientY, bubbles:true, cancelable:true}));
    return;
  }
  if (serverHistory || !touchAlternate) {
    const height = $('.xterm-screen')?.getBoundingClientRect().height || terminal.rows * 16;
    const lines = Math.round((event.touches[0].clientY - touchStartY) / (height / terminal.rows));
    if (serverHistory) {
      const step = touchLines - lines;
      if (step < 0 || historyMode) scrollHistory(step);
      touchLines = lines;
    } else terminal.scrollToLine(Math.max(0, touchViewport - lines));
  }
}, { passive: false, capture: true });
$('#terminal').addEventListener('touchend', (event) => {
  if (!['scroll', 'type'].includes(mode) || touchStartY === null) return;
  const delta = (event.changedTouches[0]?.clientY ?? touchStartY) - touchStartY;
  touchStartY = null; event.stopPropagation();
  if (mode === 'scroll' && !serverHistory && touchAlternate && Math.abs(delta) >= 48) {
    try { send(delta > 0 ? '\x1b[5~' : '\x1b[6~'); } catch (error) { setStatus(error.message); }
  }
}, { passive: true, capture: true });
$('#terminal').addEventListener('touchcancel', () => { touchStartY = null; }, { passive: true });
new ResizeObserver(resize).observe($('.terminal-frame'));
window.visualViewport?.addEventListener('resize', syncVisualViewport);
window.visualViewport?.addEventListener('scroll', syncVisualViewport);
window.addEventListener('resize', syncVisualViewport);

$$('[data-mode]').forEach((button) => button.onclick = () => { $('#terminal-more').open = false; setMode(button.dataset.mode); });
$$('[data-key]').forEach((button) => button.onclick = () => {
  try { send(JSON.parse(`"${button.dataset.key}"`)); } catch (error) { setStatus(error.message); }
});
$$('dialog').forEach(dialog => dialog.addEventListener('close', () => { clipboardContext++; }));
$$('[data-close]').forEach((button) => button.onclick = () => document.getElementById(button.dataset.close).close());
reconnect.onclick = () => { autoReconnectEnabled = true; cancelReconnect(); connect(); };
$('#detach').onclick = () => {
  try {
    if (socket?.readyState !== WebSocket.OPEN) throw new Error('Terminal is disconnected');
    socket.send(JSON.stringify({ type: 'detach' }));
  } catch (error) { setStatus(error.message); }
};
$('#fullscreen').onclick = async () => { try { await document.documentElement.requestFullscreen?.(); } catch (error) { setStatus(`Fullscreen unavailable: ${error.message}`); } };
$('#load-brief').onclick = () => { $('#terminal-more').open = false; loadBrief(false); };
$('#text-refresh').onclick = () => refreshTextView();
$('#text-page-up').onclick = () => refreshTextView('up');
$('#text-page-down').onclick = () => refreshTextView('down');
$('#copy-visible').onclick = async () => {
  const text = $('#text-content').textContent;
  await copyAndReport(text, 'Visible text copied');
};
// Moving pointer focus to this button otherwise collapses the text selection
// before click runs. Keyboard activation retains the browser selection.
function selectionCopyAction(button, action) {
  let touchActivated = false, touchPointer = null;
  button.onpointerdown = event => {
    touchActivated = false;
    touchPointer = event.pointerType === 'touch' && event.isPrimary ? event.pointerId : null;
    if (event.pointerType === 'mouse') event.preventDefault();
  };
  // After dragging text, mobile browsers can omit the compatibility click.
  // Activate on a completed tap, then ignore its optional click exactly once.
  button.onpointerup = event => {
    if (event.pointerType !== 'touch' || event.pointerId !== touchPointer) return;
    touchPointer = null;
    if (!button.contains(document.elementFromPoint(event.clientX, event.clientY))) return;
    touchActivated = true; event.preventDefault(); action();
  };
  button.onpointercancel = () => { touchActivated = false; touchPointer = null; };
  button.onclick = event => {
    const alreadyActivated = touchActivated && event.detail !== 0;
    touchActivated = false;
    if (alreadyActivated) { event.preventDefault(); return; }
    action();
  };
}
selectionCopyAction($('#copy-dom-selection'), copyDomSelection);
// Preserve terminal/browser selection and the draft insertion caret on pointer use.
selectionCopyAction($('#copy-selection'), copySelection);
$('#paste-clipboard').onclick = pasteFromDevice;
$('#paste-device').onclick = pasteFromDevice;
$('#peers').onclick = openPeers;
$('#send').onclick = () => submit(false);
$('#send-enter').onclick = () => submit(true);
newOutput.onclick = () => { leaveHistory(); terminal.scrollToBottom(); following = true; hasUnread = false; newOutput.hidden = true; newOutput.classList.remove('has-unread'); };
$('#use-manual-paste').onclick = () => { const text = $('#paste-sheet-text').value; $('#paste-sheet').close(); insertComposer(text); setStatus('Pasted into composer; review before sending'); };
composer.addEventListener('input', () => { saveDraft(); autoSizeComposer(); });
composer.addEventListener('keydown', (event) => {
  if (!coarsePointer && event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); submit(true); }
});

initTheme($('#terminal-theme'), () => { terminal.options.theme = xtermTheme(); });
saveDraft();
setMode(mode, false); syncVisualViewport(); autoSizeComposer(); autoReconnectEnabled = true; cancelReconnect(); connect();
refreshIdentity().then(() => fetch(sessionPath('review?lines=1'), { cache: 'no-store' }))
  .then((response) => response.ok ? response.json() : null)
  .then((body) => { alternateScreen = Boolean(body?.alternate_screen); })
  .catch(() => {});

fetch('/api/interface', { cache: 'no-store' })
  .then(response => response.ok ? response.json() : null)
  .then(info => { serverHistory = info?.terminal_scroll === 'tmux'; })
  .catch(() => {});
