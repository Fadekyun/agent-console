# Skill registry and session delivery

Staging version 0.11.0 adds a content-aware registry for the Console-selected skill library. The registry governs future session delivery; it does not claim to sandbox a skill script or suppress every skill supplied by a harness, repository or plugin.

## Package format

Keep portable `name` and `description` in YAML frontmatter. Put Console policy under namespaced metadata, or in `agent-console.json` beside `SKILL.md`. Declare a field in only one location.

```yaml
---
name: bounded-coding
description: Implement a bounded change and check the resulting behavior.
metadata:
  agent-console/version: "1"
  agent-console/compatible_harnesses: [codex, codex-pro]
  agent-console/compatible_profiles: [coder, bugfix]
  agent-console/risk: low
  agent-console/approval: allow
---
Read the task and repository instructions before making changes.
```

The sidecar uses unprefixed keys and requires `version: 1` unless the version is in frontmatter. Supported fields are `source`, `revision`, `scope` (`global` or `project`), `project` (absolute root), `compatible_harnesses`, `compatible_profiles`, `risk` (`low` or `elevated`), `approval` (`allow`, `ask`, `deny`), `required_binaries`, `required_services`, and `scripts`. List fields accept YAML/JSON lists. A source URL cannot include credentials, query parameters or fragments. Required services are operator-verified per revision; Console never starts or installs them. Binary checks only inspect availability on PATH.

Legacy `tools`, `allowed_profiles`, `kind` and `requires_approval` remain readable with migration diagnostics. Duplicate YAML keys and duplicate namespaced/sidecar fields are rejected. Packages must contain real regular files, with no symlinks, and fit the bounded file/count/size limits. Imports inspect potential credentials and executable declarations without executing anything. Secret scanning is a heuristic, not a guarantee of safe third-party content.

## Inspect and approve

The workbench Skills page offers inspect, stage import, review/activate, assign/remove, approval/revocation, discovery diagnostics and sync. Import paths in the web UI must be inside the configured workspace. Staged imports remain outside the canonical library until the operator activates the exact inspected content hash. Existing skill folders are never overwritten by activation. Inspect the staged files at the displayed path before approving third-party instructions.

CLI equivalents:

```sh
agentctl skills list
agentctl skills inspect bounded-coding
agentctl skills import /workspace/incoming/bounded-coding
agentctl skills import https://github.com/OWNER/REPOSITORY.git --revision main --subdirectory skills/bounded-coding
agentctl skills imports
agentctl skills inspect-import import-ID
agentctl skills activate import-ID --hash HASH
agentctl skills review bounded-coding --decision reviewed --hash HASH
agentctl skills assign bounded-coding --profile coder
agentctl skills allow bounded-coding --profile coder --hash HASH
agentctl skills disallow bounded-coding --profile coder
agentctl skills remove bounded-coding --profile coder
agentctl skills preview --profile coder --tool codex --repository /workspace/repo
agentctl skills delivery SESSION
```

`--services-verified` is an explicit assertion on review/activation when service dependencies exist. It is not a probe. `allow` binds an ask-policy approval to a role and content hash. `deny`, blocked/unreviewed trust, incompatible scope/role/harness, invalid packages and missing dependencies remain denied even with an approval receipt. Changing supporting files or executable bits changes the content hash as well as editing `SKILL.md`.

Since staging 0.19, anonymous HTTPS Git imports record the resolved commit and original content hash separately from package-declared metadata. Fetching/extraction is bounded and inert; activation remains a separate inspected-hash decision. Local edits retain their origin without claiming to match the fetched bytes. See [Git import behavior and evidence](SKILL_GIT_IMPORTS.md).

Global sync only publishes unrestricted, low-risk, globally scoped, allowed skills. Restricted packages are session-only; sync removes only Console-owned links for them and preserves unrelated native content. Harness/version gates still apply. Per-profile assignments remain rejected by launch for tools that cannot isolate them; shared skills use each adapter's supported delivery path. Import, review and assignment do not execute skill scripts.

Since staging 0.18, Pi 0.99.2 has verified native delivery through its private agent directory and participates in the capability catalog. Exact-version adapters (Pi and OpenCode) also enforce their version gate before selected-skill session creation/restart. See [Pi delivery and native discovery evidence](PI_SKILL_DELIVERY.md), including the limits of per-session placement and the remaining provider matrix.

## Exact delivery and lifecycle

Create and explicit live-terminal restart copy selected packages into the session's isolated directory. Files are checked again while copying without following symlinks. Editing the library does not alter an existing session copy. A delivery receipt records content hashes, revision, selection reason, tool and role; it persists under `state/skill-deliveries/SESSION_ID/` after the transient directory is cleaned up. The session Skills view shows this receipt. Sessions created before this release report unknown delivery instead of inferring it from current assignments. A receipt shows delivery, not proof that the model used a skill.

A stopped terminal cannot use `restart-agent`; create a new session to resume its work. Live restart is the explicit refresh boundary and resolves today's assignments and approvals. This release preserves old delivery history and does not silently refresh running sessions.

## Migration and rollback

Install the pinned PyYAML dependency with the package. No SQLite schema change is needed. Policy is in the owner-only, locked, atomically replaced `skill-policy.json` beside the database; imports are under `skill-imports/` there. Back up these files together with the library and session database. Old unbound superpower approvals need a content-bound approval before their next launch. Existing sessions remain intact.

Keep canonical reusable guides in the authoritative skills workspace on n100; generated per-session copies are not the editing source. The remaining cross-harness verification and guide migration are tracked in [FULL_REVAMP_ACCEPTANCE.md](FULL_REVAMP_ACCEPTANCE.md).

To roll staging back, select its prior runtime release and keep the state and library backup. An older release does not enforce this new registry policy: do not launch newly imported or restricted skills through it until the operator has reconciled the library. Returning to the separate current-console URL does not touch staging data or policy.
