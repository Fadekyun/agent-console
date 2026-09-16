const nativeFetch = window.fetch.bind(window);
const mobilePage = document.body.classList.contains('mobile-console');
const terminalPage = document.body.classList.contains('terminal-page');
const desktopPage = document.body.classList.contains('app-page');

async function filterMobileSessions(input, init) {
  const response = await nativeFetch(input, init);
  if (!mobilePage) return response;
  const url = typeof input === 'string' ? input : input instanceof Request ? input.url : '';
  if (!url.includes('/api/sessions?state=all') || !response.ok) return response;
  try {
    const body = await response.clone().json();
    if (!Array.isArray(body)) return response;
    const active = body.filter((session) => session.running !== false);
    const headers = new Headers(response.headers);
    headers.delete('content-length');
    return new Response(JSON.stringify(active), {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  } catch {
    return response;
  }
}

if (mobilePage) {
  window.fetch = filterMobileSessions;
}

const style = document.createElement('style');
style.dataset.agentConsoleReliability = '114';
style.textContent = `
  .mobile-tabs {
    display: flex !important;
    overflow-x: auto;
    overflow-y: hidden;
    overscroll-behavior-inline: contain;
    scroll-snap-type: x proximity;
    scrollbar-width: thin;
  }
  .mobile-tabs > * {
    flex: 0 0 auto;
    min-width: clamp(82px, 24vw, 112px) !important;
    scroll-snap-align: start;
    padding-inline: 8px;
  }
  .mobile-main { min-width: 0; }
  .mobile-session, .mobile-plan, .mobile-tree-node, .mobile-profile-card { min-width: 0; }
  .mobile-session-actions { min-width: 0; }

  @media (min-width: 761px) and (max-width: 1100px) {
    .session-table-shell { border: 0; overflow: visible; background: transparent; }
    .session-table, .session-table tbody, .session-table tr, .session-table td, .session-table th {
      display: block;
      min-width: 0;
      width: 100%;
    }
    .session-table { min-width: 0 !important; }
    .session-table thead { display: none; }
    .session-table .session-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 14px;
      margin-bottom: 10px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
    }
    .session-table .session-row > * { padding: 0; border: 0; min-width: 0; }
    .session-table .session-row > :nth-child(2),
    .session-table .session-row > :nth-child(5),
    .session-table .session-row > :last-child { grid-column: 1 / -1; }
    .session-table td::before {
      display: block;
      margin-bottom: 2px;
      color: var(--muted);
      font-size: .68rem;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .session-table td:nth-child(1)::before { content: 'State'; }
    .session-table td:nth-child(2)::before { content: 'Session'; }
    .session-table td:nth-child(3)::before { content: 'Tool / mode'; }
    .session-table td:nth-child(4)::before { content: 'Role'; }
    .session-table td:nth-child(5)::before { content: 'Repository'; }
    .session-table td:nth-child(6)::before { content: 'Activity'; }
    .session-table td:nth-child(7)::before { content: 'Clients'; }
    .session-table td:nth-child(8)::before { content: 'Actions'; }
    .session-name, .repo-cell, .row-note { max-width: none; white-space: normal; overflow-wrap: anywhere; }
    .session-table .session-actions { flex-wrap: wrap; justify-content: flex-start; }
  }

  @media (max-width: 430px) {
    .mobile-header { align-items: flex-start; }
    .mobile-header > div:last-child { flex-wrap: wrap; justify-content: flex-end; }
    .mobile-header select { max-width: 96px; }
  }
`;
document.head.append(style);

async function getJson(path) {
  const response = await nativeFetch(path, { cache: 'no-store', headers: { 'Content-Type': 'application/json' } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

async function openCorrectMobileDelegate(parentName, node) {
  const form = document.querySelector('#mobile-delegate');
  const dialog = document.querySelector('#mobile-delegate-dialog');
  if (!form || !dialog) return;

  const [identity, profiles, sessions] = await Promise.all([
    getJson('/api/me'),
    getJson('/api/profiles'),
    getJson('/api/sessions?state=active'),
  ]);
  const session = sessions.find((item) => item.tmux_name === parentName);
  const fallbackProfile = node?.querySelector('small')?.textContent?.split(' · ')[1] || 'general';
  const parentProfile = session?.profile || fallbackProfile;
  const profile = profiles.find((item) => item.name === parentProfile);
  let allowed = profile?.allowed_delegation_profiles;
  if (!Array.isArray(allowed)) {
    allowed = identity.profiles
      .filter((item) => item.read_write_capability === 'read_only')
      .map((item) => item.name);
  }
  if (!allowed.length) allowed = ['planner', 'researcher', 'reviewer', 'scout'];

  form.elements.parent.value = parentName;
  form.elements.profile.replaceChildren(...allowed.map((name) => {
    const meta = identity.profiles.find((item) => item.name === name);
    return new Option(meta?.display_name || name, name, false, name === 'planner');
  }));

  const readyTools = identity.tool_status.filter((item) => item.status === 'ready');
  form.elements.tool.replaceChildren(...readyTools.map((item) => new Option(
    item.name,
    item.name,
    false,
    item.name === identity.default_tool,
  )));

  const refreshContexts = () => {
    const tool = form.elements.tool.value;
    const contexts = identity.auth_contexts.filter((item) => item.tool === tool && item.enabled !== false);
    form.elements.auth_context.replaceChildren(...contexts.map((item) => new Option(
      `${item.name} · ${item.status}`,
      item.name,
      false,
      item.default,
    )));
  };
  form.elements.tool.onchange = refreshContexts;
  refreshContexts();
  form.elements.repository.value = session?.repository || '';
  form.elements.task.value = '';
  form.elements.name.value = '';
  dialog.showModal();
}

if (mobilePage) {
  document.addEventListener('click', (event) => {
    const button = event.target.closest('.mobile-tree-node button');
    if (!button || button.textContent.trim() !== 'Delegate') return;
    const node = button.closest('.mobile-tree-node');
    const parentName = node?.querySelector('strong')?.textContent?.trim();
    if (!parentName) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openCorrectMobileDelegate(parentName, node).catch((error) => window.alert(error.message || String(error)));
  }, true);
}

function setConnectionStatus(message) {
  const connection = document.querySelector('#connection');
  if (connection) connection.textContent = message;
}

function insertComposerText(text) {
  const composer = document.querySelector('#composer');
  if (!composer) return;
  const start = composer.selectionStart ?? composer.value.length;
  const end = composer.selectionEnd ?? start;
  composer.setRangeText(text, start, end, 'end');
  composer.dispatchEvent(new Event('input', { bubbles: true }));
  composer.focus();
}

async function writeClipboard(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through */ }
  }
  const fallback = document.querySelector('#terminal-clipboard-fallback');
  if (!fallback) return false;
  fallback.value = text;
  fallback.classList.remove('visually-hidden');
  fallback.select();
  const copied = document.execCommand?.('copy') || false;
  fallback.classList.add('visually-hidden');
  fallback.value = '';
  return copied;
}

async function pasteClipboardIntoComposer() {
  if (window.isSecureContext && navigator.clipboard?.readText) {
    try {
      const text = await navigator.clipboard.readText();
      insertComposerText(text);
      setConnectionStatus('Pasted into composer; review before sending');
      return;
    } catch { /* use existing manual fallback */ }
  }
  document.querySelector('#paste-device')?.click();
}

function terminalFocused(event) {
  const target = event.target instanceof Element ? event.target : document.activeElement;
  return Boolean(target?.closest?.('#terminal'));
}

function installTerminalReliability() {
  const terminal = window.__terminal;
  if (!terminal) {
    window.setTimeout(installTerminalReliability, 25);
    return;
  }
  if (document.body.dataset.reliabilityTerminal === 'ready') return;
  document.body.dataset.reliabilityTerminal = 'ready';

  if (matchMedia('(pointer: coarse)').matches) {
    document.querySelector('[data-mode="type"]')?.click();
    document.querySelector('#terminal')?.addEventListener('pointerdown', () => {
      if (document.body.dataset.terminalMode !== 'type') document.querySelector('[data-mode="type"]')?.click();
    }, { passive: true });
  }

  document.addEventListener('keydown', (event) => {
    if (!(event.ctrlKey || event.metaKey) || !terminalFocused(event)) return;
    const key = event.key.toLowerCase();
    if (key === 'c') {
      const selected = terminal.getSelection?.() || '';
      if (!selected) return; // preserve Ctrl-C interrupt when nothing is selected
      event.preventDefault();
      event.stopImmediatePropagation();
      writeClipboard(selected).then((copied) => {
        setConnectionStatus(copied ? 'Selection copied' : 'Copy blocked by browser');
      });
      return;
    }
    if (key === 'v') {
      event.preventDefault();
      event.stopImmediatePropagation();
      pasteClipboardIntoComposer();
    }
  }, true);
}

if (terminalPage) installTerminalReliability();

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function installDockReliability() {
  const dock = document.querySelector('#terminal-dock');
  const handle = document.querySelector('#terminal-dock-handle');
  if (!dock || !handle) return;
  let dragging = false;
  const ratioKey = 'agent-console-dock-height-ratio';

  const saveRatio = () => {
    if (dock.hidden || dock.classList.contains('collapsed')) return;
    const ratio = dock.getBoundingClientRect().height / Math.max(window.innerHeight, 1);
    if (Number.isFinite(ratio) && ratio > 0) localStorage.setItem(ratioKey, String(clamp(ratio, 0.2, 0.92)));
  };

  const applyRatio = () => {
    if (dock.hidden || dock.classList.contains('collapsed') || document.fullscreenElement === dock || dock.classList.contains('forced-fullscreen')) return;
    const ratio = Number.parseFloat(localStorage.getItem(ratioKey) || '');
    if (!Number.isFinite(ratio)) return;
    const maximum = Math.max(160, window.innerHeight - 56);
    const minimum = Math.min(240, maximum);
    const height = clamp(Math.round(window.innerHeight * ratio), minimum, maximum);
    dock.style.height = `${height}px`;
    document.documentElement.style.setProperty('--dock-height', `${height}px`);
    const visibleFrame = dock.querySelector('.terminal-embed:not([hidden])');
    try { visibleFrame?.contentWindow?.dispatchEvent(new Event('resize')); } catch { /* same-origin guard */ }
  };

  const storedPixels = Number.parseInt(localStorage.getItem('agent-console-dock-height') || '', 10);
  if (!localStorage.getItem(ratioKey) && Number.isFinite(storedPixels) && storedPixels > 0) {
    localStorage.setItem(ratioKey, String(clamp(storedPixels / Math.max(window.innerHeight, 1), 0.2, 0.92)));
  }

  handle.addEventListener('pointerdown', () => { dragging = true; }, true);
  window.addEventListener('pointerup', () => {
    if (!dragging) return;
    dragging = false;
    saveRatio();
  }, true);
  window.addEventListener('resize', () => requestAnimationFrame(applyRatio));
  document.addEventListener('click', (event) => {
    if (event.target.closest('.terminal-tab, [data-action="attach"], #terminal-dock-fullscreen, #terminal-dock-collapse')) {
      setTimeout(applyRatio, 0);
    }
  });
  setTimeout(applyRatio, 0);
}

if (desktopPage) installDockReliability();
