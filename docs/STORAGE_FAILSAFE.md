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
(default 4096 MiB, matching the host resource admission that already guards the
LXC deployment, so one gate cannot be laxer than the other). The
refusal is a single `StoragePressure` error raised at the top of
`SessionManager.create`, so no context, launcher, worktree, overlay or session
row exists afterwards. The web layer maps it to HTTP **503** (retryable once
space is reclaimed) instead of the generic 400 used for bad requests.

## Reclaim tiers

Tier 1 runs on **every** invocation and never loses anything that cannot be
regenerated:

* obsolete `releases/release-*` directories — the newest three plus whatever
  `current` and `canary` point at are always kept;
* `update-backups/*` older than 14 days;
* `logs/*` older than 30 days;
* `contexts/<session>` directories older than 7 days whose session is not live;
* managed `/tmp` trees older than 2 days — only names the Console tooling creates
  (`agc-*`, `offkai-*`, `ac-*`, `pr1*`, `pr2*`, `pi-*`, `codex-*`, `console-*`,
  `hermes-*`, `pnpmhome`, `node-compile-cache`, `ops-*`); anything else in `/tmp`
  belongs to another job and is never touched;
* superseded `harness-assessments/<timestamp>` snapshots — the newest two plus
  whatever `latest.json` names are kept, and a snapshot younger than six hours is
  left alone in case a run is still writing it.

Tier 1 runs on **every** invocation, not only under pressure: bounded growth is
the point, and each rule keeps a hard minimum. Tier 2 below only runs when the
filesystem is at `warn` or worse **and** `AGENT_CONSOLE_STORAGE_GUARD_TIER2=1`,
so enabling it never deletes history on a healthy disk.

Tier 2 is opt-in (`AGENT_CONSOLE_STORAGE_GUARD_TIER2=1`) because it destroys
native conversation history:

* `tool-overlays/<session>` older than 14 days;
* `provider-state/<session-id>` older than 7 days.

## Safety properties

- **Fail closed.** If the session database exists but cannot be read, every
  session-dependent rule (contexts, overlays, provider state, `/tmp`) is skipped
  and the run reports an error. An unreadable database must never look like "no
  sessions", because a lock or corruption is exactly when a deletion would be
  most damaging. Rules that do not depend on session state (releases, update
  backups, logs, assessments) still run.
- **Activity, not directory age.** Eligibility uses the newest file inside the
  candidate, not the top-level directory mtime, and anything written inside
  `PROTECT_SECONDS` (one hour) is never selected. Touching a single file deep in
  an old tree therefore protects the whole tree.
- **Containment.** A candidate must resolve inside its own root, with no
  symlinked component, and must be owned by the guard's user. Symlinked category
  roots and escapes are ignored.
- **Pointers rechecked at delete time.** Release candidates are re-verified
  against `current`/`canary` immediately before removal, so a deploy or rollback
  that flips pointers between planning and applying cannot lose a live release.
- **Serialised.** Applying takes an exclusive `storage-guard.lock`; a cron run and
  an operator run cannot act on the same plan concurrently, and a blocked run
  reports an error instead of pretending success.
- **Live sessions** are excluded by database status (`reserved`, `attached`,
  `detached`), by session-name prefix, and by the activity window above.
- `contexts/<name>.md` instruction files are kept even when stale: a few
  kilobytes each, bounded by session count rather than age, and possibly edited
  by an operator. Only the heavy per-session context *directories* are pruned.

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

## Approved cleanup proposals

Anything destructive runs through a digest-bound proposal, so what an operator
approves is exactly what executes.

```bash
# 1. plan only; writes storage-proposals/<sha256>.json and prints the digest
python3 ops/lxc115-storage-guard.py --propose --json

# 2. after approval, execute exactly that digest
python3 ops/lxc115-storage-guard.py --apply-plan <sha256>
```

A proposal is a canonical manifest of the guide's decisions: host, state root,
retention policy version, tier, and every path with its kind, tier and size. Its
sha256 covers the host, state root, policy and action list, so:

- editing the file invalidates the digest, so an approval cannot be stretched to a
  different set of paths;
- a proposal expires after seven days, so an old approval cannot be replayed;
- execution re-plans and refuses on drift, so anything that became (in)eligible
  after approval needs a fresh approval;
- a digest can be claimed once, so a retried webhook or a double click cannot run
  the same deletion twice;
- a policy change changes every digest, and a proposal from another state root is
  refused.

Exit codes for `--apply-plan`: `0` applied, `1` applied with cleanup errors, `2`
refused (invalid, expired, drifted, or already executed). Models may summarise or
critique a proposal but cannot create one, add paths, or approve one: the digest
they talk about is produced by the deterministic planner.

## Bi-weekly approval loop

Lossless retention stays automatic (every 15 minutes). Anything destructive goes
through a proposal the operator approves, on the 1st and 15th at 09:00 SGT.

```bash
ac-storage-loop-run --propose            # 1st and 15th: plan, summarise, notify
ac-storage-loop-run --poll               # every 15 min: execute approved digests
ac-storage-loop-run --propose --dry-run  # print the card, post and delete nothing
```

The flow, all outbound from the Console host — nothing gains shell access to it:

1. `--propose` calls `storage_proposal.build`, writes the manifest, asks the cheap
   OpenRouter model for a plain-language summary, asks a local Sol review
   (`codex exec`, read-only) for a verdict, and posts one Discord card with the
   digest, the size, both opinions and an approval link.
2. The approval link points at the n8n workflow `AC storage approvals`, which
   records `{digest, approver, approved_at}` in the `ac_storage_approvals` data
   table.
3. `--poll` reads the approved digests, refuses any whose approver is not the
   configured owner, then runs `storage_proposal.execute` — which re-hashes, checks
   expiry, re-plans for drift and claims the digest once before deleting.
4. The result is marked back into n8n (`executed` / `refused`) and posted to
   Discord with what was removed and the free space afterwards.

If the Sol review returns a verdict of `object`, the card is posted **without** an
approval link; the plan has to be changed first.

Configuration is host-local in `~/.config/agent-console/storage-loop.env` (0600):
n8n base URL and the three webhook paths, the public base URL used inside the
approval link, the Discord webhook, the owner's Discord id, and the OpenRouter key.

**Known limitation.** The approval link is a bearer credential: whoever holds the
link can approve, and the recorded approver id is asserted by the caller rather
than proven by Discord. It is bounded by a single-use digest, a seven-day expiry
and the drift check, and the console host only executes digests approved inside
that window. Replacing it with a signed Discord interaction (button press handled
by the bot) is the next hardening step if the link ever leaves the operator's own
devices.
