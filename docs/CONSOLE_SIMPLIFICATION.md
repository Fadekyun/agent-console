# Console simplification — 2026-10-02 (#90)

## Architecture decision

Use one stack: xterm.js → the existing authenticated WebSocket/PTY bridge → tmux →
the selected CLI harness. Agent Console owns session identity, tree relationships,
skills and optional scheduling. No additional terminal service is installed.

The alternatives were evaluated as complete products, not a shopping list of parts:

| Option | What it provides | Decision for this console |
| --- | --- | --- |
| [xterm.js](https://github.com/xtermjs/xterm.js) + current bridge | Embeddable renderer with the existing managed-session backend | Keep. Preserves identities, account boundaries, drafts and tree awareness. |
| [ttyd](https://github.com/tsl0922/ttyd) | Command-to-browser terminal service | Strong replacement candidate for a standalone terminal. Here it would duplicate transport and require reimplementing lifecycle/auth integration. Use its documented protocol behavior as a reference, not another service. |
| [WeTTY](https://github.com/butlerx/wetty) | Browser terminal over SSH/login | Better fit for remote host login than Console-owned local sessions. Adds a login/transport layer here. |
| [GoTTY](https://github.com/sorenisanerd/gotty) | Exposes command-line applications as web applications | Small service, but still needs Console lifecycle and authorization integration. No demonstrated fix beyond the renderer/bridge already present. |
| [sshx](https://sshx.io/) | Shared terminals on a collaborative canvas | Useful for people working together. Spatial placement is not parent/child orchestration, and a canvas adds navigation cost on phones. |
| [Wave Terminal](https://github.com/wavetermdev/waveterm) | Desktop terminal workspace with blocks, previews and durable SSH | Attractive complete desktop alternative; does not meet browser-only phone access as a drop-in replacement. |
| [code-server](https://github.com/coder/code-server) | VS Code hosted in the browser | Appropriate when the main task is editing code in an IDE. Too much additional interface/service scope for harness orchestration. |

These are source/documentation comparisons, not performance benchmarks or deployments.
Changing products would not remove tmux copy-mode semantics. The confirmed wheel defect
was fixed in 0.22.1 by respecting native mouse reporting; keep that regression coverage.

## Space audit and implementation

- Always-visible input occupied a textarea and three action buttons, particularly costly
  on phones. **Input** now opens the optional composer. **Input · draft** signals unsent
  text, including a stored task. Paste, Send and Send + Enter appear only inside it.
  Explicit brief/peer insertion opens it; a failed direct send exposes the fallback.
  Transport failures still need reconnection; the composer does not bypass that.
- The key strip and interaction modes competed with output. A compact toolbar keeps
  Input, Copy / Read and More. More overlays the terminal without changing its size.
  Reconnect appears only when retry is available. Direct input is the default on phones
  and desktops; explicit Scroll/Select remain available under More.
- The sidebar consumed 280px even when unused. Its Sessions disclosure can collapse
  and reclaim width. Tree branches collapse independently; navigating to a descendant
  reveals its ancestors. A Sessions dialog exposes the same tree inside full-screen
  and mobile terminal views, including Add session at any layer.
- Attention editing is secondary to work; it now has its own disclosure within session
  details. Settings diagnostics/accounts/advanced controls also collapse. Readiness
  warnings expand automatically so failures stay discoverable.
- Existing Results, scheduled dependencies, handoffs and release controls remain under
  their explicit views. Normal Add session still opens the harness picker and creates
  a direct child. Tree membership does not require automatic reviews or model calls.

## Interaction contracts

Closing Input preserves its draft. Closing the terminal disconnects only its view.
Stopping a session ends its process and retains recent output. Switching tree sessions
keeps only the visible terminal attached; browser drafts survive iframe eviction.
Related sessions discover each other through the existing relative-session CLI; no new
claim of shared model memory or automatic delegation is made.

## Validation and rollout

Browser tests cover collapsed input and branches, draft retention, expanded terminal
navigation, direct typing/native wheel routing, optional scheduling, stop and connection
lifecycle. Test desktop and 360px/390px Chromium viewports. Physical Mac trackpad and
phone keyboard behavior remain hardware acceptance checks.

Deploy to the separate staging instance first, keeping the current instance and its
rollback release available. This change has no database, session or credential migration.


## Recorded verification

- 69 Workbench cases passed across desktop/360px/390px; 12 final targeted checks
  passed after the last small navigation/fallback adjustments (overlapping coverage).
- 40 shared-terminal/legacy cases passed; 11 platform/live-only cases were intentionally
  skipped. 19 backend Workbench launch/state tests passed in isolated state.
- Staging source `c5aa9126f6b5b188497235c58fbfdcfea7363071` was exercised using two owned
  shell sessions and a mouse-aware full-screen fixture. Native wheel reached the app,
  visible ticks continued, direct typing worked, 360/390px tree switching preserved the
  draft, and Stop ended only the selected child. Both probes were stopped afterward.
- Opening/closing the composer changed the live desktop output height by 65px.
  Main remains on 0.22.1; staging is 0.23.0. No model requests or account changes.
