# Workbench staging verification — 2026-10-02

Versions 0.15.0–0.15.1 address the overview and terminal portions of #90. The full #90/#140/#6 goal remains open; see `FULL_REVAMP_ACCEPTANCE.md`.

## Connected overview

Live staging Chromium at 1280, 360 and 390 pixels verified connected ownership, collapsed logical attempts, child completion, structured results, session history and horizontal fit. Existing native result `result-a352d3c829574437b43f8c5078bb7eac` remains attributed to its one logical step. The attached target `sess-84ec2f47a34b4d0790e58619503295f3` appears under its connected owner, although it was originally launched independently. Readiness warnings accurately identify unavailable staging harness launchers.

API/unit coverage verifies distinct mechanical/attention/result states, priority ordering, historical bindings, acknowledged failures, pending logical parents and bounded history. Browser journeys cover saved secondary filters, empty state, focus and terminal-frame preservation.

## Native terminal matrix

Two disposable staging Shell sessions exercise real tmux PTYs, real WebSockets and xterm. They are stopped after each check; results do not rely on mocked terminal output. Desktop 1280 and emulated phone 390 cover:

- Embedded desktop and full-screen phone layouts; desktop expand/restore.
- Desktop wheel and synthetic phone touch gestures through real server scrollback; the outer page stays put.
- Timed program output while reading history; the view stays in history until Latest output is selected.
- Text selection and denied-clipboard manual fallback, with pasted text retained as a draft until explicit Send.
- Two independent terminal frames and composer drafts across session navigation.
- Viewport resize, phone landscape dimensions and reduced viewport height representing an open keyboard; composer remains visible.
- Explicit standalone detach/reconnect with the same native session and retained output.
- A real alternate-screen program, Page up/down through Text View, visible-screen capture and return to normal output.

The matrix found two bugs: pointer focus cleared Copy selection, and a pending capture could reopen Text View after dismissal. Version 0.15.1 fixes both. Regression browser tests cover desktop/360/390, false/throwing denied copy APIs, native Chromium clipboard permission on secure localhost, draft-only paste and dismissal during delayed capture.

Evidence scripts, logs, screenshots and fixture identities are retained in the task workspace at `handoffs/console-overview-20261002/`. The initial candidate matrix substituted only the local terminal JavaScript against the real staging backend; the selected staging release is verified again after publication.

## Limits

Phone touch, orientation and keyboard behavior above are browser emulation, not physical iOS/Samsung certification. tmux history is shared by clients attached to the same pane; this is documented behavior, while different sessions keep independent terminals/drafts. Clipboard permission success uses Chromium's secure localhost origin; LAN HTTP exercises the manual path. No provider authentication or device behavior beyond these checks is implied.
