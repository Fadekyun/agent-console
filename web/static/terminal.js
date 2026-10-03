import { Terminal } from '/vendor/xterm.mjs';
import { FitAddon } from '/vendor/addon-fit.mjs';
import { initTheme, xtermTheme } from '/static/theme.js?v=10';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const name = new URLSearchParams(location.search).get('session');
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
const terminal = new Terminal({ cursorBlink: true, scrollback: 10000, fontSize: coarsePointer ? 13 : 14, theme: xtermTheme() });
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
let reconnectTimer = null;
let reconnectAttempt = 0;
const MAX_RECONNECT_ATTEMPTS = 30;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 30000;
let autoReconnectEnabled = true;
const draftKey = `agent-console:composer:${name}`;
try { const draft = sessionStorage.getItem(draftKey); if (draft !== null) { composer.value = draft; briefLoaded = true; } } catch { /* storage may be disabled */ }
function saveDraft() { $('#toggle-composer').textContent = composer.value ? 'Input · draft' : 'Input'; try { sessionStorage.setItem(draftKey, composer.value); } catch { /* continue without persistence */ } }
function showComposer(open, focus = false) {
  $('#input-drawer').hidden = !open;
  $('#toggle-composer').setAttribute('aria-expanded', String(open));
  try { sessionStorage.setItem(`${draftKey}:open`, String(open)); } catch {}
  autoSizeComposer();
  if (focus) { if (open) composer.focus(); else if (mode === 'type') terminal.focus(); }
}
$('#resume-typing').onclick = () => setMode('type');
$('#toggle-composer').onclick = () => showComposer($('#input-drawer').hidden, true);
new MutationObserver(() => { reconnect.hidden = reconnect.disabled; }).observe(reconnect, {attributes:true, attributeFilter:['disabled']});
$('#terminal-more').addEventListener('keydown', event => {
  if (event.key === 'Escape') { $('#terminal-more').open = false; $('#terminal-more > summary').focus(); }
});
document.addEventListener('pointerdown', event => {
  if (!$('#terminal-more').contains(event.target)) $('#terminal-more').open = false;
});
window.addEventListener('pagehide', saveDraft);

function setStatus(message) {
  connection.textContent = message;
  if (isEmbedded) window.parent.postMessage({ type: 'agent-console:terminal-status', status: message }, location.origin);
}

window.addEventListener('message', (event) => {
  if (!isEmbedded) return;
  if (event.origin !== window.location.origin) return;
  if (event.source !== window.parent) return;
  if (managedVisibility && event.data?.type === 'agent-console:terminal-visibility' && typeof event.data.visible === 'boolean') {
    if (viewVisible === event.data.visible) return;
    viewVisible = event.data.visible;
    if (viewVisible) { autoReconnectEnabled = true; cancelReconnect(); connect(); }
    else {
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
  resize();
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

function connect() {
  if (!viewVisible) return;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (socket) { socket.onclose = null; socket.onerror = null; socket.onmessage = null; socket.close(); }
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws/sessions/${encodeURIComponent(name)}`);
  socket.binaryType = 'arraybuffer'; setStatus('Connecting…'); reconnect.disabled = true;
  socket.onopen = () => { cancelReconnect(); socket.send(JSON.stringify({type:'scroll',lines:0})); nativeScrolled = false; historyMode = false; setStatus('Connected'); resize(); if (following) terminal.scrollToBottom(); if (focusOnConnect && mode === 'type' && document.activeElement !== composer) terminal.focus(); focusOnConnect = false; };
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
    if (event.code === 4001 || event.code === 4404) {
      autoReconnectEnabled = false; setStatus('Session ended');
      reconnect.disabled = true;
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
}

function setMode(selected, focus = true) {
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
  composer.style.height = 'auto';
  composer.style.height = `${Math.min(composer.scrollHeight, parseFloat(getComputedStyle(composer).lineHeight || '20') * 4 + 20)}px`;
  resize();
}

function submit(addEnter) {
  try {
    send(composer.value + (addEnter ? '\r' : ''));
    composer.value = ''; saveDraft(); autoSizeComposer();
    if (mode === 'type') terminal.focus();
  } catch (error) { setStatus(error.message); showComposer(true); }
}

async function copyText(value) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return true; } catch { /* fallback */ }
  }
  const fallback = $('#terminal-clipboard-fallback');
  fallback.value = value; fallback.classList.remove('visually-hidden'); fallback.select();
  try { return document.execCommand?.('copy') || false; }
  catch { return false; }
  finally { fallback.classList.add('visually-hidden'); fallback.value = ''; }
}

function showCopySheet(value) {
  $('#copy-sheet-text').value = value;
  $('#copy-sheet').showModal();
  $('#copy-sheet-text').focus(); $('#copy-sheet-text').select();
}

async function copySelection() {
  const selected = terminal.getSelection();
  if (!selected) { setStatus('Select terminal text first'); return; }
  if (await copyText(selected)) { setStatus('Selection copied'); return; }
  showCopySheet(selected);
}

async function loadBrief(silent = false) {
  if (briefLoaded && silent) return;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(name)}/brief`, { cache: 'no-store' });
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
    const response = await fetch(`/api/sessions/${encodeURIComponent(name)}/review?lines=1000`, { cache: 'no-store' });
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
  if (await copyText(selected)) setStatus('Selection copied');
  else showCopySheet(selected);
}

async function pasteFromDevice() {
  if (window.isSecureContext && navigator.clipboard?.readText) {
    try {
      const value = await navigator.clipboard.readText();
      insertComposer(value); setStatus('Pasted into composer; review before sending'); return;
    } catch { /* manual fallback */ }
  }
  $('#paste-sheet-text').value = '';
  $('#paste-sheet').showModal();
  $('#paste-sheet-text').focus();
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
    ['Copy name', async () => setStatus(await copyText(session.tmux_name) ? 'Session name copied' : 'Copy blocked by browser')],
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
terminal.onSelectionChange(() => { /* xterm selection is secondary to selectable Text View */ });
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
$$('[data-close]').forEach((button) => button.onclick = () => document.getElementById(button.dataset.close).close());
reconnect.onclick = () => { autoReconnectEnabled = true; cancelReconnect(); connect(); };
$('#detach').onclick = () => {
  try {
    if (socket?.readyState !== WebSocket.OPEN) throw new Error('Terminal is disconnected');
    socket.send(JSON.stringify({ type: 'detach' }));
  } catch (error) { setStatus(error.message); }
};
$('#fullscreen').onclick = async () => { try { await document.documentElement.requestFullscreen?.(); } catch (error) { setStatus(`Fullscreen unavailable: ${error.message}`); } };
$('#text-view').onclick = () => refreshTextView();
$('#load-brief').onclick = () => { $('#terminal-more').open = false; loadBrief(false); };
$('#text-refresh').onclick = () => refreshTextView();
$('#text-page-up').onclick = () => refreshTextView('up');
$('#text-page-down').onclick = () => refreshTextView('down');
$('#copy-visible').onclick = async () => {
  const text = $('#text-content').textContent;
  if (await copyText(text)) setStatus('Visible text copied');
  else showCopySheet(text);
};
// Moving pointer focus to this button otherwise collapses the text selection
// before click runs. Keyboard activation retains the browser selection.
$('#copy-dom-selection').onpointerdown = event => event.preventDefault();
$('#copy-dom-selection').onclick = copyDomSelection;
$('#paste-device').onclick = pasteFromDevice;
$('#peers').onclick = openPeers;
$('#send').onclick = () => submit(false);
$('#send-enter').onclick = () => submit(true);
newOutput.onclick = () => { leaveHistory(); terminal.scrollToBottom(); following = true; hasUnread = false; newOutput.hidden = true; newOutput.classList.remove('has-unread'); };
$('#use-manual-paste').onclick = () => { insertComposer($('#paste-sheet-text').value); $('#paste-sheet').close(); setStatus('Pasted into composer; review before sending'); };
composer.addEventListener('input', () => { saveDraft(); autoSizeComposer(); });
composer.addEventListener('keydown', (event) => {
  if (!coarsePointer && event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(true); }
});

initTheme($('#terminal-theme'), () => { terminal.options.theme = xtermTheme(); });
saveDraft();
try { showComposer(sessionStorage.getItem(`${draftKey}:open`) === 'true'); } catch {}
setMode(mode, false); syncVisualViewport(); autoSizeComposer(); autoReconnectEnabled = true; cancelReconnect(); connect(); loadBrief(true);
fetch(`/api/sessions/${encodeURIComponent(name)}/review?lines=1`, { cache: 'no-store' })
  .then((response) => response.ok ? response.json() : null)
  .then((body) => { alternateScreen = Boolean(body?.alternate_screen); })
  .catch(() => {});

fetch('/api/interface', { cache: 'no-store' })
  .then(response => response.ok ? response.json() : null)
  .then(info => { serverHistory = info?.terminal_scroll === 'tmux'; })
  .catch(() => {});
