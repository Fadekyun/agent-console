// xterm 6.0 can retain an empty alternate buffer's old capacity (#6163),
// or finish a resize without live lines for every viewport row (#6063).
// Remove this version-bound hook once an upstream release passes both regressions.
export function guardTerminalResize(terminal) {
  function fitBufferRows() {
    const buffers = terminal._core?._bufferService?.buffers;
    for (const buffer of [buffers?.normal, buffers?.alt]) {
      const lines = buffer?.lines;
      if (!lines || typeof lines.length !== 'number' || typeof lines.maxLength !== 'number'
          || typeof lines.push !== 'function' || typeof buffer.getBlankLine !== 'function'
          || typeof buffer.getNullCell !== 'function' || !Number.isInteger(buffer.ybase)) {
        throw new Error('Unsupported xterm resize adapter; revalidate the pinned terminal version');
      }
      if (lines.length === 0) {
        // An unused alternate buffer must stay empty until it is activated.
        if (buffer === buffers.alt && lines.maxLength > terminal.rows) lines.maxLength = terminal.rows;
        continue;
      }
      const required = buffer.ybase + terminal.rows;
      // A full CircularList recycles instead of growing. Do not loop forever or
      // discard real history if a different corruption exceeds its capacity.
      if (buffer.ybase < 0 || required > lines.maxLength) {
        throw new Error('Unsupported xterm buffer bounds; revalidate the pinned terminal version');
      }
      // Preserve every existing line. Allocate distinct blanks for missing rows
      // before the renderer listeners and the next asynchronous PTY write run.
      const missing = required - lines.length;
      for (let i = 0; i < missing; i++) lines.push(buffer.getBlankLine(buffer.getNullCell()));
    }
  }
  fitBufferRows();
  return terminal.onResize(fitBufferRows);
}
