// Compatibility fix for the pinned xterm 6.0 non-composing keyCode 229 fallback.
// Its native composition helper must retain ownership of composition and pending
// commits; a second DOM/timer state machine leaves gaps that can repeat input.
// https://github.com/xtermjs/xterm.js/issues/6078
export function guardTerminalInput(terminal) {
  const textarea = terminal.textarea, surface = textarea.parentElement;
  const helper = terminal._core?._compositionHelper;
  // This is intentionally one version-bound internal hook. The browser tests
  // exercise the shipped module, so an xterm upgrade must revalidate this seam.
  if (typeof helper?._handleAnyTextareaChanges !== 'function') {
    throw new Error('Unsupported xterm input adapter; revalidate the pinned terminal version');
  }
  let pending = null, timer = null;
  function flush() {
    clearTimeout(timer); timer = null;
    if (pending === null) return;
    const before = Array.from(pending), after = Array.from(textarea.value);
    pending = null;
    let start = 0, oldEnd = before.length, newEnd = after.length;
    while (start < oldEnd && start < newEnd && before[start] === after[start]) start++;
    while (oldEnd > start && newEnd > start && before[oldEnd - 1] === after[newEnd - 1]) { oldEnd--; newEnd--; }
    const inserted = after.slice(start, newEnd).join('');
    helper._dataAlreadySent = inserted;
    if (inserted) terminal.input(inserted, true);
    else if (oldEnd > start) terminal.input('\x7f', true);
  }
  helper._handleAnyTextareaChanges = () => {
    // Called by xterm only when neither composing nor awaiting its final commit.
    flush(); pending = textarea.value;
    timer = setTimeout(flush, 0);
  };
  surface.addEventListener('keydown', event => {
    // Flush before xterm clears the textarea for Enter or handles the next key.
    if (event.target === textarea) flush();
  }, true);
  surface.addEventListener('keypress', event => {
    if (event.target === textarea && pending !== null) event.stopImmediatePropagation();
  }, true);
  surface.addEventListener('input', event => {
    if (event.target === textarea && pending !== null) {
      event.stopImmediatePropagation(); flush();
    }
  }, true);
  surface.addEventListener('compositionstart', event => {
    if (event.target === textarea) flush();
  }, true);
  // Capture before xterm clears its hidden textarea on blur.
  surface.addEventListener('blur', event => { if (event.target === textarea) flush(); }, true);
}
