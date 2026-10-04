# Pi skill delivery

Pi now participates in the capability-driven skill catalog, global sync and session selection. Versions **0.99.2** and **1.0.2** are verified for the native discovery mechanism. Global sync uses `.pi/agent/skills`; a Console session links its private `PI_CODING_AGENT_DIR/skills` to its immutable copied selection. This fixes the previous gap where the per-session directory was configured but no skills were delivered into it.

Assigned and shared skills follow the same policy/hash checks. Restart uses the same selected-snapshot path and explicitly refreshes its copied packages. An existing unrelated directory or conflicting link is preserved and reported as an error. Pi's per-session placement prevents Console-assigned packages from leaking into the global root; it does not disable native project, ancestor, settings, package or plugin sources. The static inventory includes native and `.agents` roots and explicitly reports that configured sources and precedence are not exhaustively verified. Native per-skill permission support is not claimed.

## Version admission

For adapters with an exact-version contract, selected-skill delivery now checks the installed version before session creation or explicit restart, as well as before global sync. This applies to the explicitly verified Pi and OpenCode versions in the capability table. Missing/unverified versions fail with an explanation and require verification before extending the capability table. A session with no Console-selected skills is not blocked by this skill-delivery check. Other adapters retain their existing compatibility policy; this change does not certify their full version matrix.

## Evidence — 2026-10-02

- The installed Pi 0.99.2 `DefaultResourceLoader.reload()` and `getSkills()` discovered all four new portable guide packages from an isolated agent directory with no diagnostics. The probe used an empty project/home and disabled extensions; no model request or credential was used.
- The installed Hermes 0.21.4 native `skills_list()` discovered the same four packages via `skills.external_dirs` in an isolated home. This is native discovery evidence; Hermes per-profile isolation remains conservatively unsupported in Console pending the rest of its capability audit.
- Real-tmux tests with inert harness launchers verify Pi assigned/shared delivery, unchanged running copies after canonical edits, explicit restart refresh, durable receipt and pre-creation denial for an unverified version. Unit tests verify conflicts are preserved, sync skips unverified versions and selected-skill admission rejects both unverified Pi and OpenCode.
- Staging's Pi launcher/account is still setup-required. These checks verify discovery and Console integration separately; they do not claim an authenticated Pi model task ran on staging.

No schema migration or credentials are required. Back up the normal skill policy/library/session state. Rollback to 0.17.3 preserves receipt data but loses Pi skill selection/delivery and the new pre-launch version check. Unassign Pi-dependent role selections before launching through an older release; current-console operation remains independent.

## Pi 1.0.2 verification and profile limits — 2026-10-04 (#145)

The installed 1.0.2 wrapper reports the correct version with the pinned Node22 runtime. An owned live Pi orchestrator creation on v0.28.10 returned HTTP400 before creating a session: the version was not verified, and two assigned legacy skills (`orchestrator-spawn`, `report-to-orchestrator`) explicitly excluded Pi. General Pi creation also failed its shared-skill version check.

The 1.0.2 native loader was verified separately with disposable home, project and selected-snapshot roots, no credentials, extensions or model request. Reproduce that evidence with:

```sh
python scripts/verify-pi-skills.py --pi-root /path/to/pi-coding-agent --node /path/to/node22 --expected-version 1.0.2
```

Both the low-level skills reader and `DefaultResourceLoader` must discover exactly the selected fixture, resolve its file into the copied snapshot, read its relative support file and omit the unselected library fixture. Empty in-memory settings prevent package configuration from entering the probe. This verifies selected-snapshot delivery, not suppression of every native source or a successful model response.

Version admission does not widen skill permissions or remove assignments. The live orchestrator profile remains blocked by its incompatible legacy assignments until a separately authorized policy change. Unknown versions and prereleases remain denied; errors identify installed and verified versions. Current sessions and skill permissions are untouched.
