/* Synchronous first-paint preferences; safe when storage is unavailable. */
(() => {
  if (window.AgentConsoleAppearance) return;
  const modes = ['system', 'light', 'dark'];
  const palettes = ['forest', 'ocean', 'violet'];
  const media = matchMedia('(prefers-color-scheme: dark)');
  const read = (key, values, fallback) => {
    try { const value = localStorage.getItem(key); return values.includes(value) ? value : fallback; }
    catch { return fallback; }
  };
  let mode = read('agent-console-theme', modes, 'system');
  let palette = read('agent-console-palette', palettes, 'forest');
  const subscribers = new Set();
  const state = () => ({mode, palette});
  function render() {
    const resolved = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode;
    Object.assign(document.documentElement.dataset, {theme: mode, palette, colorMode: resolved});
    document.documentElement.style.colorScheme = resolved;
    subscribers.forEach(fn => fn(state()));
  }
  function broadcast() {
    const message = {type: 'agent-console:appearance', ...state()};
    if (parent !== window) parent.postMessage(message, location.origin);
    document.querySelectorAll('iframe').forEach(frame => frame.contentWindow?.postMessage(message, location.origin));
  }
  function set(next, persist = true) {
    const nextMode = modes.includes(next.mode) ? next.mode : 'system';
    const nextPalette = palettes.includes(next.palette) ? next.palette : 'forest';
    const changed = mode !== nextMode || palette !== nextPalette;
    mode = nextMode; palette = nextPalette;
    if (persist) {
      try { localStorage.setItem('agent-console-theme', mode); localStorage.setItem('agent-console-palette', palette); } catch { /* In-memory preference still works. */ }
    }
    render();
    if (changed) broadcast();
    return state();
  }
  window.addEventListener('storage', event => {
    if (event.key === null || ['agent-console-theme', 'agent-console-palette'].includes(event.key)) {
      set({mode: read('agent-console-theme', modes, 'system'), palette: read('agent-console-palette', palettes, 'forest')}, false);
    }
  });
  window.addEventListener('message', event => {
    if (event.origin !== location.origin) return;
    const isFrame = [...document.querySelectorAll('iframe')].some(frame => frame.contentWindow === event.source);
    if (!(isFrame || (parent !== window && event.source === parent))) return;
    if (event.data?.type === 'agent-console:appearance-request' && isFrame) {
      event.source.postMessage({type: 'agent-console:appearance', ...state()}, location.origin);
    } else if (event.data?.type === 'agent-console:appearance' && modes.includes(event.data.mode) && palettes.includes(event.data.palette)) {
      set(event.data, false);
    }
  });
  media.addEventListener?.('change', () => { if (mode === 'system') render(); });
  window.AgentConsoleAppearance = {state, set, subscribe(fn) { subscribers.add(fn); fn(state()); return () => subscribers.delete(fn); }};
  render();
  if (parent !== window) parent.postMessage({type: 'agent-console:appearance-request'}, location.origin);
})();
