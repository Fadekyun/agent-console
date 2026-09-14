# Pi and Hermes through CommandCode

The CT115 default is `deepseek/deepseek-v4.1-flash`, validated against the
69-model authenticated catalogue on 2026-09-14. V4 Flash is a separate model;
the provisioner refuses to configure defaults if V4.1 Flash is missing.

## Installation and credential provisioning

Install upstream `@mariozechner/pi-coding-agent@0.73.1` under `~/.local` and
Node 22.22.2 under `~/.local/share/agent-console/pi-runtime` (npm package `node`).
Install `scripts/pi-wrapper` as `~/.local/bin/pi-agent-console` and set
`AGCONSOLE_PI_BIN` to that absolute path in Console's runtime.env.
Pi 0.73.1 uses the **bare environment-variable name** `CMD_API_KEY` in models.json;
newer upstream documentation uses a different interpolation syntax. Keep this
version pinned until the adapter and native smoke are reverified together.

Install `scripts/hermes-wrapper` as `~/.local/bin/hermes` and set
`AGCONSOLE_HERMES_BIN` to it. It invokes the existing Hermes checkout with its
venv interpreter, avoiding stale entrypoint shebangs and editable-install paths.
CT115's copied venv had a dangling Python link; its original target is retained
as `venv/bin/python.pre-commandcode`, and `python` points to `/usr/bin/python3.11`.

Run `python -m agent_console.commandcode` from the release with the credential
on stdin, using a secure host-local pipe (never paste the value into arguments
or logs). `--config-dir` defaults to `~/.config/agent-console`.
The provisioner validates syntax and authenticated API access before creating
or updating any files. It backs up existing credential and registry files under
`config/backups/commandcode-<UTC timestamp>/`, writes the key only to
`secrets.d/commandcode-main.env` (0600), and makes that context the default for
both tools. Contexts hold the endpoint, catalogue IDs, default and verification
timestamp, never the key. Rerun to refresh the catalogue or rotate the key.

## Launch behavior and limitations

Session launchers source the selected secret file at runtime. Pi gets a private
models.json with an environment reference and `--append-system-prompt` pointing
to Console's context file. Hermes gets a private HERMES_HOME/config.yaml with
`${CMD_API_KEY}`; its wrapper reads the Console context into
HERMES_EPHEMERAL_SYSTEM_PROMPT at launch, including after a rename/restart.
The user still sends the stored session brief explicitly in the composer.

Both desktop and mobile expose the selected context's model catalogue. The CLI
accepts `--model` and validates it against that context. Unsupported agent-mode
and reasoning-effort pins fail clearly. Console profile instructions reach both
harnesses, but neither adapter enforces a sandbox, read-only execution, or human
approval. Per-session assigned-skill isolation is unsupported; existing guards
reject profiles with assigned skills. Hermes's private home does not inherit
shared native memory/skills/config. The native tool remains otherwise available.

Existing OpenRouter contexts and files are preserved for rollback; the new
Pi/Hermes adapters require verified CommandCode contexts. Old persisted
launchers are not rewritten by release installation.

## Verification and rollback

Run `pytest tests/test_commandcode.py tests/test_auth_contexts.py
tests/test_profile_schema.py tests/test_core.py tests/test_web.py tests/test_version.py`
and the harness selector browser test. Run a bounded native smoke in a temporary
directory: give the system context a marker and request a tool read of a random
probe file; require both markers in the authenticated response. Exit status
alone is insufficient (Pi can exit 0 after an API error).

Before activation, snapshot runtime.env, auth-contexts.json, the existing
credential (if any), wrappers, runner and selected-release target. Retain the
previous release. Restore those files and the selected symlink, then restart
only the web service to roll back; do not terminate existing tmux sessions.
