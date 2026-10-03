# Native skill delivery matrix — 2026-10-02

Each row was checked with a real native reader and clean temporary configuration.
All seven current Console guides were present, including maintenance revision
2026-10-02.3. This verifies discovery and selected content availability, not model
use, provider authentication or a universal skill sandbox.

| Harness | Verified version | Native evidence / delivery |
| --- | --- | --- |
| Codex / Codex Pro | 0.159.2 | App-server `skills/list` sees the copied selection through `CODEX_HOME/skills`; user `.agents` and repository `.agents`/`.codex` fixtures remain visible. Both adapters use this same mechanism. |
| Claude | 2.1.287 | Native initialization lists the seven guides through the actual Console shell launcher and `--add-dir` containing `.claude/skills`; user and project fixtures remain visible. No account token or model turn. |
| OpenCode | 1.18.30, 1.18.31 | Existing 1.18.30 discovery contract retained; 1.18.31 native `debug skill --pure` verifies all seven guides via the selected `skills` configuration path plus six global/project fixtures. Duplicate precedence remains unverified. |
| Pi | 0.99.2 | `DefaultResourceLoader.reload/getSkills` loads the seven copied guides through the Console-created private agent-directory link, with no diagnostics. |
| Hermes | 0.21.4 | Native `skills_list` loads the seven copied guides through Console-written `skills.external_dirs`. |

## Repeatable probes

Run with the Console Python and explicitly selected installed runtimes:

- `scripts/verify-codex-skills.py CODEX_BINARY --guides GUIDE_ROOT`
- `scripts/verify-claude-skills.py CLAUDE_BINARY --guides GUIDE_ROOT`
- `scripts/smoke-opencode-skills.sh OPENCODE_BINARY GUIDE_ROOT`
- `scripts/verify-pi-hermes-skills.py --guides GUIDE_ROOT --pi-root PI_PACKAGE --node NODE --hermes-root HERMES_CHECKOUT --hermes-python HERMES_PYTHON`

The scripts use disposable homes/projects. They do not install providers or
authenticate. Claude was tested using the official temporary npm platform package
`@anthropic-ai/claude-code-linux-x64@2.1.287`, downloaded without install scripts;
the disabled host launcher remains disabled. Package integrity was recorded in
the local evidence. No current-console files or credentials were changed.
Pi/Hermes MCP parity is separately verified in [native MCP checks](NATIVE_MCP_VERIFICATION.md).

## Actual fixes and limits

Claude previously received `CLAUDE_HOME`, which its native loader did not use.
The Console now adds a dedicated directory containing only `.claude/skills` and
the selected copied packages. It leaves native account/configuration locations
alone. A quoted environment reference in the shell launcher follows session
renames. Explicit restart upgrades old launchers once and refreshes the snapshot;
library edits do not alter already-running copies. Real-tmux regression tests
cover the copy, old launcher upgrade and repeated restart.

The capability table records tested versions and project/global source support.
Codex/Claude/Hermes retain their existing permissive compatibility policy:
unrecorded versions are visibly **unverified**, not silently certified. Pi and
OpenCode keep exact-version admission for selected skill delivery and sync.
Missing binaries remain unavailable. This release does not install or enable
missing staging provider accounts.

Static discovery inventories include known global/project roots but do not
exhaustively inspect ancestor, admin, configured, plugin or package sources.
The former `configured_sources_inspected=True` claims were removed. Console
selection policy controls its own packages; broader native discovery can remain
active. Native per-skill permission support is a capability, not a promise that
Console rewrites every provider's permission configuration. Profile assignment
guards remain for providers without the supported assignment-isolation contract.

The native observations agree with the documented Codex
[skill listing protocol](https://learn.chatgpt.com/docs/app-server) and
[discovery roots](https://learn.chatgpt.com/docs/build-skills), and Claude's
[additional-directory discovery](https://code.claude.com/docs/en/skills#load-skills-from-a-directory-outside-the-project).
Installed protocol schemas, rather than newer optional documentation fields,
are used by the pinned probes.

## Operator surfaces

`agentctl skills validate NAME` validates current package content without starting
session infrastructure and returns nonzero for invalid/missing content. Use
`--package` if NAME is also a profile. `validate-profile PROFILE` is unambiguous;
legacy `validate PROFILE` remains compatible. `unassign NAME --profile PROFILE`
and `remove NAME --profile PROFILE` remove the assignment, preserving the library
and historical session copies.

Skills → Inspect → Check harness delivery exposes native sync paths, version
state, collisions and discovery uncertainty. It distinguishes global sync from
session Configuration's frozen receipt. Inspect, approve/revoke, assign/remove,
stage/activate, doctor and sync share the existing policy engine; no hand-edited
harness configuration or automatic script installation is needed.
