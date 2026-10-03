// Clipboard permissions differ between localhost, HTTPS and plain LAN HTTP.
// Keep a selectable fallback when both browser copy methods are unavailable.
export async function copyText(value, { title = 'Copy text' } = {}) {
  const origin = document.activeElement;
  const dialog = origin?.closest('dialog');
  const href = location.href;
  if (isSecureContext && navigator.clipboard?.writeText) {
    let invalidated = false;
    const invalidate = () => { invalidated = true; };
    dialog?.addEventListener('close', invalidate);
    window.addEventListener('hashchange', invalidate);
    try { await navigator.clipboard.writeText(value); return true; } catch { /* try native copy */ }
    finally { dialog?.removeEventListener('close', invalidate); window.removeEventListener('hashchange', invalidate); }
    // A permission prompt can outlive the view that requested it.
    if (invalidated || location.href !== href || (dialog && !dialog.open) || document.activeElement !== origin) return null;
  }
  const selection = getSelection();
  const ranges = Array.from({ length: selection?.rangeCount || 0 }, (_, i) => selection.getRangeAt(i).cloneRange());
  const inputSelection = origin && typeof origin.selectionStart === 'number'
    ? [origin.selectionStart, origin.selectionEnd, origin.selectionDirection] : null;
  const restore = () => {
    const details = origin?.closest('details');
    const target = details && !details.open ? details.querySelector('summary') : origin;
    if (target?.isConnected) target.focus({ preventScroll: true });
    if (inputSelection) origin.setSelectionRange(...inputSelection);
    if (selection) { selection.removeAllRanges(); ranges.forEach(range => selection.addRange(range)); }
  };
  const field = document.createElement('textarea');
  field.className = 'visually-hidden'; field.value = value; field.readOnly = true;
  // A textarea outside an open modal is inert and cannot be copied.
  (dialog?.open ? dialog : document.body).append(field);
  let copied = false;
  try { field.select(); copied = Boolean(document.execCommand?.('copy')); }
  catch { /* show the selectable sheet below */ }
  finally { field.remove(); restore(); }
  if (copied) return true;

  const sheet = document.createElement('dialog');
  sheet.setAttribute('aria-label', title);
  const form = document.createElement('form'); form.method = 'dialog'; form.className = 'dialog-form';
  const heading = document.createElement('h2'); heading.textContent = title;
  const hint = document.createElement('p'); hint.textContent = 'Select the text and use your device’s Copy command.';
  const text = document.createElement('textarea'); text.value = value; text.readOnly = true; text.rows = 3; text.setAttribute('aria-label', title);
  const actions = document.createElement('div'); actions.className = 'dialog-actions';
  const close = document.createElement('button'); close.textContent = 'Close'; actions.append(close);
  form.append(heading, hint, text, actions); sheet.append(form); document.body.append(sheet);
  sheet.addEventListener('close', () => { sheet.remove(); restore(); }, { once: true });
  sheet.showModal(); text.focus(); text.select();
  return false;
}
