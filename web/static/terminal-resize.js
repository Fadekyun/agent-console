// xterm 6.0 keeps the old capacity when an unused alternate buffer shrinks.
// tmux then acquires phantom scrollback which reappears on maximize (#196).
// Remove this version-bound workaround once xtermjs/xterm.js#6163 is fixed
// upstream and the browser regressions pass with the replacement version.
export function guardTerminalResize(terminal) {
  function fitEmptyAlternateBuffer() {
    const lines = terminal._core?._bufferService?.buffers?.alt?.lines;
    if (!lines || typeof lines.length !== 'number' || typeof lines.maxLength !== 'number') {
      throw new Error('Unsupported xterm resize adapter; revalidate the pinned terminal version');
    }
    // Never trim populated output or the normal buffer's real scrollback.
    if (lines.length === 0 && lines.maxLength > terminal.rows) lines.maxLength = terminal.rows;
  }
  fitEmptyAlternateBuffer();
  return terminal.onResize(fitEmptyAlternateBuffer);
}
