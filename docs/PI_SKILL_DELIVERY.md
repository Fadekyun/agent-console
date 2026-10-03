# Pi skill delivery — staging 0.18

Pi now participates in the capability-driven skill catalog, global sync and session selection. Version **0.99.2** is verified for the native discovery mechanism. Global sync uses `.pi/agent/skills`; a Console session links its private `PI_CODING_AGENT_DIR/skills` to its immutable copied selection. This fixes the previous gap where the per-session directory was configured but no skills were delivered into it.

Assigned and shared skills follow the same policy/hash checks. Restart uses the same selected-snapshot path and explicitly refreshes its copied packages. An existing unrelated directory or conflicting link is preserved and reported as an error. Pi's per-session placement prevents Console-assigned packages from leaking into the global root; it does not disable native project, ancestor, settings, package or plugin sources. The static inventory includes native and `.agents` roots and explicitly reports that configured sources and precedence are not exhaustively verified. Native per-skill permission support is not claimed.

## Version admission

For adapters with an exact-version contract, selected-skill delivery now checks the installed version before session creation or explicit restart, as well as before global sync. This applies to Pi 0.99.2 and OpenCode 1.18.30. Missing/unverified versions fail with an explanation and require verification before extending the capability table. A session with no Console-selected skills is not blocked by this skill-delivery check. Other adapters retain their existing compatibility policy; this change does not certify their full version matrix.

## Evidence — 2026-10-02

- The installed Pi 0.99.2 `DefaultResourceLoader.reload()` and `getSkills()` discovered all four new portable guide packages from an isolated agent directory with no diagnostics. The probe used an empty project/home and disabled extensions; no model request or credential was used.
- The installed Hermes 0.21.4 native `skills_list()` discovered the same four packages via `skills.external_dirs` in an isolated home. This is native discovery evidence; Hermes per-profile isolation remains conservatively unsupported in Console pending the rest of its capability audit.
- Real-tmux tests with inert harness launchers verify Pi assigned/shared delivery, unchanged running copies after canonical edits, explicit restart refresh, durable receipt and pre-creation denial for an unverified version. Unit tests verify conflicts are preserved, sync skips unverified versions and selected-skill admission rejects both unverified Pi and OpenCode.
- Staging's Pi launcher/account is still setup-required. These checks verify discovery and Console integration separately; they do not claim an authenticated Pi model task ran on staging.

No schema migration or credentials are required. Back up the normal skill policy/library/session state. Rollback to 0.17.3 preserves receipt data but loses Pi skill selection/delivery and the new pre-launch version check. Unassign Pi-dependent role selections before launching through an older release; current-console operation remains independent.
