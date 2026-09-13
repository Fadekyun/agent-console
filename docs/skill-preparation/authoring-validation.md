# Prepared reference for existing agent-console-ops: skill validation

Delivery state: prepared source reference/helper, not synced to live skill roots.
The authoritative shared skills workspace remains /home/fadekyun/codex/skills on
n100; /home/agentstage/codex/skills is its staging mirror, and session overlays are
generated delivery copies. Do not edit an overlay to "fix" canonical content.

Before proposing an update to an existing skill, run the reviewed helper against
an isolated candidate copy:

```sh
python3 -B scripts/validate-skill-content.py --root /path/to/candidate-skills
```

This calls the existing catalogue validator only. It does not construct a writer
manager, sync links, assign profiles, probe providers or change approval state.
Unknown tools/profiles/kinds and containment-invalid canonical files are errors.
The supported optional metadata fields default normally: absent tools/allowed
profiles/kind do not automatically make a skill invalid or unusable. "v2 metadata"
is not an established mandatory schema in this candidate.

Then inspect native IDs/collisions with the appropriate reviewed catalogue/doctor
path in an isolated fixture. Catalogue discovery is not assignment, and assignment
is not installed delivery or verified per-session isolation. OpenCode global
support remains separate from the unsupported per-session assignment boundary.
A future CI job can invoke this same helper on tracked skill sources; no credential
or provider-dependent CI workflow is enabled by this change.
