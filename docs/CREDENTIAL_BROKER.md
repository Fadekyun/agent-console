# Protected credentials

The optional credential broker lets you keep using the Environment variables page. Enter the variable name and key, choose global or project scope, and save. The broker holds the upstream key and adds it to each request. A session receives a broker token instead of that key. Replacing a protected key takes effect on the next request, including requests from an already-running broker-connected session.

Protected names are `N8N_MCP_TOKEN`, `DIRECTUS_MCP_TOKEN`, `BUSHI_MCP_TOKEN`, `OPENROUTER_API_KEY`, and `CMD_API_KEY`. Other environment variables continue to use the normal environment settings. Protected values must be nonempty ASCII bearer tokens without spaces or newlines.

Do not enter production keys until the feature has been verified in your installation. Development tests use synthetic credentials and mock upstreams. Enabling this feature does not import, rotate, erase, or migrate existing credentials. Existing direct connections and running sessions need an explicit restart to adopt the new launch configuration.

## Install the service

The broker is a separate system service, using its own Unix user. The example unit is [agent-console-credential-broker.service](../deploy/systemd/agent-console-credential-broker.service). It always listens on `127.0.0.1`; the default port is `8792`.

The following commands are a setup example for a host administrator. Run them from the reviewed Agent Console source checkout. They install an isolated broker copy and create the private directory; they do not start or alter Console sessions.

```sh
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin agent-console-broker
sudo python3 -m venv /opt/agent-console-broker
sudo /opt/agent-console-broker/bin/pip install '.[web]'
sudo install -d -m 0700 /etc/agent-console-broker
sudo install -m 0644 deploy/systemd/agent-console-credential-broker.service /etc/systemd/system/
```

Skip account creation when the dedicated account already exists. Do not install the broker as the session runner user. Its source and virtual environment must remain writable only by a host administrator.

Create a new random administration token locally, without putting it in a terminal command or printing it. Provision the same token into two files:

- `/etc/agent-console-broker/admin-token`: root-owned, mode `0600`. Systemd passes a private copy to the broker with `LoadCredential`.
- A file readable only by the Console web service user, mode `0600`, in a private directory. Use its absolute path as `AGENT_CONSOLE_BROKER_ADMIN_FILE`.

This administration token belongs to the local broker; it is separate from the upstream keys you will enter later. Never put either kind of credential in the source checkout, command arguments, logs, or screenshots.

Start the broker and verify its health:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now agent-console-credential-broker.service
curl --fail http://127.0.0.1:8792/healthz
```

Before enabling the broker, check the installed tool launchers. Selecting a Console release does not replace custom `AGCONSOLE_PI_BIN`, `AGCONSOLE_HERMES_BIN` or `AGCONSOLE_OPENCODE_BIN` wrappers. Install the reviewed `scripts/pi-wrapper`, `scripts/hermes-wrapper` and `scripts/opencode-wrapper` changes at the configured paths where those wrappers are used, preserving any unrelated local changes. Legacy wrappers that source credential files can reintroduce raw keys after Console prepares the environment. The [Pi/Hermes wrapper guide](PI_HERMES_COMMANDCODE.md#updating-an-existing-host-wrapper) describes the backup and verification process.

Configure the Console web service with:

```text
AGENT_CONSOLE_BROKER_URL=http://127.0.0.1:8792
AGENT_CONSOLE_BROKER_ADMIN_FILE=/absolute/private/path/broker-admin-token
```

Reload the Console web service in your normal maintenance window. New and explicitly restarted managed sessions use broker routes. Enter test credentials through **Settings → Environment variables**, then confirm behavior before entering real keys. Once enabled, the five protected names use broker storage exclusively; legacy host files and launch snapshots are not used as a fallback.

The example service installs a copy of the reviewed package. Upgrade that copy deliberately when upgrading Console; selecting a new Console release alone does not update the broker virtual environment.

## Set up a fresh Pi or Hermes account

Pi and Hermes also need a verified model catalogue. If their selected CommandCode account is already verified, keep it; replacing a protected key does not require provisioning again.

For a fresh installation, first save `CMD_API_KEY` through Environment. Then, as the Console service user, run this from the reviewed Console source directory using its installed Python environment. Set the two broker settings in this shell as well as the web service:

```sh
export AGENT_CONSOLE_BROKER_URL=http://127.0.0.1:8792
export AGENT_CONSOLE_BROKER_ADMIN_FILE=/absolute/private/path/broker-admin-token
CONSOLE_PYTHON=/absolute/path/to/installed-console-venv/bin/python
"$CONSOLE_PYTHON" -m agent_console.commandcode --broker --config-dir "$HOME/.config/agent-console"
```

Replace the Python and config paths with this installation's paths. If the key was saved only for a project, add `--project-id PROJECT_ID` to that command. No key is entered at the terminal: the command asks the broker for the authenticated model catalogue using the key already saved in the UI. It stores only model metadata in `auth-contexts.json`, creates `commandcode-main` for Pi and Hermes, and selects it as their default account. It does not create a raw-key credential file.

Refresh the Work page and choose the `commandcode-main` account when creating a Pi or Hermes session. The selected project must have an effective `CMD_API_KEY`; catalogue verification does not grant other projects access. To create a session in a project, use the Project field in **Settings → More controls → Open full control panel**. The simple Work form's repository field alone does not assign a project. Children inherit their parent's project.

If provisioning reports a missing key or unavailable broker, fix the Environment scope or broker connection and rerun the command. The legacy stdin provisioning path is rejected while broker mode is enabled.

## Fixed connections and inheritance

| Broker route | Default upstream | Protected name |
|---|---|---|
| `/mcp/n8n` | `http://192.168.1.73/mcp-server/http` | `N8N_MCP_TOKEN` |
| `/mcp/directus` | `http://192.168.1.71:8055/mcp` | `DIRECTUS_MCP_TOKEN` |
| `/mcp/bushi` | `http://192.168.1.67:8791/mcp` | `BUSHI_MCP_TOKEN` |
| `/mcp/openrouter` | `https://mcp.openrouter.ai/mcp` | `OPENROUTER_API_KEY` |
| `/model/commandcode/v1` | `https://api.commandcode.ai/provider/v1` | `CMD_API_KEY` |
| `/model/openrouter/v1` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |

Model routes accept only `GET /models` and `POST /chat/completions`. MCP routes accept `GET`, `POST`, and `DELETE`, forwarding the existing tool payloads. JSON responses, event streams, MCP session headers, and model streaming are preserved. Requests cannot select an upstream URL; query strings, unsupported routes, and upstream redirects are refused. Failed tool calls and inference requests are not automatically retried.

Only the host administrator can change destinations. An optional `--upstreams-file /etc/agent-console-broker/upstreams.json` argument reads a JSON map such as `{"mcp/n8n":"http://internal-host/mcp"}`. Its names are the six route names in the table, without a leading slash or `/v1`. Store no keys in that file. The broker refuses URLs containing credentials, query strings, or fragments. Override `BROKER_PORT` in the unit when needed and use the matching Console URL.

Credentials resolve in this order: broker host default, selected account default, global override, project override. Host and account defaults are optional broker control API entries; no existing host files are imported automatically. Disabling or deleting an override restores inheritance. Project suppression removes access even when a global or account key exists. Deleting a project removes its settings and revokes its broker tokens.

A broker token is shared by sessions with the same project and account. There is no extra per-session permissions system. The broker allows at most 2,048 contexts, reuses them after restarts, and resolves current settings for each request. If no effective key exists or the broker is unavailable, the connection fails clearly; the launcher does not substitute a raw key.

## Security boundary

The upstream store is `/var/lib/agent-console-broker/credentials.json`, mode `0600`, inside a `0700` directory owned by the broker user. Writes are locked, atomic, and synced to disk. Keep any host backups equally private. The control API accepts values but returns metadata only; it has no endpoint for reading saved keys. Proxy responses redact the active upstream key if an upstream echoes it, including across stream chunks. Logs contain only fixed route, HTTP method, status, and generic errors.

A separate broker user protects these saved upstream values from ordinary session processes. The local administration token must also be protected: if Console web and agent sessions run as the same Unix user, those sessions can read the Console user's token file and call broker administration. This setup does **not** claim isolation between processes sharing that user. Strong control-plane isolation requires running Console web separately from session workers or equivalent operating-system restrictions. Host administrators are trusted. A broker data token cannot call administration endpoints.

This feature does not remove existing raw keys from old files or processes. Existing host credentials remain accessible according to their current operating-system permissions. Do not describe old keys as hidden because the broker is installed.

## Verification and rollback

Run the isolated broker tests from the source checkout with the web dependencies installed:

```sh
python -m unittest tests.test_credential_broker -v
```

The tests exercise all six fixed routes, write-only metadata, inheritance and suppression, immediate replacement, project token revocation, permission separation, malformed input, redirects, streaming, cancellation, error redaction, and atomic storage. They contact no real upstreams and require no real credentials.

For rollback, keep the broker available for existing broker-connected sessions. Restore the previous reviewed application and its native launch assets through the normal rollback process, or stop those sessions and create new ones after restoring direct account configuration. Removing the two broker settings only changes new launches; this version refuses to restart or resume a broker-backed session with broker mode disabled, before stopping any running process. It never converts broker tokens into raw keys. Preserve the private broker store while deciding whether to retain it. No automatic reverse migration or credential rotation is performed.
