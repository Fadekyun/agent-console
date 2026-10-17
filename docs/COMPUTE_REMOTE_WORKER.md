# AGC laptop compute and central publication

Issue #203 extends the existing compute queue. The queue stays on CT115 in
`connected-work.sqlite3`; databases, payments and serving processes stay on N100.
No Kubernetes migration, laptop wake schedule, staff browser profile, desktop or
casting controller change is part of these queue APIs.

## Enablement and identity

Dispatch still requires `AGENT_CONSOLE_COMPUTE_ENABLED=1` and the durable owner hold
to be released. `AGENT_CONSOLE_COMPUTE_LAPTOP_CONFIG` points to a root-owned JSON
file, non-writable by group/other, containing:

```json
{
  "enabled": true,
  "host": "100.111.183.86",
  "user": "agc-worker",
  "identity_file": "/etc/agent-console/compute-worker-ed25519",
  "known_hosts_file": "/etc/agent-console/compute-worker-known-hosts"
}
```

The key is dedicated to the restricted worker account, pinned to the existing
host key. Every call is outbound CT115 SSH to a fixed `agc-worker rpc` dispatcher;
no job controls an SSH option, command, environment or filesystem path. Provision
that key and the Windows/guest limits through the worker installation procedure
before enabling the configuration. Enrollment alone does not declare readiness:
verified host telemetry, exact adapter capability and a healthy observation
window are also required.

Automated producers use `/api/compute/producers/*`, separately from the unchanged
owner `/api/compute/*` controls. The root-owned file selected by
`AGENT_CONSOLE_COMPUTE_PRODUCERS_CONFIG` contains a `producers` object keyed by
service name. Each entry requires `enabled`, `token_sha256`, `source_cidrs`,
`kinds`, `request_prefix`, `max_priority` and `publish_targets`. Set
`allow_production_writes` only for an independently authorized Bushi producer.
Give each producer its own high-entropy token, store it host-locally, and send it
as `Authorization: Bearer …` with `X-Compute-Producer: service-name`.

Producer HTTP is accepted only over direct TLS or loopback reached through an
authenticated SSH tunnel. Forwarded headers never establish TLS or peer identity.
A cleartext LAN request is rejected even with a valid token. Browser and managed
agent headers are rejected. Producers cannot change holds, retry attempts,
reconcile unknowns or access another producer's jobs. Credentials do not grant
general owner identity. Local managed CLI guards are unchanged.

## Immutable request

The existing enqueue fields remain: `kind`, `execution_target`, `source_digest`
(SHA-256 of the source artifact), `input_digest`, `request_key`. Laptop requests
also carry an immutable `manifest`, with this shape:

```json
{
  "version": 1,
  "kind": "agc.build",
  "source_sha": "exact-lowercase-40-or-64-character-commit",
  "source_artifact": {"sha256": "64-character-digest", "size": 1234},
  "inputs_artifact": {"sha256": "64-character-digest", "size": 5678},
  "input_sha256": "same-digest-as-input_digest",
  "parameters": {},
  "production_writes": false
}
```

`inputs_artifact` is optional. Parameters are validated by each installed adapter;
no caller argv is accepted. The source commit, input digest, complete canonical
manifest hash, job ID, attempt ID and lease generation are bound together on
every worker response. Canonical JSON uses sorted keys, compact separators,
UTF-8 and no ASCII escaping. Manifests are capped at 64 KiB.

Optional scheduling fields are `dependencies` (at most 20 existing owned jobs),
`priority` (0–100, also producer-limited), `not_before`, and `deadline` (Unix
seconds). Dependencies must complete before admission. Missed deadlines remain
visible as `deadline_passed_requires_review`; they never silently execute late.
Request keys identify immutable jobs, including scheduling fields and manifests.

Profiles are `yuyutei.collect`, `yuyutei.parse`, `agc.build`, `agc.test`, `tv.build`,
`points.weekly`, `points.player_events`, and `bushi.run`. A disabled `bushi.emergency` profile describes the future bounded
N100/CT102 fallback; no executable emergency adapter is enabled by this change.
Both Bushi profiles require the same durable `bushi-organizer` named reservation,
acquired atomically with the physical-host reservation and retained for unknown
attempts until termination is reconciled. Local flocks alone do not release this
cross-host authority. The owner-approved
ceilings are 16,000,000,000 bytes total worker RAM including overhead/shared GPU,
12 logical processors and 300,000,000,000 bytes SSD usage, with one job at a time.
Build profiles currently cap at 8 GiB; browsers and data jobs at 2 GiB. Physical
RAM and CPU counts are verified on each sample. Admission retains at least 25%
or 8 GiB RAM for staff, 15% of the Windows volume free, and 6 GiB guest disk free.
AC power, Wednesday–Sunday availability and Sunday 23:45 drain apply. Monday/Tuesday skip even status and archival RPCs so a probe cannot start WSL. A fresh
sample (30 seconds) and 5-minute healthy window are required. CPU >=90% or commit
>=85% defers new work; the independent worker enforces ongoing staff throttling.
GPU support is not implied by a RAM/CPU admission result.

## Transfer and completion

Upload source/input blobs with `POST /api/compute/producers/artifacts/{sha256}`:
`{size, offset, data, final}` where `data` is base64 for at most 256 KiB. The
response's authoritative offset supports retry after a lost acknowledgement.
Finalization requires the exact total size and SHA-256. Objects are at most 2 GiB.
The store has a 20 GiB upper bound **and** a strict 6 GiB free-space floor: this
is not a claim that CT115 currently has 20 GiB available. Admission to transfer
fails when the current filesystem cannot fit the next chunk.

A result is not marked completed until all its artifacts have been fetched,
hashed, fsynced and atomically installed centrally. A worker inventory is at most
2 MiB and 4096 files, shaped `{version:1,binding:{…},files:[{path,sha256,size}]}`.
The small result receipt names `inventory_artifact:{sha256,size}` and includes
that descriptor in `artifacts`. Inventory paths are descriptive, never extraction
paths. The worker performs safe extraction/materialization separately.

Download accepted result chunks from
`GET /api/compute/producers/jobs/{job_id}/artifacts/{sha256}?offset=…&length=…`.
Only immutable objects in the producer's completed result are readable.

A start request is durably marked sent before SSH. Its acknowledgement may be
lost; the controller subsequently inspects that attempt and never resends start.
Leases expire after 120 seconds. The worker watchdog must stop unattended work
within 90 seconds and checkpoint after 60 seconds. Unknown attempts retain the
physical-host reservation until an authenticated, exact-bound terminal receipt
or owner-supplied termination evidence reconciles them. Reconciliation does not
automatically accept results or replay production writes. OOM jobs never retry
with an unchanged budget; write-capable Bushi jobs never replay through retry.

## Central publication and retention

1. Verify the accepted artifact contract and current production preconditions.
2. `POST /jobs/{job_id}/publication` under the producer prefix with `target` and
   `request_key`. Only the first successful claim returns `may_publish:true`
   and `receipt_token`.
3. Perform the bounded central activation/transaction using the domain's existing
   atomicity and rollback procedure. The laptop receives no production DB or
   deployment credential for these operations.
4. `POST /publications/{id}/finish` with `token` and verification `evidence`.
   The claim expires after 600 seconds. An expired or uncertain claim remains
   unknown; neither a new key nor the same key permits duplicate publication.
5. After the destination has retained its required release/rollback artifacts,
   `POST /jobs/{job_id}/consume` with durable retention `evidence`. This explicitly
   acknowledges that central transfer blobs may be retired.

Only acknowledged results with completed publications can enter garbage
collection. Unknown/unpublished jobs, all other live references, and 48-hour
upload pins protect shared objects. Job, attempt and publication receipts remain
in SQLite. After central artifact acceptance, a durable acknowledgement outbox
lets the worker reclaim its archived workspace and unshared blobs; receipt and
inventory metadata remain. Lost acknowledgement responses are retried safely. Orphan uploads and `.part` files are never deleted solely by age.
The owner-only `POST /api/compute/publications/{id}/reconcile` records
`outcome: completed|abandoned` and external verification `evidence` for an expired
publication. It never executes a production action. Do not clear unknown receipts merely to unblock a queue; reconcile the actual
external effect first. The publication fence supplements each domain's own
transaction/idempotency contract and does not make a multi-system write atomic.

## Rollback and verification

Set the durable owner hold first. Existing remote work is stopped/inspected; no
new job starts. Disable the worker configuration only after termination receipts
are confirmed. Preserve unknown reservations and the database. Schema version 2
is additive but older queue code rejects it intentionally, so a code rollback
must keep the compatible queue modules or restore a pre-migration database only
when no post-migration receipts would be lost. Never blindly replay old timers.

Run the bounded `test_compute*` suite, plus live worker qualification for resource
limits, staff priority, AC/schedule interruption, lost SSH acknowledgements,
artifact corruption, and production publication reconciliation before migrating
each workload's old timer. Source unit tests cannot prove Windows/WSL isolation
or actual laptop capabilities.
