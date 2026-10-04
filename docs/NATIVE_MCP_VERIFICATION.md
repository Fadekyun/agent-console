# Native Pi / Hermes MCP verification

Staging 0.19.3 generates native MCP configuration directly. The prior Pi
`auth: bearer`, `bearerTokenEnv`, `requestTimeoutMs` and `disabled` fields belonged
to the retired extension; Pi 0.99.2 rejected that format without a host shim.
Native entries now use `${VAR}` authorization headers, `timeout` in seconds,
`exposure: codemode` and `{url, enabled: false}` for missing credentials.
Hermes 0.21.4 also reads `timeout` (not `tool_timeout`) for individual servers.

Both adapters offer n8n, Directus, OpenRouter and Bushi only when their credential
variables are present. Defaults and optional URL overrides live in the shared
descriptor table. No credential values are serialized. Relaunch replaces stale
managed entries; running sessions are not changed by a release switch.

## Reproducible native check

`scripts/verify-native-mcp.py` accepts `--pi-root`, `--node`, `--hermes-root`
and `--hermes-python` pointing to installed runtimes. Run it with the Console's
Python. It enforces Pi 0.99.2 and Hermes 0.21.4, creates temporary homes and uses
a clean environment containing only synthetic fixture credentials. It writes
configuration through the actual Console provider adapters, then loads it
through native harness readers and connects to a temporary loopback HTTP server.
It never invokes a model or connects to an external MCP service.

Verified on 2026-10-02:

- Pi native `loadMcpConfig` and `McpServerConnection`: four servers connected,
  one tool discovered and called per server, correct credential interpolation
  checked by the HTTP fixture, and expected request timeouts.
- Pi native loader: all four missing-credential entries validate as disabled;
  a trusted project entry overrides the agent-directory entry. This explicitly
  disproves the old highest-precedence/complete-isolation claim.
- Hermes native `_load_mcp_config` and `MCPServerTask`: the same four connections,
  tool listings and authenticated calls; 1200-second Bushi timeout retained.
- Sixteen adapter tests cover no embedded secrets, private files, URL overrides,
  credential removal on relaunch, provider models and existing reasoning gates.

The initial native test exposed the wrong Hermes timeout key; the correction
was verified by the actual native connection's resolved timeout, not just by
comparing generated JSON.

## Boundaries and rollout

Native project/settings/plugin sources can add or override tools. Console
configuration delivery is not a sandbox or complete MCP allowlist. Skills do
not grant access: tool credentials and role/action permissions remain separate.
Staging's missing authenticated Pi/Hermes launchers remain setup-required; this
fixture demonstrates installed native integration, not authenticated model use.

The source Pi wrapper now names `@earendil-works/pi-coding-agent`; explicit
launcher overrides remain supported. Current-console host wrappers, extension
directories, credentials and generated session files were not changed. Retire
host shims only through a separate verified host rollout. Rollback to 0.19.2
retains all application state but requires that shim for its legacy Pi format.
