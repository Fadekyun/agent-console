# AGC laptop compute and central publication

Issue #203 extends the existing compute queue. The queue stays on CT115 in
`connected-work.sqlite3`; databases, payments and serving processes stay on N100.
No Kubernetes migration, laptop wake schedule, staff browser profile, desktop or
casting controller change is part of these queue APIs.

## Enablement and identity

Authenticated owners can pause or resume dispatch on `/compute`; the page uses the same protected owner API. Managed agents and producer identities cannot use these controls.

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
`points.weekly`, `points.player_events`, and `bushi.run`. The separately enrolled
`bushi.emergency` profile supports explicit urgent paid participation on CT102
only after the qualification described below; it has no implicit fallback.
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
lets the worker reclaim its archived workspace; receipt, inventory and bulk
content objects remain until a separately qualified worker cache policy retires them. Lost acknowledgement responses are retried safely. Orphan uploads and `.part` files are never deleted solely by age.
The owner-only `POST /api/compute/publications/{id}/reconcile` records
`outcome: completed|abandoned` and external verification `evidence` for an expired
publication. It never executes a production action. Do not clear unknown receipts merely to unblock a queue; reconcile the actual
external effect first. The publication fence supplements each domain's own
transaction/idempotency contract and does not make a multi-system write atomic.

## Rollback and verification

Set the durable owner hold first. Existing remote work is stopped/inspected; no
new job starts. Disable the worker configuration only after termination receipts
are confirmed. Preserve unknown reservations and the database. Schema version 5
is additive but older queue code rejects it intentionally, so a code rollback
must keep the compatible queue modules or restore a pre-migration database only
when no post-migration receipts would be lost. Never blindly replay old timers.

Run the bounded `test_compute*` suite, plus live worker qualification for resource
limits, staff priority, AC/schedule interruption, lost SSH acknowledgements,
artifact corruption, and production publication reconciliation before migrating
each workload's old timer. Source unit tests cannot prove Windows/WSL isolation
or actual laptop capabilities.

## Forced-command producer bridge (CT106/CT955 to CT115)

`agent_console/compute_producer_bridge.py` is a standalone standard-library file.
Install a reviewed copy root-owned under `/opt/agent-console-producer/`; invoke
with `/usr/bin/python3 -I` so caller Python paths cannot replace imports. The
`bridge` command accepts one bounded JSON object on stdin and returns one JSON
object on stdout. It ignores `SSH_ORIGINAL_COMMAND` and accepts no caller headers.

Request: `{"version":1,"method":"GET","path":"/api/compute/producers/jobs/JOB"}`.
POST requests add an object `body`. Response: `{"status":200,"body":{...}}`.
Only the producer upload, enqueue, inspect, fetch, publication begin/inspect/finish
and consume routes are accepted. Owner hold/retry/reconcile routes are inaccessible.
The fixed destination is CT115's own `http://192.168.1.115:3210` listener,
with a mandatory `127.0.0.1` source bind. Before each connection, the bridge requires
the kernel route to be type `local`, device `lo`, table `local`, with the exact
source/destination. It verifies connected socket endpoints before sending headers.
Any failed check refuses the request. This works with the existing LAN-address-only
listener while remaining entirely on-host; authenticated SSH protects the network leg. No proxy environment, redirects, forwarded identity or managed-agent
headers are used. API producer policies must separately allow `127.0.0.1/32` and
retain workload, request-prefix, production-write and publication-target limits.

Provision **one dedicated Unix account per producer**, each with no sudo, no
Console state access and read access only to its own root-owned configuration.
Use a root-controlled authorized-keys path and an entry shaped as follows (replace
public key and source IP with enrolled values; this is a template, not an installer):

```text
restrict,from="192.168.1.71",command="/usr/bin/python3 -I /opt/agent-console-producer/compute_producer_bridge.py bridge --config /etc/agent-console-producers/points.json" ssh-ed25519 PUBLIC_KEY
```

Enforce no forwarding, PTY, tunnels or agent forwarding in the account's sshd
policy as well. Do not permit account-owned authorized keys to enroll another
command. The fixed command needs no root privileges. Config shape is exactly
`{"producer":"points","token":"HOST_LOCAL_GENERATED_BEARER"}`; use root-owned
0640 with the dedicated account's private group, protected root-owned ancestor
directories, and no world access. The API stores only the matching token SHA256.
No token is sent to the source host in SSH mode.

The same Python file supplies `ProducerClient.from_file(path)` and CLI commands
`request`, `upload --file PATH`, `fetch --job JOB --artifact SHA256 --file NEW_PATH`.
Every command takes `--config PATH`. Client SSH configuration has exactly:

```json
{
  "transport": "ssh-stdio",
  "ssh_host": "points-producer@192.168.1.115",
  "ssh_identity_file": "/etc/points-compute/id_ed25519",
  "ssh_known_hosts_file": "/etc/points-compute/known_hosts"
}
```

Pin the host key independently before enabling. All paths are absolute and
protected; configuration and ancestor directories are root-owned. Private keys
may also be owned by the dedicated client UID. SSH uses no remote command, no
user SSH config, no multiplexing, no environment forwarding and strict host pins.
Direct HTTPS clients instead use exactly `transport:"https"`, `endpoint` (origin
only), `producer`, and `token` in the protected local config; literal loopback HTTP
is also permitted for local services. LAN HTTP and URL credentials are rejected.

Request envelopes are capped at 512 KiB; responses at 1 MiB. Calls are time bounded,
artifact chunks are 256 KiB, and the API's 2 GiB/object ceiling remains. Uploads
follow the central authoritative offset; fetches verify size and SHA256 before
an exclusive durable destination creation. There are **no automatic retries** on
transport failure. Reconcile the existing immutable request/job/publication before
retrying; a lost publication reply never grants another publication attempt.

Validation: `python -m unittest discover -s tests -p test_compute_producer_bridge.py`.
Tests use disposable files and local subprocess fixtures, with no network access.
Rollback: revoke only the affected producer SSH key/account or disable its API
policy; preserve queue, artifacts and publication receipts for reconciliation.

## External write uncertainty is separate from process termination

A Bushi worker may exit after clicking submit without knowing whether the site
applied it. The fixed worker classifies its bound `response.json`
`errorType: write_unknown` as `state: unknown`, `reason: external_write_unknown`
and includes its immutable output inventory. The controller persists a separate
external-write fence **before** artifact retrieval. A failed transfer, expired
lease, restart or later process-exit receipt cannot release `bushi-organizer`.
Both host and named reservations stay held. Production-write failures without
proof that no write started are treated conservatively the same way.

Evidence artifacts are retrieved and verified while the attempt remains unknown;
the owning producer may fetch archived evidence for review. Unknown results are
never acknowledged to the worker or automatically consumed. Ordinary termination
reconciliation explicitly rejects these attempts, including after process exit.

Only authenticated owner control can call
`POST /api/compute/attempts/{id}/reconcile-external` with the exact `generation`,
`outcome: applied|not_applied`, and independently verified external `evidence`.
Confirmed process termination is also required. This records the reviewed outcome
and releases reservations, but leaves the original attempt terminal and ineligible
for replay. Follow-up work needs a new explicitly reconciled request. This route
is absent from the producer bridge and inaccessible to managed agents.

Schema 3 prevents older queue code from ignoring the external fence after rollback.
Tests cover transfer failure, restart, late success after lease loss, failed writes,
ordinary termination reconciliation, evidence fetch authorization and owner guards.


## Explicit urgent paid participation on CT102

This path uses the existing durable queue, physical N100 reservation and shared
`bushi-organizer` reservation. It never sends a second browser job because a
laptop request became delayed, disconnected or uncertain. The first admitted
request explicitly chooses `bushi.emergency`, execution target `n100`; the
laptop remains the target for routine browser jobs and bulk data collection.

The initial emergency adapter supports only paid `participate`. It requires
`production_writes:true`, the existing exact event/player/order parameters, and:

```json
{
  "business_key": "agc:paid-participation:12345678-1234-4234-8234-123456789abc",
  "emergency": {"urgent": true, "deadline": 1792462500}
}
```

The UUID must equal `parameters.orderId`. The illustrative timestamp above must
be replaced with an actual future Unix timestamp: the first enqueue requires a
deadline within 1–1200 seconds, queue `deadline` equal to the manifest deadline,
and priority 90–100. Urgency is outside browser parameters. No `inputs_artifact`
is accepted for this initial adapter. The source commit **and** source archive
descriptor must match the root-qualified installed runtime. It cannot execute
an arbitrary uploaded source tree. The worker also checks the deadline before
submission. Central drains at least 30 seconds before the deadline/runtime cap.

Producer configuration must explicitly include `bushi.emergency` in `kinds`,
`allow_emergency:true`, `allow_production_writes:true`, suitable `max_priority`,
and `business_key_namespaces:["agc:paid-participation"]`. Existing laptop paid
producers need the same namespace permission. Other namespaces and arbitrary
cross-producer resource claims are rejected. Keep the stable paid request key
(e.g. `agct:participate:<order UUID>`) across delivery retries and target choices.

Schema 4 adds `compute_business_claims`. The paid-order claim is derived even
when optional `business_key` is omitted; supplying it requires an exact match.
The job and claim are created in one SQLite transaction. A new key, another
producer or another target cannot claim that order again. Claims survive
completion, cancellation, failure, external reconciliation and retention GC.
The additive migration backfills existing paid requests and aborts atomically
if historical claims conflict; it never picks a winner or deletes evidence.
Older queue binaries reject schema 4 instead of ignoring the new protection.

Root must independently qualify CT102 before setting
`AGENT_CONSOLE_COMPUTE_EMERGENCY_CONFIG` to a root-owned non-writable JSON file:

```json
{
  "enabled": true,
  "host": "192.168.1.67",
  "user": "bushi-compute",
  "identity_file": "/etc/agent-console-compute/emergency_ed25519",
  "known_hosts_file": "/etc/agent-console-compute/emergency_known_hosts"
}
```

The transport accepts only this host/user and fixed `agc-emergency rpc` command,
using a separate key and strict host pins. Restrict CT102's authorized key to
actual CT115 LAN source `192.168.1.115`, no PTY, forwarding, agent or shell access.
The root-reviewed runtime must enforce 1 GiB per job, 50% of one CPU, no job swap,
512 MiB temporary workspace and at most 1200 seconds or the remaining deadline,
whichever is smaller. Its browser receives only the independent organizer state;
no database credential or staff browser state belongs in this worker.

The trusted central sampler independently reads N100 `/proc` and fixed
`pct config 102`, requiring hostname `bushi-automation`, root volume
`local-lvm:vm-102-disk-0`, and the verified `pve/data` thin pool. CT102's configured
memory ceiling must be at most 2 GiB and agree with the actual outer cgroup
limit and the worker's enrollment. The previously observed 1 GiB CT102 setting cannot fit this job
plus reserve; changing it requires root's current headroom check and explicit
provisioning. No sampler modifies Proxmox configuration.

The emergency host probe now measures `/sys/fs/cgroup/lxc/102/memory.max` and
`memory.current` on N100, verifies the init PID from `lxc-info -n 102 -pH`, its
`/proc/<pid>/cgroup` membership and the cgroup2 mount root in `mountinfo`.
The actual limit must equal `pct config 102` and be at most 2 GiB; a tighter
unexpected ancestor, remap or restart during the probe fails closed. CT102's
visible namespace root may legitimately report `max` because its outer parent
is hidden. Neither that value nor guest `MemAvailable` is a capacity grant.
The sample includes `ct102_memory_current_bytes`, `ct102_cgroup_path` and
`ct102_namespace_root`; timestamp is the actual host observation. The receiver
requires all fields, freshness within 30 seconds (5 seconds future skew), and the installed
enrollment binding. Available memory is the lesser of actual outer headroom
and local namespace headroom, including a tighter finite visible limit.

Before initial installation or a deliberate cap change, run from the reviewed
Console checkout on CT115 using its already authorized N100 SSH identity:

```bash
/home/agentstage/.local/share/agent-console/venv/bin/python -I \
  ops/agc-worker/enroll-emergency-boundary.py
```

The helper is read-only and emits exactly
`{version:1,physical_host:"n100",guest_id:"lxc-102",cgroup_path:"/lxc/102",
namespace_root:"/lxc/102/ns",memory_limit_bytes:<actual>,observed_at:<unix>}`.
Root transfers that public JSON through the authenticated route to, for example,
`/root/bushi-emergency-stage/boundary-enrollment.json` (root-owned, mode 0600, protected
ancestors), then passes that path as **argument 7** to `install-emergency.sh`.
The receipt must be at most 300 seconds old when installed. Capture it after any approved
host cap change; never supply an invented numeric limit. Policy persists the
full `boundary_enrollment`, and every new trusted host sample must match it.
Existing policy needs held re-enrollment; sampler/worker upgrades are paired.
No sample at startup yields unknown capacity and a valid held status response.
Do not set the guest namespace root's limit merely to make enrollment pass.

Before each status sample, CT115 sends the emergency-only `host-sample` RPC:

```json
{
  "protocol_version": 1,
  "action": "host-sample",
  "sample": {
    "observed_at": 1792461600,
    "physical_host": "n100",
    "memory_total_bytes": 16500310016,
    "memory_available_bytes": 6442450944,
    "cpu_count": 4,
    "ct102_memory_limit_bytes": 2147483648,
    "ct102_memory_current_bytes": 134217728,
    "ct102_cgroup_path": "/lxc/102",
    "ct102_namespace_root": "/lxc/102/ns",
    "memory_full_avg60": 0.1,
    "io_some_avg60": 0.5,
    "thinpool_data_percent": 79,
    "thinpool_metadata_percent": 10
  }
}
```

These are illustrative values, never producer input or fixed capacity. Worker
`status` returns identity `bushi-emergency`, boot ID, capability
`bushi.emergency`, admission readiness and telemetry keys `sampled_at`,
`guest_available_bytes`, `guest_memory_limit_bytes`, `guest_disk_free_bytes`,
`policy_verified`, `installed_source_sha`, `installed_source_artifact`.
The worker locally validates the host sample and measures guest resources;
its watchdog stops if trusted samples become stale. Central warm-up requires
five uninterrupted healthy minutes, resetting on boot, missing samples,
pressure or holds. Samples expire after 30 seconds; clock skew up to 5 seconds
is tolerated without shortening warm-up. The existing host maintenance veto
still applies.

Admission requires N100 available RAM >=4 GiB and >=2 GiB after reservations,
CT102 >=512 MiB after the 1 GiB job reservation, disk >=6 GiB after scratch,
memory full PSI avg60 <1, IO some PSI avg60 <10, thin-pool data <80% and metadata
<70%. While running, N100 <2 GiB available, guest <512 MiB, pressure, stale
telemetry, owner hold or deadline triggers stop. Worker self-reported busy
readiness does not by itself terminate the already running job. Monday/Tuesday
emergency operation does not contact or wake the laptop.

Acceptance before enabling a real producer: verify fixed SSH identity and
source pins, actual cgroup/CPU/tmpfs limits, host-sample expiry stop, successful
bound output inventory and acknowledgement, process kill/lease expiry, and an
external-write-unknown receipt that retains `bushi-organizer` across both hosts.
Use a non-writing harness for lifecycle faults; do not synthesize a paid action
against production. Pure compute regressions are in `test_compute_emergency.py`.

An authenticated owner may transfer a queued or blocked paid laptop job using
`POST /api/compute/jobs/<id>/transfer-emergency` or local human-owner
`agentctl compute transfer-emergency <id> --json-file <request.json>`. The body is:

```json
{
  "request_key": "owner:urgent-order-transfer-1",
  "expected_manifest_sha256": "<exact original 64-character SHA-256>",
  "deadline": 1792462500,
  "reason": "Laptop unavailable before this paid action cutoff"
}
```

The deadline must be an integer within the next twenty minutes, no later than
the original deadline, and after the original not-before time. No attempt may
ever have existed, including an expired reservation or a failed/cancelled
attempt. Completed, cancelled, running, unknown and noneligible jobs refuse the
operation. Any publication evidence also prevents transfer. This uses the same
SQLite write lock as admission, so a competing laptop reservation either wins
first (transfer refuses) or observes the job only on N100.

Schema 5 adds an append-only `compute_transfers` receipt containing both specs,
the original manifest and the owner request hash. Job ID, producer request key,
original `spec_hash`, owner, source, inputs, not-before time, dependencies and
business claim remain unchanged. The effective kind/target become
`bushi.emergency`/`n100`, priority is at least 90, and only `kind` and the explicit
`emergency` envelope change in the manifest. N100 still requires the exact
qualified source artifact and normal headroom gates; routing does not assert
capacity or waive an organizer reservation held by any other job.

GET job and exact original producer enqueue retries expose `transfers:[receipt]`.
The receipt has `version,id,request_key,job_id,original_kind,effective_kind,
from_execution_target,to_execution_target,from_manifest_sha256,to_manifest_sha256,
original_deadline,deadline,reason,at,operator`. Here `request_key` is the owner's
transfer key. Producers must derive the effective manifest from their original
envelope plus the recorded deadline and verify all unchanged fields and the
result binding. A producer without that support must remain held. Repeating the
same owner transfer request returns the same receipt even after execution; a
changed key/body cannot repeat or reverse the transfer. Producers cannot invoke
the owner route or use the managed-agent CLI to bypass it.

There is no automatic emergency routing or series/application write expansion.
Tuesday scheduling alone does not authorize a paid or browser action. Retain
normal durable laptop queues and deadline warnings for unsupported families.

Rollback: hold new admission, stop/reconcile the exact active attempt using its
fixed worker binding, then revoke the emergency key/config. Retain receipts,
artifacts, business claims and uncertain organizer reservations. Keep a schema-5
capable Console binary; never roll back to a state snapshot that loses a new
claim or unknown external action. Existing laptop/DB services stay in place.


## Series application and modification proof

The fixed laptop `bushi.run` adapter also recognizes `apply_series`,
`apply_manual` and `modify_criteria`, with exactly three parameters:
`action`, canonical positive-decimal `seriesId` and `mode` (`dry-run` or
`execute`). Execution needs separate producer namespace permission:
`agc:bushi-series-apply` for either application path or
`agc:bushi-series-modify` for criteria changes. Existing paid-only producer
permission does not grant these writes. No emergency series execution or new
automatic-application policy is introduced. Existing approved templates,
manual-only rules and owner approval remain authoritative.

Both modes require an immutable `inputs_artifact` containing exactly one regular
`request.json`, at most 128 KiB. Only plain tar or one gzip tar is supported,
with at most 256 KiB compressed and expanded bytes; no links, extra entries,
PAX extensions, duplicate JSON keys or filesystem extraction. Its envelope is
`{version:1,action,series_id,request,dry_run?}`. The approved adapter validates
the action-specific request schema. Central hashes the canonical UTF-8 JSON
`{action,series_id,request}` itself; callers cannot choose a replacement digest.

The dry job must have `production_writes:false`, no business claim or proof, and
finish centrally with a bound output inventory containing `response.json`.
That response must report `ok:true`, matching `worker_binding` and a result with
`action`, `series_id`, `mode:"dry-run"`, `ready:true`, `verified:false`,
`request_sha256`, `semantic_sha256`, and the exact `semantic` confirmation object.

An execution job needs `production_writes:true`, top-level `dry_run_job_id`, and
`request.json.dry_run` containing the accepted dry job's `job_id`, `source_sha`,
`manifest_sha256`, `input_sha256`, `request_sha256`, `semantic_sha256`, `semantic`,
`ready:true`, `action` and `series_id`. The queue verifies that proof against its
own completed same-producer receipt and hash-verified inventory/response, checks
the exact source commit/archive and original request, then creates the job and
business claim atomically. A missing, consumed, mismatched, unsuccessful or
unknown dry-run result cannot authorize execution. The browser independently
compares the live confirmation details with the accepted semantic object before
its final submission.

Required business keys are derived from the immutable request:
`agc:bushi-series-apply:<seriesId>` is shared across routine/manual producers;
`agc:bushi-series-modify:<seriesId>:<request_sha256>` identifies one exact change.
Changing producer, request key or source cannot repeat the same business claim.
Different reviewed criteria changes require their own accepted dry runs.
These claims use the existing schema-4 table and survive result retention.
Keep source/namespace configuration coordinated on rollback; the earlier
schema-4 worker cannot accept the required `dry_run_job_id` manifest field.
