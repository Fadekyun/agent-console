# Release Notes

All release-note entries include: date, issue/PR, impact, configuration/migration, verification, and rollback guidance.

## 0.1.1 (Unreleased)

- **Date**: 2026-07-28
- **Issue/PR**: [#40](https://github.com/Fadekyun/agent-console/issues/40) / [#41](https://github.com/Fadekyun/agent-console/pull/41)
- **Impact**: Documentation and governance only. No runtime behavior changes.
- **Configuration/Migration**: None.
- **Verification**: `python -m pytest tests/test_version.py` — 2/2 pass. Full suite 518/527 pass, 9 pre-existing failures (unrelated).
- **Rollback**: Revert the commit. No data or config changes persist.

Changes:
- AGENTS.md with PR governance rules.
- DEVELOPMENT_ROADMAP.md for feature tracking.
- RELEASE_NOTES.md for changelog.
- Version consensus test.
- Bump 0.1.0 → 0.1.1.
- README links to roadmap, release notes, and AGENTS.md.

## 0.1.0

- **Date**: (Unreleased — no GitHub tag or release published)
- **Impact**: Initial foundation.
- **Configuration/Migration**: See scripts/install.sh and docs/ for setup.
- **Verification**: `agentctl doctor`

Initial capabilities: multi-tool orchestration, web terminal, session delegation, auth contexts, plan management, audit logging, SSH client installer, canary/staging deployment mode.
