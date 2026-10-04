import '/static/theme-bootstrap.js?v=1';

const appearance = window.AgentConsoleAppearance;
export function currentTheme() { return appearance.state().mode; }
export function applyTheme(mode) { return appearance.set({...appearance.state(), mode}).mode; }

export function initTheme(select, onChange = () => {}) {
  const controls = Array.isArray(select) ? select.filter(Boolean) : select ? [select] : [];
  const palettes = [...document.querySelectorAll('[data-palette-select]')];
  appearance.subscribe(({mode, palette}) => {
    controls.forEach(control => { control.value = mode; });
    palettes.forEach(control => { control.value = palette; });
    onChange(mode);
  });
  controls.forEach(control => control.addEventListener('change', () => appearance.set({...appearance.state(), mode: control.value})));
  palettes.forEach(control => control.addEventListener('change', () => appearance.set({...appearance.state(), palette: control.value})));
  return currentTheme();
}

export function xtermTheme() {
  const styles = getComputedStyle(document.documentElement);
  const value = name => styles.getPropertyValue(name).trim();
  const theme = {
    background: value('--terminal-bg'), foreground: value('--terminal-text'),
    cursor: value('--terminal-cursor'), cursorAccent: value('--terminal-bg'),
    selectionBackground: value('--terminal-selection'),
    selectionInactiveBackground: value('--terminal-selection-inactive'),
  };
  for (const name of ['black','red','green','yellow','blue','magenta','cyan','white',
    'brightBlack','brightRed','brightGreen','brightYellow','brightBlue','brightMagenta','brightCyan','brightWhite']) {
    theme[name] = value(`--ansi-${name}`);
  }
  return theme;
}
