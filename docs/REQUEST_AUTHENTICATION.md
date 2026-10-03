# HTTP and terminal authentication

The Console uses the socket peer address, an explicit proxy allowlist, and an operator login. Run Uvicorn with `--no-proxy-headers` (the provided service and installer runner already do this). `X-Forwarded-For` and `Forwarded` are not authority: allowing a server/framework to rewrite `request.client` would undermine proxy-source checks.

- `AGENT_CONSOLE_TAILSCALE_LOGIN` remains required on HTTP, presence reads and terminal WebSockets. An absent configuration denies access consistently.
- `AGENT_CONSOLE_TRUSTED_PROXY_CIDRS` is a comma-separated list, default `127.0.0.1/32,::1/128`. Only these socket peers may assert `Tailscale-User-Login`. Configure exact proxy addresses, not the entire LAN. Empty configuration disables forwarded identities.
- A configured proxy **must** supply the matching identity header. It cannot fall back to trusted-LAN access, even if its address is inside `AGENT_CONSOLE_LAN_CIDR`. Configure the proxy to authenticate users and overwrite identity headers, never copy client-supplied identity.
- Direct clients inside `AGENT_CONSOLE_LAN_CIDR` retain LAN authority when they are not configured proxies and send no identity header. A forged identity header from an untrusted peer is denied, including LAN peers.
- `AGENT_CONSOLE_ALLOWED_ORIGINS` controls browser terminal connections. Default: `http://localhost:3210,http://127.0.0.1:3210`. Entries must be exact HTTP(S) origins with no paths. Scheme, hostname and effective port must match; HTTPS port 443 and HTTP port 80 normalize to their defaults. No wildcards or `null` origins are accepted. This is separate from `AGENT_CONSOLE_TRUSTED_HOSTS`, which restricts incoming Host headers.
- Native/non-browser WebSocket clients may omit Origin, but still require the same identity and peer authorization. Browsers provide Origin; foreign or duplicate origins are rejected before session inspection or PTY allocation.

For the currently verified deployment, CT969 Tailscale Serve forwards `https://tailscale-router.tail9f3ae6.ts.net` to `http://192.168.1.115:3210`, with socket peer `192.168.1.64`. Before cutover, configure:

```dotenv
AGENT_CONSOLE_TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,192.168.1.64/32
AGENT_CONSOLE_ALLOWED_ORIGINS=https://tailscale-router.tail9f3ae6.ts.net,http://192.168.1.115:3210,http://localhost:3210,http://127.0.0.1:3210
```

Keep the existing login, trusted-host and LAN settings. Direct LAN clients such as `192.168.1.94` remain authorized through the LAN policy. Loopback is a proxy peer by default and therefore needs the identity header; use an explicit direct LAN URL for browser access. Health checks and scoped agent-reporting capabilities keep their independent authorization policies. No live configuration is changed by this source update.

If adding NPM or another proxy, first verify the actual socket source and that it enforces authentication, then add its exact CIDR and externally visible origin. Preserve these variables on upgrade. Rolling back code restores the previous weaker header policy; remove the new configuration variables only when no longer used.
