const STORAGE_KEY = 'agent-console-theme';
const VALID_THEMES = new Set(['system', 'light', 'dark']);

export function currentTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  return VALID_THEMES.has(stored) ? stored : 'system';
}

export function applyTheme(theme) {
  const selected = VALID_THEMES.has(theme) ? theme : 'system';
  document.documentElement.dataset.theme = selected;
  document.documentElement.style.colorScheme = selected === 'system' ? 'light dark' : selected;
  return selected;
}

export function initTheme(select, onChange = () => {}) {
  const controls = Array.isArray(select) ? select.filter(Boolean) : select ? [select] : [];
  const media = matchMedia('(prefers-color-scheme: dark)');
  const update = () => {
    const selected = applyTheme(currentTheme());
    controls.forEach((control) => { control.value = selected; });
    onChange(selected);
  };
  update();
  controls.forEach((control) => control.addEventListener('change', () => {
    const selected = applyTheme(control.value);
    localStorage.setItem(STORAGE_KEY, selected);
    controls.forEach((other) => { other.value = selected; });
    onChange(selected);
  }));
  media.addEventListener?.('change', () => {
    if (currentTheme() === 'system') onChange('system');
  });
  return currentTheme();
}

export function xtermTheme() {
  const styles = getComputedStyle(document.documentElement);
  return {
    background: styles.getPropertyValue('--terminal-bg').trim(),
    foreground: styles.getPropertyValue('--terminal-text').trim(),
    cursor: styles.getPropertyValue('--focus').trim(),
    selectionBackground: styles.getPropertyValue('--terminal-selection').trim(),
  };
}
