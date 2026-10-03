// Compact familiar secondary actions. Unfamiliar decisions and primary actions
// retain text. The DOM text remains the accessible name, including during updates.
const common = new Map([
  ['refresh', 'refresh'], ['close', 'close'], ['close terminal', 'close'],
  ['stop session', 'stop'], ['full screen', 'expand'], ['exit full screen', 'collapse'],
  ['history', 'history'], ['read output', 'output'], ['configuration', 'settings'],
]);
const managed = new WeakMap();
function title(button, value) { if (button.getAttribute('title') !== value) button.setAttribute('title', value); }
function sync(button) {
  const label = (button.getAttribute('aria-label') || button.textContent).trim();
  let record = managed.get(button);
  if (!record) record = {auto: !button.hasAttribute('data-icon'), description: button.getAttribute('title') || ''};
  if (record.auto) {
    const icon = common.get(button.textContent.trim().toLowerCase()) || common.get(label.toLowerCase());
    if (!icon) {
      if (managed.has(button)) {
        button.removeAttribute('data-icon'); button.removeAttribute('data-icon-only');
        if (record.description) title(button, record.description); else button.removeAttribute('title');
        managed.delete(button);
      }
      return;
    }
    if (button.dataset.icon !== icon) button.dataset.icon = icon;
    if (!button.hasAttribute('data-icon-only')) button.setAttribute('data-icon-only', '');
  } else if (!button.hasAttribute('data-icon')) return;
  else if (['expand', 'collapse'].includes(button.dataset.icon)) {
    const icon = /restore|exit|collapse/i.test(label) ? 'collapse' : 'expand';
    if (button.dataset.icon !== icon) button.dataset.icon = icon;
  }
  managed.set(button, record);
  if (button.hasAttribute('data-icon-only')) {
    title(button, record.description && record.description !== label ? `${label} — ${record.description}` : label);
  }
}
function update() {
  document.querySelectorAll('.terminal-tab-close').forEach(button => {
    button.dataset.icon = 'close';
    if (!button.hasAttribute('data-icon-only')) button.setAttribute('data-icon-only', '');
  });
  document.querySelectorAll('button, a[data-icon], [role="button"][data-icon]').forEach(sync);
  // The collapsed navigation's visible label is hidden by its existing layout.
  document.querySelectorAll('.nav-button').forEach(button => {
    if (!button.title) title(button, button.getAttribute('aria-label') || button.textContent.trim());
  });
  const rail = document.querySelector('#rail-toggle');
  if (rail) {
    title(rail, rail.getAttribute('aria-label') || 'Collapse navigation');
    const icon = rail.querySelector('[data-icon]');
    if (icon) icon.dataset.icon = rail.title.startsWith('Expand') ? 'chevron-right' : 'chevron-left';
  }
}
let pending = false;
new MutationObserver(() => {
  if (pending) return;
  pending = true;
  queueMicrotask(() => { pending = false; update(); });
}).observe(document.body, {subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-label','data-icon-only','title']});
update();
