# Agent Console UI review — 2026-10-02 (#90)

Reviewed the parent session `agent-console-ui-revamp-1001` and its latest clean source, `7116f20`, version 0.23.0. Parent's latest inspection proposed viewport allocation, independent pane scrolling and toolbar consolidation. Work is isolated on `fix/session-viewport-20261002`; production is unchanged.

## Findings

| Priority | Finding | Result |
| --- | --- | --- |
| High | Fixed 540px desktop terminal sits below large page padding and wrapping headings. At 1024×600 with a long name/path and 25 children, its bottom was 892.25px; a substantial portion required page scrolling. | Corrected: active desktop sessions fill the remaining viewport; names/paths truncate with full-text titles; tree/details scroll independently. |
| Medium | Heading action container can let buttons extend off the right edge beside long text. | Corrected: fixed action group and shrinking text column; explicit button-bound regression. |
| Medium | Full screen appears both in the workbench and inside its iframe's More menu. | Corrected: embedded view hides the duplicate; containing view owns expansion. Standalone terminal retains its control. |
| Medium | Open terminal is visible even while open, and Stop is duplicated in the heading and terminal toolbar. Parent view actions and native input actions occupy two toolbar rows. | Corrected in 0.23.2: while the terminal is visible the session heading hides Open/Stop so the terminal header is the single lifecycle location, and the heading set is restored on close. Phone outer headers keep one compact row (Sessions + Stop + Close) with 44px targets; the outer status stays in the accessibility tree and describes the Sessions control. |
| Low | Session details contain many action buttons once expanded. | Remains behind the existing collapsed disclosure. A tabbed/details drawer would be a separate UI change. |

## Verification

- Added regressions first; both failed on unchanged source (terminal bottom 892.25px and visible embedded Full screen).
- Full affected Workbench suite: 76 passed, 2 skipped (desktop-only viewport case on phone projects), Chromium at desktop/360px/390px.
- Final heading adjustment checked with focused desktop scenarios: short windows, button bounds, details, scrolling, close/reopen, expand/restore, navigation, pending status and tree drafts.
- Version checks: 3 passed. Corrected pre-existing UI badge mismatch as part of the patch version increment; 0.23.2 continues the patch line for the terminal lifecycle consolidation.
- Consolidated-lifecycle regressions cover duplicate lifecycle actions, close/reopen/switch/stopped sessions, close-then-refresh persistence, stale refresh after Stop, phone row/44px bounds under resized/orientation viewports and a 200% zoom-equivalent desktop viewport. Full affected Workbench suite: 82 passed, 5 skipped (phone-only assertions skipped on desktop and vice versa), Chromium at desktop/360px/390px.
- Screenshot evidence: [before](short-window-before.png), [after](short-window-after.png).

Browser tests use mocked API/WebSocket fixtures and the real frontend/xterm components. No live deployment or physical phone keyboard/trackpad validation was performed. A single consolidated toolbar and broader interaction redesign remain future work; this patch does not certify the whole Console as issue-free.
