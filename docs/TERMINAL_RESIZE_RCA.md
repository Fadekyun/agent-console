# Repeated text after maximizing a terminal (#196)

A short terminal could show an old prompt again after being maximized. The
browser reproduced this with one WebSocket and no outgoing input: one visible
`USER-DRAFT` became two copies. This was a display buffer error, not a repeated
submission.

## Cause

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
It leaves populated buffers and normal scrollback alone.

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
