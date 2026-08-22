# Agent Console — Agent Working Rules

## Mandatory Startup Protocol

**Before inspecting implementation details, creating a worktree, changing files, or starting an issue, every development agent MUST read [`docs/START_HERE.md`](docs/START_HERE.md).**

Then the agent MUST verify the current GitHub state for the intended work: read the relevant master epic and its latest comments, the exact target issue and its latest comments, and any existing PR/branch/worktree for that issue. Do not begin implementation from stale chat history, an old roadmap entry, or an issue title alone.

Before writing code, establish the target issue, issue status, existing PR/worktree, dependency state, documents read, bounded scope, explicit non-goals, and current deployment state as described in `docs/START_HERE.md`.

If repository documentation, GitHub state, and code disagree, do not guess. Surface/reconcile the discrepancy and retain the narrower safe boundary until it is resolved.

## PR Governance

Every PR targeting `main` MUST:

1. Reference exactly one GitHub issue.
2. Update `docs/DEVELOPMENT_ROADMAP.md` — mark the completed item or add a future item if the PR introduces new scope.
3. Update `docs/RELEASE_NOTES.md` — add an entry under the next unreleased version. Each entry MUST include: date, issue/PR number, impact summary, configuration or migration notes (or "none"), verification steps, and rollback instructions (or "none").
4. Increment the SemVer version in both `pyproject.toml` and `agent_console/__init__.py` according to the scope of changes. **Patch is the default for unclassified changes:**
   - **patch** (e.g. 0.1.0 → 0.1.1): bugfixes, documentation, refactoring with no API change.
   - **minor** (e.g. 0.1.0 → 0.2.0): new feature, API addition, deprecation.
   - **major** (e.g. 1.0.0 → 2.0.0): breaking API change.
5. Work in an **isolated worktree** — never commit directly to `main` or a shared branch.
6. Include or update relevant tests.
7. Contain no secrets, tokens, passwords, or credentials.
8. Receive **human approval** before merge, release, or deployment.

## Workflow

- Read `docs/START_HERE.md` and verify the live tracker state before starting work.
- Create an issue before starting work.
- Create a branch and worktree from the base commit.
- Implement, test, commit.
- Open a PR for review.
- Leave the end-of-session handoff described in `docs/START_HERE.md` for meaningful development sessions.
- Do not merge, deploy, or release without explicit human authorization.
