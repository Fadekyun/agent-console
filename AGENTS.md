# Agent Console — Agent Working Rules

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

- Create an issue before starting work.
- Create a branch and worktree from the base commit.
- Implement, test, commit.
- Open a PR for review.
- Do not merge, deploy, or release without explicit human authorization.
