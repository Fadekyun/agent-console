# Skill implementation and prepared-delivery audit

Integration issue96, 2026-09-13. The existing OpenCode findings peer supplied its
exact list on request. We compared it with the combined candidate, the staging
skills content and delivery-link metadata, and the authoritative n100 skill names
and hashes. Runtime discovery, engine support, prepared content, installed links
and profile assignment are distinct evidence states.

## Engine versus installed delivery

- **Implemented in this candidate:** shared provider skill capability descriptor,
  OpenCode1.18.30 native global root/sync/catalogue/doctor, exact-version refusal,
  collision/shadow diagnostics, unrelated-file preservation, containment,
  existing assignment/superpower gates and the guarded session/profile routes.
  Tests: `test_skill_capabilities.py`, `test_skills.py`, `test_skills_secrets.py`,
  `test_core.py`, `test_cli_inspection.py`, `test_inspection.py`.
- **Not claimed:** OpenCode per-session assigned-profile isolation, deterministic
  duplicate precedence, native planning containment, MCP platform integration,
  automatic task replay or blanket profile assignment. Unsupported boundaries
  continue to fail closed; nothing in this audit enables them.
- **Actual local content:**27 skill directories have SKILL.md content. Exact links
  to those staging directories currently exist for27 Codex,26 Claude and25 Hermes
  roots; zero exact mirror links were found at the native OpenCode root. These
  counts describe links, not provider discovery or policy correctness. OpenCode
  can also discover legacy roots, so zero native links does not mean zero visible
  skills. No live sync/assignment was performed.
- **Metadata correction:**25 files omit the optional tools/kind/profile fields.
  The parser supplies defaults; that is not evidence they lack a mandatory "v2"
  format or are unusable. The two explicitly annotated skills are
  orchestrator-spawn and report-to-orchestrator. Tool restrictions explain some
  root differences and must not be removed to equalize counts.
- **Assignment evidence:** this audit did not read live assignment rows. The
  guarded current-DB probe previously refused absent WAL/SHM prerequisites. We do
  not infer unassigned status from discovery or absent native links, and do not
  use a writer-initializing CLI or unguarded SQLite fallback merely for a count.

## Exact requested learnable/missing items

| Peer proposal | Existing content/engine | Bounded preparation in this candidate | Still not implemented/installed |
|---|---|---|---|
| agc-remote | agc-ops already defines SSH host, repository/service/DB orientation | `skill-preparation/agc-remote-and-release.md` fixes the read-only wrapper boundary and host/authority split | Executable generic remote/DB/test/deploy wrapper; requires the AGC owner's concrete allowlist and review |
| agc-release-verify | agc-ops has deployment notes; agc-github-ops has authenticated GitHub routing | Same reference gives an exact-SHA/check/health/schema/rollback/lane handoff recipe | No parallel AGC deployment engine or automatic CI watch; install reference in existing skills after canonical review |
| session-takeover | Existing inspect/context/review, launchers, reports and lifecycle commands | `skill-preparation/session-recovery-and-digest.md` gives stable-ID/name recovery and explicit stopped/integration handling | Automatic dead/exhausted-session recreation or replay; remains a separate lifecycle feature |
| session-digest | Guarded inspect already emits structured metadata | `scripts/session-digest.py` produces bounded JSON from existing inspect output, excludes free text/payloads and disclaims acknowledgement | Not a new DB/API namespace or automatic delivery pipeline; source helper awaits installation |
| skill-authoring validator | `validate_catalog` already checks canonical metadata/containment | `scripts/validate-skill-content.py` packages that read-only check; reference documents defaults and review steps | No mandatory invented metadata migration, provider-dependent CI or automatic sync; canonical delivery remains review-gated |

Delivery payloads are real reference files and helper scripts, with targets and
state recorded in `skill-preparation/delivery.json`. They extend existing skills;
no duplicate skill was created or installed in a generated overlay. The
canonical location remains `/home/fadekyun/codex/skills` on n100; the local path is
a staging mirror. Add links from the canonical existing SKILL.md files and deploy
these reviewed references/helpers via the existing sync process only at the
separate authorized delivery step. Source preparation is not a live installation.

## Existing skills checked individually

These27 names were present in the peer list and staging content inventory:

- agc-directus-staff
- agc-github-ops
- agc-ops
- agc-product-stocking
- agc-stock
- agc-zoho-mail
- agent-console-ops
- bushi-account-risk-management
- cloudflare-dns
- filehub-drop
- frontend-ui-builder
- ha-haos-vm
- implement-plan
- llocg-simulator
- model-agnostic-ai-worker
- n100-hardening-final-check
- n100-screenshot-render
- nocode-hermes-orchestration
- orchestrator-spawn
- points-portal-ops
- report-to-orchestrator
- samba-file-retrieval
- spaceship-dns
- store-kiosk-ops
- tailscale-router
- yyt-adhoc-sync
- zoho-mail-migration

The authoritative n100 inventory additionally contains **bushi-manual-event-apply**,
which is absent from the staging mirror. This is an actual content-delivery gap,
not a missing Console engine or evidence to automate Bushi submissions. It needs
an explicit canonical-to-mirror delivery review through the existing skill sync
workflow; its operational policy must be preserved. No unrelated Bushi source is
bundled into this Console candidate.

## Corrections and genuine deferrals

The peer's runtime source was the old deployed602248b release. Its report that
OpenCode was missing from sync is correct for that runtime but is fixed in this
candidate. Claims about Claude environment mismatch or per-session isolation are
not accepted without provider-specific proof. Existing context packs, launch
recipes, usage/budgets, bounded lifecycle/replay and MCP registry proposals remain
their existing issues; they are not mislabeled as prepared skills here.

Native planning remains disabled/unverified even after skill discovery tests pass.
Exact integrated tests and independent implementation review are still required
before publication, and installed delivery requires a separate recorded step.
