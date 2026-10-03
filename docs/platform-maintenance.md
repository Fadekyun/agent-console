# Installation, updates, and recovery

The host targets are Ubuntu 24.04 LTS, macOS 14+, and Windows 11 with Ubuntu 24.04 under WSL2. Python 3.11+, Node/npm, Git and tmux must already be available to the service user. Install at least one supported harness separately and complete its authentication as that same user. Native Win32 hosting is not supported.

## Install

From a reviewed checkout, run `./scripts/install.sh`. Linux uses a systemd user service; macOS uses `~/Library/LaunchAgents/com.agent-console.web.plist` and starts after login. On WSL2, enable systemd in the distribution and keep checkout, state, and workspaces in the Linux filesystem. `scripts/install-wsl.ps1` checks the initialized distribution and invokes `~/agent-console/scripts/install.sh` there; it does not install a distribution or clone unreviewed code automatically.

The installer discovers available harness executables; explicit executable overrides must be absolute paths. `agentctl doctor` and `agentctl skills doctor` report missing prerequisites/configuration. Authentication must be checked through the chosen harness itself; executable discovery is not evidence of a working credential.

Existing runtime keys, comments, role directory settings, and service definitions are retained. Explicit environment overrides change recognized installer settings; unknown keys remain intact. Runtime values are parsed as literal assignments, never shell commands. Configuration is validated and atomically replaced with mode 0600. Do not place shell expressions in `runtime.env`; use resolved values. New values requiring spaces or punctuation are quoted safely.

Each selected release uses its own `.runtime` Python environment. Requirements are installed, `pip check` and imports run before the candidate is used. `.runtime` is excluded from source manifests, avoiding interpreter symlink traversal and thousands of irrelevant package hashes. An input digest prevents accidental reuse with changed requirements. Candidate dependency failures leave previous runtimes intact. Historical releases retain the original `state/venv` fallback; the installer never upgrades that existing environment.

The runner preserves the optional presence-master startup path when `AGCONSOLE_DEVICE_PRESENCE=1`. Presence's kernel-backed receiver and guarded offline inspection remain Linux-specific; macOS should leave device presence disabled and use managed service inspection. This is a platform limitation, not a passed macOS verification.

## Update and rollback

Use `./scripts/update.sh <40-character-approved-main-sha>` from a Git checkout. The command checks protected `origin/main`, locks updates, retains a consistent SQLite backup and service/configuration snapshots, prepares the candidate runtime, and verifies a private canary before selecting it. Configured profiles are preserved. Changes in running/finished state, session names, and newly created sessions during the update are allowed; existing managed session identities must remain present.

Canaries bind only to loopback and receive a minimal environment with private HOME, configuration, database, workspaces, logs and tmux paths. Workflow dispatch is off, non-health HTTP requests and all WebSockets are rejected. Health must report the candidate's random identity and child PID; an occupied port, early process exit, unrelated HTTP 200, or timeout cannot pass. Processes are reaped and private files removed after verification.

Live service health checks require the selected release and its service process (or a presence-master descendant). Rollback selects the former release with its own dependencies and restores service configuration. The schema guard runs before older code is selected; incompatible rollback refuses without deleting new records. Keep the backup and use forward recovery when refusal occurs. Never restore an old SQLite snapshot over newer operational records merely to make a downgrade start.

Interrupted dependency preparation leaves no selected candidate; incomplete runtime directories are refused rather than reused. Existing selections are not advanced until runtime preparation and canary health pass. A first-install interruption after selection may require removing only the incomplete candidate runtime and rerunning installation; never delete the state database.

## Docker

Use the existing Dockerfile/Compose flow. The image is the code/dependency unit; replace or roll back the image through Docker, retaining mounted state/configuration/workspaces. `AGENT_CONSOLE_SERVICE_BACKEND=foreground` prevents calls to host service managers. `update.sh` refuses to mutate a running Docker image. The image supplies Python/Node/tmux, but external harnesses and credentials still need explicit installation/provisioning.

## Verification boundaries

Regression tests cover configuration retention, atomic write failures, dependency failure/reuse, durable session preservation, canary process/environment/identity checks and cleanup, and mocked systemd/LaunchAgent/foreground adapter behavior. Existing installer, release-selection and schema-rollback tests exercise temporary files and synthetic services. Native macOS login startup, Windows/WSL2 bootstrap, and Docker image build require those platform runners and are not claimed as verified by Linux unit tests. The canary is a startup check; it does not replace authenticated browser, harness, migration, or session lifecycle acceptance tests.
