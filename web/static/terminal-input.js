// xterm 6.0's out-of-composition keyCode 229 fallback schedules one diff per
// keydown and can resend the entire hidden textarea on a replacement. Own only
// that fallback here; regular keys and actual IME composition stay with xterm.
// https://github.com/xtermjs/xterm.js/issues/6078
export function guardTerminalInput(terminal) {
  const textarea = terminal.textarea, surface = textarea.parentElement;
  let composing = false, settling = false, pending = null, timer = null;
  function flush() {
    clearTimeout(timer); timer = null;
    if (pending === null) return;
    const before = Array.from(pending), after = Array.from(textarea.value);
    pending = null;
    let start = 0, oldEnd = before.length, newEnd = after.length;
    while (start < oldEnd && start < newEnd && before[start] === after[start]) start++;
    while (oldEnd > start && newEnd > start && before[oldEnd - 1] === after[newEnd - 1]) { oldEnd--; newEnd--; }
    const inserted = after.slice(start, newEnd).join('');
    if (inserted) terminal.input(inserted, true);
    else if (oldEnd > start) terminal.input('\x7f', true);
  }
  surface.addEventListener('keydown', event => {
    if (event.target !== textarea) return;
    if (event.keyCode !== 229 || composing || settling) { flush(); return; }
    // Finish the preceding edit before taking the next snapshot. Multiple
    // notifications with no textarea change still produce no input.
    flush(); pending = textarea.value;
    if (timer === null) timer = setTimeout(flush, 0);
    // Allow the browser to edit the textarea, but not xterm's overlapping timer.
    event.stopImmediatePropagation();
  }, true);
  surface.addEventListener('keypress', event => {
    if (event.target === textarea && pending !== null && !composing) event.stopImmediatePropagation();
  }, true);
  surface.addEventListener('input', event => {
    if (event.target === textarea && pending !== null && !composing) {
      event.stopImmediatePropagation(); flush();
    }
  }, true);
  surface.addEventListener('compositionstart', event => {
    if (event.target !== textarea) return;
    flush(); composing = true;
  }, true);
  textarea.addEventListener('compositionend', () => {
    composing = false; settling = true;
    // Registered after xterm's target listener: release ownership only after
    // its deferred commit, including a final Process edit in that interval.
    setTimeout(() => { settling = false; }, 0);
  });
  // Capture before xterm clears its hidden textarea on blur.
  surface.addEventListener('blur', event => { if (event.target === textarea) flush(); }, true);
}
