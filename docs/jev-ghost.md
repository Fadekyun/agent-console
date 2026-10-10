# Jev ghost probes

A **ghost probe** is a bounded, low-cost synthetic check that keeps the shared
Jev (TypeSafe) credential and typed-answer path under continuous test while
other work happens. It is intentionally separate from the Console web process:
the Console only **reads** the probe's artifacts and renders them in the
**Jev Ghost** view.

## What a cycle does

`scripts/jev-ghost-probe.py` performs one cycle:

1. Reads `TYPESAFE_API_KEY` from the **inherited environment only** (never the
   secret file, never prints the value).
2. Sends one small typed request (two questions) to the System One endpoint,
   rotating through a fixed scenario list (`email_intent`, `ticket_routing`,
   `claim_check`).
3. Asserts response schema, option domains, probability/confidence ranges, and
   per-scenario expected outcomes.
4. Records a sanitized run (status, HTTP code, model, typed answers, check
   counts, duration, usage, skill-path status) and refreshes the rolling
   summary.

No hostnames, IPs, usernames, or secret values are recorded. Cycles are
single-writer guarded and history is capped.

## Artifacts

Under `<state_dir>/jev-ghost` (override with `AGENT_CONSOLE_JEV_GHOST_DIR`):

| File | Purpose |
| --- | --- |
| `runs.jsonl` | one run record per line (newest appended) |
| `latest.json` | the most recent run |
| `summary.json` | rolling aggregate (pass ratio, streak, per-day counts) |
| `run.lock` | single-writer guard (stale after 10 minutes) |

The Console route `GET /api/jev-ghost?limit=N` reads these files defensively and
drops unknown fields.

## Running it

One cycle:

```sh
TYPESAFE_API_KEY=... python3 scripts/jev-ghost-probe.py
```

Periodic (systemd user units; the secret is referenced, never inlined):

`~/.config/systemd/user/jev-ghost.service`

```ini
[Unit]
Description=Jev ghost confidence probe (one bounded cycle)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=%h/.config/agent-console/secrets.d/typesafe.env
ExecStart=%h/.local/bin/jev-ghost
Nice=10
```

`~/.config/systemd/user/jev-ghost.timer`

```ini
[Unit]
Description=Periodic Jev ghost confidence probe

[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
RandomizedDelaySec=3min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable with `systemctl --user enable --now jev-ghost.timer`. Check with
`systemctl --user list-timers jev-ghost.timer` and
`journalctl --user -u jev-ghost.service`.

## Safety

- The probe grants no permissions and authorizes no execution; it is a
  read-only health signal.
- Failure is recorded per run and must not leak the credential.
- Keep payloads minimal; each cycle is a few hundred input tokens.
- The permission model is unchanged: the Console view is read-only.
