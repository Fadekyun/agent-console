import { Terminal } from '/vendor/xterm.mjs';
import { FitAddon } from '/vendor/addon-fit.mjs';
import { initTheme, xtermTheme } from '/static/theme.js?v=7';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const name = new URLSearchParams(location.search).get('session');
if (!name) location.href = '/';
$('#session-name').textContent = name;

const coarsePointer = matchMedia('(pointer: coarse)').matches;
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
let mode = coarsePointer ? 'scroll' : 'type';
let resizeFrame;
let alternateScreen = false;
let touchStartY = null;
let briefLoaded = false;

function setStatus(message) { connection.textContent = message; }

function send(value) {
  if (socket?.readyState !== WebSocket.OPEN) throw new Error('Terminal is disconnected');
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
      fit.fit();
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }));
    } catch { /* the container can be between viewport sizes */ }
  });
}

function syncVisualViewport() {
  const height = window.visualViewport?.height || window.innerHeight;
  document.documentElement.style.setProperty('--visual-height', `${Math.round(height)}px`);
  resize();
}

function connect() {
  socket?.close();
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws/sessions/${encodeURIComponent(name)}`);
  socket.binaryType = 'arraybuffer'; setStatus('Connecting…'); reconnect.disabled = true;
  socket.onopen = () => { setStatus('Connected'); resize(); if (mode === 'type') terminal.focus(); };
  socket.onmessage = (event) => {
    const keepAtBottom = atBottom();
    const output = typeof event.data === 'string' ? event.data : decoder.decode(event.data, { stream: true });
    terminal.write(output, () => {
      if (keepAtBottom) terminal.scrollToBottom();
      else { newOutput.hidden = false; newOutput.textContent = 'New output \u00b7 Scroll to bottom'; }
    });
  };
  socket.onclose = (event) => {
    setStatus(event.code === 4001 ? 'Session ended' : event.code === 4000 ? 'Detached by user; tmux is still running' : `Detached (${event.code}${event.reason ? `: ${event.reason}` : ''})`);
    reconnect.disabled = event.code === 4001;
  };
  socket.onerror = () => { setStatus('Connection error'); reconnect.disabled = false; };
}

function setMode(selected) {
  mode = ['scroll', 'type', 'select'].includes(selected) ? selected : 'scroll';
  document.body.dataset.terminalMode = mode;
  terminal.options.disableStdin = mode !== 'type';
  $$('[data-mode]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
  if (mode === 'type') terminal.focus();
  else terminal.blur();
}

function insertComposer(text) {
  const start = composer.selectionStart ?? composer.value.length;
  const end = composer.selectionEnd ?? start;
  composer.setRangeText(text, start, end, 'end');
  autoSizeComposer(); composer.focus();
}

function autoSizeComposer() {
  composer.style.height = 'auto';
  composer.style.height = `${Math.min(composer.scrollHeight, parseFloat(getComputedStyle(composer).lineHeight || '20') * 4 + 20)}px`;
  resize();
}

function submit(addEnter) {
  try {
    send(composer.value + (addEnter ? '\r' : ''));
    composer.value = ''; autoSizeComposer();
    if (mode === 'type') terminal.focus();
  } catch (error) { setStatus(error.message); }
}

async function copyText(value) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return true; } catch { /* fallback */ }
  }
  const fallback = $('#terminal-clipboard-fallback');
  fallback.value = value; fallback.classList.remove('visually-hidden'); fallback.select();
  const copied = document.execCommand?.('copy') || false;
  fallback.classList.add('visually-hidden'); fallback.value = '';
  return copied;
}

async function copySelection() {
  const selected = terminal.getSelection();
  if (!selected) { setStatus('Select terminal text first'); return; }
  if (await copyText(selected)) { setStatus('Selection copied'); return; }
  $('#copy-sheet-text').value = selected;
  $('#copy-sheet').showModal();
  $('#copy-sheet-text').focus(); $('#copy-sheet-text').select();
}

async function loadBrief(silent = false) {
  if (briefLoaded && silent) return;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(name)}/brief`, { cache: 'no-store' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || response.statusText);
    if (body.brief && (!composer.value || !silent)) {
      if (!composer.value || !silent) insertComposer(body.brief);
      briefLoaded = true;
      setStatus('Brief loaded into composer; review and send when ready');
    } else if (!silent) setStatus('This session has no stored brief');
  } catch (error) { if (!silent) setStatus(error.message); }
}

async function refreshTextView(direction = null) {
  try {
    if (direction) send(direction === 'up' ? '\x1b[5~' : '\x1b[6~');
    if (direction) await new Promise((resolve) => setTimeout(resolve, 180));
    const response = await fetch(`/api/sessions/${encodeURIComponent(name)}/review?lines=1000`, { cache: 'no-store' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || response.statusText);
    alternateScreen = body.alternate_screen;
    $('#text-content').textContent = body.content || '(no captured output)';
    $('#text-scope').textContent = body.capture_scope === 'visible-screen'
      ? 'Current alternate-screen TUI view only. Page, then refresh to inspect more.'
      : `${body.line_count} captured lines · ${body.capture_scope}${body.truncated ? ' · truncated' : ''}`;
    if (!$('#text-dialog').open) $('#text-dialog').showModal();
  } catch (error) { setStatus(error.message); }
}

async function copyDomSelection() {
  const selected = window.getSelection()?.toString() || '';
  if (!selected) { setStatus('Select text in Text View first'); return; }
  if (await copyText(selected)) setStatus('Selection copied');
  else { $('#copy-sheet-text').value = selected; $('#copy-sheet').showModal(); }
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
    button.onclick = () => { handler(); if (label.startsWith('Insert')) $('#peers-dialog').close(); };
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

terminal.onData((value) => { if (mode === 'type') { try { send(value); } catch { /* status is visible */ } } });
terminal.onSelectionChange(() => { /* xterm selection is secondary to selectable Text View */ });
terminal.onScroll(() => { newOutput.hidden = atBottom(); if (!newOutput.hidden) newOutput.textContent = 'Scroll to bottom'; });
$('#terminal').addEventListener('touchstart', (event) => {
  if (mode === 'scroll' && event.touches.length === 1) touchStartY = event.touches[0].clientY;
}, { passive: true });
$('#terminal').addEventListener('touchend', async (event) => {
  if (mode !== 'scroll' || touchStartY === null) return;
  const end = event.changedTouches[0]?.clientY ?? touchStartY;
  const delta = end - touchStartY; touchStartY = null;
  if (Math.abs(delta) < 48) return;
  if (terminal.buffer.active.type === 'alternate' || alternateScreen) {
    try { send(delta > 0 ? '\x1b[5~' : '\x1b[6~'); } catch (error) { setStatus(error.message); }
  } else terminal.scrollLines(delta > 0 ? -6 : 6);
}, { passive: true });
new ResizeObserver(resize).observe($('.terminal-frame'));
window.visualViewport?.addEventListener('resize', syncVisualViewport);
window.visualViewport?.addEventListener('scroll', syncVisualViewport);
window.addEventListener('resize', syncVisualViewport);

$$('[data-mode]').forEach((button) => button.onclick = () => setMode(button.dataset.mode));
$$('[data-key]').forEach((button) => button.onclick = () => {
  try { send(JSON.parse(`"${button.dataset.key}"`)); } catch (error) { setStatus(error.message); }
});
$$('[data-close]').forEach((button) => button.onclick = () => document.getElementById(button.dataset.close).close());
reconnect.onclick = connect;
$('#detach').onclick = () => {
  try {
    if (socket?.readyState !== WebSocket.OPEN) throw new Error('Terminal is disconnected');
    socket.send(JSON.stringify({ type: 'detach' }));
  } catch (error) { setStatus(error.message); }
};
$('#fullscreen').onclick = async () => { try { await document.documentElement.requestFullscreen?.(); } catch (error) { setStatus(`Fullscreen unavailable: ${error.message}`); } };
$('#text-view').onclick = () => refreshTextView();
$('#load-brief').onclick = () => loadBrief(false);
$('#text-refresh').onclick = () => refreshTextView();
$('#text-page-up').onclick = () => refreshTextView('up');
$('#text-page-down').onclick = () => refreshTextView('down');
$('#copy-visible').onclick = async () => setStatus(await copyText($('#text-content').textContent) ? 'Visible text copied' : 'Use native selection and system Copy');
$('#copy-dom-selection').onclick = copyDomSelection;
$('#paste-device').onclick = pasteFromDevice;
$('#peers').onclick = openPeers;
$('#send').onclick = () => submit(false);
$('#send-enter').onclick = () => submit(true);
newOutput.onclick = () => { terminal.scrollToBottom(); newOutput.hidden = true; };
$('#use-manual-paste').onclick = () => { insertComposer($('#paste-sheet-text').value); $('#paste-sheet').close(); setStatus('Pasted into composer; review before sending'); };
composer.addEventListener('input', autoSizeComposer);
composer.addEventListener('keydown', (event) => {
  if (!coarsePointer && event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(true); }
});

initTheme($('#terminal-theme'), () => { terminal.options.theme = xtermTheme(); resize(); });
setMode(mode); syncVisualViewport(); autoSizeComposer(); connect(); loadBrief(true);
fetch(`/api/sessions/${encodeURIComponent(name)}/review?lines=1`, { cache: 'no-store' })
  .then((response) => response.ok ? response.json() : null)
  .then((body) => { alternateScreen = Boolean(body?.alternate_screen); })
  .catch(() => {});
