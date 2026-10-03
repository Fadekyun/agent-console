# Web terminal references — 2026-10-02

The Mac Chrome report exposed a gap in the earlier shell-only test. Our capturing wheel
listener always called tmux copy-mode. A full-screen mouse-aware agent has its own
transcript: intercepting its wheel events freezes tmux's displayed pane instead of
scrolling the app. tmux explicitly documents that copy mode freezes pane output.
The process continues to run. Live mode-only inspection confirmed the Console's Codex
sessions use alternate screens and mouse reporting, with little or no tmux history.

## Open-source comparison

| Project | Useful pattern | Fit here |
| --- | --- | --- |
| [xterm.js](https://github.com/xtermjs/xterm.js) | Terminal emulator with native mouse reporting, input, fit and scroll handling | Already used by Console. Keep it and respect its mouse protocol instead of overriding every wheel event. |
| [ttyd](https://github.com/tsl0922/ttyd) | Thin web terminal bridge; its frontend forwards both onData and raw onBinary events and handles resize/reconnect | Best implementation reference. Adopting the event forwarding pattern avoids replacing Console sessions, authentication and relationships. |
| [WeTTY](https://github.com/butlerx/wetty) | Browser terminal focused on SSH/login integration | Useful reference for terminal embedding, but replacing our backend with an SSH-focused service adds an unnecessary connection layer. |
| [sshx](https://sshx.io/) | Collaborative terminal workspace with a zoomable canvas | Useful layout inspiration for several sessions; does not by itself solve tmux copy-mode semantics. |

Recommendation: retain xterm.js and the existing backend. Use native terminal mouse
input for mouse-enabled sessions, following ttyd's small input bridge. Keep custom
history control only for the fallback without mouse reporting. Do not install a second
terminal server merely to change the interface.

0.22.1 forwards native wheel input, preserves non-UTF-8 binary mouse bytes, supports
high-resolution trackpad deltas through xterm, and routes touch drags through that same
native wheel path. Keyboard input after a scroll exits tmux copy mode before typing.
Reopening a connection clears any old stuck copy view; no process is interrupted.

The browser frame remains useful isolation for focus/styles. A later UI pass can
reduce controls and give the tree a phone drawer without changing the PTY architecture.
Mac hardware behavior still needs feedback; Chromium wheel/touch fixtures and a
live-updating full-screen application cover the actual protocol regression.

Sources inspected:
- [ttyd input and resize wiring](https://github.com/tsl0922/ttyd/blob/main/html/src/components/terminal/xterm/index.ts)
- [xterm terminal modes](https://xtermjs.org/docs/api/terminal/interfaces/imodes/)
- [tmux copy-mode behavior](https://github.com/tmux/tmux/wiki/Getting-Started#copy-and-paste)

The expanded seven-option comparison and implementation decision are in
[CONSOLE_SIMPLIFICATION.md](CONSOLE_SIMPLIFICATION.md).
