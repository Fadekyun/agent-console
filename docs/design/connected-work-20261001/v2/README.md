# B revision 2: less on screen, only the sessions you need

The operator selected B. This revision simplifies phone use and makes the four-step chain optional.

Open **[index.html](index.html)** locally, or download `prototype-v2.zip`, extract and open its `index.html`. Everything is fictional and offline. The original concepts remain in the parent directory and their pinned GitHub commit.

## Try it

- **Work → Fix the search hint:** one session. It can implement and check the task itself. **More → Demo: finish** completes it without creating reviews.
- **Work → Rework catalog import:** one session plus two *suggested* follow-ups, each with a reason. Neither starts automatically.
- **Add session below** any node: enter a task, role and readiness choice (final result, ready checkpoint or alongside).
- **Rules:** compare suggestions-only and optional automatic expansion within an explicit scope/limit. This demonstrates settings, not an implemented scheduler.
- **Phone:** Work → Session → Open terminal. Terminal is its own full-screen view; Back returns to Session. Details and Skills are opened separately.
- **Terminal:** scroll into history, press **Demo output**, then **Latest output**. Drafts and reading position are retained when expanding and returning. The fixture uses a native scrolling element, not production xterm.
- **Settings:** inspect effective skills, why they apply and a stale guide that needs repair. This shows the proposed model; it does not modify the real skill library.

## Frames

| Frame | Purpose |
|---|---|
| [B2-01 desktop Work](frames/B2-01-desktop-work.png) | Accepted B direction, small and larger tasks |
| [B2-02 desktop tree](frames/B2-02-desktop-tree.png) | One root, optional suggestions, Add session below |
| [B2-03 phone Work](frames/B2-03-phone-work.png) | Attention + task cards; no nested terminal |
| [B2-04 phone Session](frames/B2-04-phone-session.png) | Status + Open terminal + compact session list |
| [B2-05 phone Terminal](frames/B2-05-phone-terminal.png) | One viewport and one output scroll area |
| [B2-06 Skills](frames/B2-06-skills.png) | Effective per-session access and delivery explanation |
| [B2-07 added session](frames/B2-07-added-session.png) | A manually added child waiting for its chosen input |

## Unified scope

[revamp.md](revamp.md) links #90/#80, #140/#86, terminal reliability, results/inbox, skill engine and guide rewrites, project context, harness/MCP parity and rollout into one ordered program. It includes the one-session fast path, manual addition, optional bounded growth, real terminal acceptance matrix, and the skill content rewrite scope.

Suggestions-only is the provisional default until the operator chooses the expansion policy. Larger tasks may use an explicit auto-growth envelope; a new goal, repository, permission or release target cannot be silently added. Required checks remain, but extra agent layers are not a default.

## Verification and limits

[verification.json](verification.json) records fixture checks. Run with an installed Playwright and Chromium:

```sh
PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium node verify.cjs
```

These checks exercise real wheel and touch gestures against the **native scroll fixture**. They do not establish a deployed xterm fix, clipboard correctness on an actual phone, scheduler durability, or real harness skill delivery. Those are explicit gates in the unified plan. Source edits are confined to design assets and documents.
