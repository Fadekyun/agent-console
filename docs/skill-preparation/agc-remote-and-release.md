# Prepared reference for existing agc-ops and agc-github-ops

Delivery state: prepared content for review in the authoritative skills workspace;
not installed, assigned, or executed. Keep the AGC source/deployment owner in
control. This Console change does not deploy AGC or change its migrations.

## Bounded remote orientation

Use the existing trusted SSH aliases and host-local credentials; do not copy
credentials into Console. The existing skills define lxc-106 as the source/runtime
host and n100 as the authenticated GitHub host. Read current AGC instructions and
its source revision first. A reusable wrapper may offer a fixed allowlist of
read-only commands (source revision/status, unit health, bounded service output),
with a timeout and clear host labeling. Never accept a generic shell string from
an unattended request, and do not hide database writes or deployment in a helper
named inspect. No such mutation wrapper is included or enabled by this reference.

## Exact-release evidence recipe

1. Record the candidate SHA, source worktree, target service/site and existing
   deployed SHA. Preserve manual/untracked files.
2. Query the actual protected branch and PR review/check results through the
   existing n100 GitHub credentials. Chat approval is authority but is not a
   fabricated platform approving review. No admin/force/alternate-identity bypass.
3. Coordinate the one heavy validation lane. Run the current project's approved
   isolated validation, including real database migration fixtures when applicable.
   Do not use a passing source build as proof of browser login or data migration.
4. Before promotion, identify the supported atomic deployment and rollback path
   from current AGC instructions. Record whether this candidate changes schema,
   worker behavior, payments or stock. Keep unrelated deferred work out of release.
5. Promote only after actual release authority and gates pass. Verify exact
   deployed SHA, health and the relevant behavior; retain the prior release and
   required recovery evidence. Do not use this generic recipe as a deployment
   command or assume old branch names are immutable candidates.
6. Produce a bounded handoff: candidate/deployed SHA, commands/results, remaining
   findings, rollback handle, lane released and current explicit session name.

The concrete next wrapper implementation belongs in the existing AGC ops skill
under its owner after its allowlist is reviewed. This preparation avoids adding a
second competing AGC release engine to the Console repository.
