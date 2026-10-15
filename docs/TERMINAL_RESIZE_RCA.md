# Repeated text after maximizing a terminal (#196)

A short terminal could show an old prompt again after being maximized. The
browser reproduced this with one WebSocket and no outgoing input: one visible
`USER-DRAFT` became two copies. This was a display buffer error, not a repeated
submission.

## First cause: empty alternate-buffer capacity

Console uses xterm 6.0.0. A terminal starts with 24 rows and fits its container
before tmux enters the alternate screen. A short dock can fit to fewer than 24
rows while that alternate buffer is still empty.

In the pinned xterm version, shrinking an empty buffer does not reduce its
capacity. It can therefore retain old rows when it later scrolls, even though an
alternate screen should have no scrollback. Growing the terminal can pull those
rows back into view. A disposable tmux/browser probe showed a 13-row alternate
screen holding 24 rows; a deterministic browser test reproduced the duplicate.
The dependency has the same reported defect in
[xterm issue 6163](https://github.com/xtermjs/xterm.js/issues/6163).

The maximize handlers retain the iframe and connection. Plain-shell and installed
Pi-renderer probes against private tmux sockets did not duplicate their native
pane history. The regression isolates the browser buffer using synthetic ANSI
redraws, without reading or sending anything to a real agent.

## Fix and prevention

`guardTerminalResize` reduces an empty alternate buffer's capacity to the current
row count after each resize. It installs before the first fit and also handles
returning to the normal screen, shrinking, and entering the alternate screen again.
The initial fix left populated buffers and normal scrollback alone.

This is a small compatibility hook into the pinned xterm version, like the
existing input compatibility hook. Its shape check makes an incompatible upgrade
fail visibly. Remove it after an upstream replacement passes the regression tests.
Do not deduplicate terminal text or clear live history to mask the symptom.

`tests/ui/terminal-resize.spec.mjs` checks the visible prompt count before and after
maximize, repeated alternate-screen entry, preserved normal history, unsent
drafts, one retained iframe/socket, and exact subsequent typing. The existing
input, identity, and layout suites cover the adjacent behavior.

After installing the fix, reload the Console page once to create a clean browser
terminal. Running agents continue in tmux. Existing corrupted browser buffers
cannot be safely repaired by this prevention-only hook. No settings, credentials,
or database migration are needed.

## Follow-up: missing visible rows after resize

The owner still saw corruption after the initial rollout. Their Brave/macOS screenshot shows
an idle Codex session, the Workbench expanded with its Input drawer open, and
the green tmux status line stranded above repeated text. This differs from the
original short-terminal reproduction.

The pinned dependency also has a reported resize failure in
[xterm issue 6063](https://github.com/xtermjs/xterm.js/issues/6063): its line list
can end before the visible viewport. Stale circular-list slots can temporarily
hide that state, then resizing exposes missing lines to the asynchronous ANSI
parser. A subsequent erase or write can fail partway through redraw.

The follow-up regression explicitly injects that reported logical-length fault,
then grows the terminal and writes into the newly visible rows. The normal and inactive-normal variants fail against
the original guard. This proves the buffer failure and repair; it does not establish how the
owner's browser first entered that state. Private native Codex and Linux Chromium
resize probes did not reproduce the natural Mac trigger.

The same compatibility hook now checks both initialized buffers immediately after
resize and before renderer resize listeners. It appends distinct blank lines only
where visible rows are missing. Existing lines and real history remain intact;
the unused alternate screen stays empty. A capacity bound prevents recycling
history or looping indefinitely if some other unsupported state occurs.

`tests/ui/terminal-resize-invariant.spec.mjs` covers the forced precondition,
subsequent ANSI erase/write, initialized buffers, preserved rows, and unused
alternate-screen behavior. Workbench coverage uses device scale 2 and an open
Input drawer, checks the status line at the bottom after first expansion, and
keeps the same iframe and socket without sending input. The owner environment
still needs confirmation after rollout. Reloading discards an already-corrupted
browser buffer while preserving the running agent.
