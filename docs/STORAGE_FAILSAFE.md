# Storage failsafe

CT115 filled twice and both times the first visible symptom was unrelated. When
the state filesystem had no free blocks, the Console's SQLite WAL, tmux state,
tool overlays and session logs each failed separately, so the UI showed
`disk I/O error`, sessions stopped mid-task and restarts failed with "native
recovery selection is missing" or similar second-order errors. The cleanup that
followed was manual.

This change makes storage pressure a first-class, named condition:

* a launch is **refused before any state is written**, with a message that says
  how much space is missing;
* a **periodic guard** reclaims only regenerable state so the filesystem does not
  reach that point;
* operators can **see** the current level, from the API and from the guard log.

## Policy

Levels are measured on free space of the Console state directory
(`AGENT_CONSOLE_STATE_DIR`, default `~/.local/share/agent-console`):

| Level | Free space | Launch allowed | Guard action |
| --- | --- | --- | --- |
| `ok` | ≥ 10 GiB | yes | nothing |
| `warn` | 4–10 GiB | yes | tier 1 reclaim |
| `critical` | 1–4 GiB | yes, until the reserve is hit | tier 1 reclaim, exit status 2 |
| `blocked` | < 1 GiB | refused when below the reserve | exit status 2 |

A launch is refused when free space is below `AGENT_CONSOLE_MIN_FREE_MIB`
(default 2048 MiB — about one tool overlay plus its worktree and logs). The
refusal is a single `StoragePressure` error raised at the top of
`SessionManager.create`, so no context, launcher, worktree, overlay or session
row exists afterwards. The web layer maps it to HTTP **503** (retryable once
space is reclaimed) instead of the generic 400 used for bad requests.

## Reclaim tiers

Tier 1 never loses anything that cannot be regenerated:

* obsolete `releases/release-*` directories — the newest three plus whatever
  `current` and `canary` point at are always kept;
* `update-backups/*` older than 14 days;
* `logs/*` older than 30 days;
* `contexts/<session>` directories older than 7 days whose session is not live;
* `/tmp` entries older than 2 days (dotfiles, mounts and tmux sockets are never
  touched).

Tier 2 is opt-in (`AGENT_CONSOLE_STORAGE_GUARD_TIER2=1`) because it destroys
native conversation history:

* `tool-overlays/<session>` older than 14 days;
* `provider-state/<session-id>` older than 7 days.

Nothing belonging to a live session is ever selected. A session counts as live
when it is `reserved`, `attached` or `detached` in the database, when its name
prefix matches a live session, or when it was modified within the last hour.

## Operation

```bash
# report only, no deletion
python3 ops/lxc115-storage-guard.py --check --json

# reclaim at warn/critical (tier 1)
python3 ops/lxc115-storage-guard.py

# also reclaim aged provider history for sessions that are no longer live
python3 ops/lxc115-storage-guard.py --tier2
```

Each run appends one line to `logs/storage-guard.log` and writes
`storage-status.json` next to the Console database. Exit status is `0` for
`ok`/`warn`, `2` for `critical`/`blocked` and `1` for an unexpected error, so a
timer surfaces the condition without extra alerting glue.

Install the timer from `deploy/systemd/agent-console-storage-guard.{service,timer}`
after replacing the placeholder paths:

```bash
sudo cp deploy/systemd/agent-console-storage-guard.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-console-storage-guard.timer
systemctl list-timers agent-console-storage-guard.timer
```

## API

`GET /api/storage` (authenticated) returns the current level, free bytes, the
configured reserve and, when the guard has run, its last result:

```json
{"path": "...", "free_bytes": 21474836480, "free_gib": 20.0, "level": "ok",
 "minimum_free_bytes": 2147483648, "headroom_ok": true,
 "guard": {"level": "warn", "removed": 4, "freed_gib": 1.8, "...": "..."}}
```

## Verification

`tests/test_storage_guard.py` covers level boundaries, reserve parsing, the
refusal (including that no launch state is created), release retention around
`current`/`canary`, live-session protection for contexts, overlays and `/tmp`,
tier-2 gating, dry-run versus apply, and the private status file.

Historical note: the first occurrence (2026-10-09/10) was diagnosed after the
fact from `logs/agent-console.jsonl`, where the sweep failures and the ASGI
exceptions both carried `disk I/O error`.
