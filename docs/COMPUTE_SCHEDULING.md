# Compute scheduling: first source slice (#181)

This release adds central durable compute records, conservative resource admission,
and a small read-only resource report. It preserves Proxmox, the existing LXCs,
persistent applications, and interactive Console sessions. It does not install a
laptop worker or move a scraper, build, deployment, or production publisher.

## Release boundary

Dispatch is **disabled by default**. The web service starts the ten-second compute
sweep only when its reviewed environment sets `AGENT_CONSOLE_COMPUTE_ENABLED=1`.
The persistent operator hold starts **on** independently of that flag. The queue,
receipts and status page are available without enabling dispatch. Neither an API
request nor a queued job can change the environment flag or enable laptop profiles.
Canary mode does not start the dispatcher.

Integration update (2026-10-10): the deployed private Console is already 0.29.1,
with native-history recovery and host launch admission absent from public main.
This source candidate is 0.30.0. Root must retain/integrate those private changes
before any release; deploying this PR's public-main tree directly would regress
the live recovery/admission behavior. This change does not authorize such a switch.

The only executable adapter, `maintenance.report`, returns a compact allowlisted
copy of trusted resource telemetry in process. It opens no browser, runs no job
subprocess, takes no arbitrary command, and performs no production writes. Its
resource envelope is an admission reservation; this release does **not** claim
per-report cgroup enforcement. Hard cgroup containment is a prerequisite for adding
any subprocess adapter. Submitted source/input SHA-256 digests are immutable
caller declarations in this slice, not claims that a checkout or artifact has been
fetched or verified. The report does not consume those artifacts.

This queue does not yet govern unrelated interactive agents, existing timers,
Docker processes, or manually launched builds. Existing service rebudgeting and
integration of all heavy launch paths remain necessary to prevent host-wide OOM.
Do not consider this source slice alone a completed live OOM fix.

## Durable records and recovery

Additive `compute_*` tables live beside workflows/results/events in
`connected-work.sqlite3`. The separate compute schema gate is version 1; an unknown
version fails closed. The interactive session schema is unchanged. Compute attempts
have their own IDs rather than creating fake tmux sessions. Existing workflow events
record queue, reservation, hold, completion, retry and reconciliation actions.

Each job has a unique idempotency key and immutable profile, execution target,
source digest and input digest. Reusing a key with different inputs is rejected.
`execution_target` identifies compute placement; it is not the deployment target.
Each attempt has a monotonically increasing generation, a 120-second lease,
heartbeat timestamp, optional checkpoint SHA-256 and hashed result receipt.

Admission and reservation use one SQLite `BEGIN IMMEDIATE` transaction. A unique
physical-host reservation allows only one attempt on `n100`, regardless of guest
identity. Starting, running and unknown attempts retain their reservation. Expiry
marks an attempt `unknown` and the job `reconciliation_required`; it does not imply
that the process died. Late results and heartbeats are rejected. The in-process
report normally completes in one sweep; an unexpected exception retains the receipt
and reservation for inspection. Service restarts do not replay it.

An owner can reconcile an unknown attempt only by recording termination evidence.
For this adapter, verify that the original report invocation/service process has
exited; merely seeing an expired lease or failed connection is insufficient. This
records the owner's attestation, not an automatic termination detector. It marks
the attempt failed and releases capacity; it does not fabricate a successful result.
A retry then requires confirmed failure, a five-minute first or thirty-minute second
backoff, and a new generation. Maximum: one original attempt plus two retries.
There is no automatic retry loop. An unchanged OOM budget cannot be retried; exit
137 alone is not accepted as an OOM classification. Queued/blocked jobs can be
cancelled; running/unknown reservations cannot be removed by cancellation.

No production publication endpoint exists. Future publication must use a central
transactional idempotency receipt and a distinct reconciliation state. Uncertain
production writes must never enter ordinary compute retry.

## Capacity policy

`maintenance.report` reserves 0.25 CPU, 512 MiB maximum RAM, 256 MiB scratch and
one lane; its declared memory-high is 384 MiB, swap zero, PID ceiling 64 and timeout
300 seconds. These declarations are fixed in source and cannot be expanded by jobs.

The trusted sampler verifies `n100`, CT115's current (not pending) Proxmox memory
ceiling and `local-lvm:vm-115-disk-0` storage, the `pve/data` thin pool, the local
guest hostname and cgroup v2 hierarchy. It combines guest `MemAvailable`, visible
ancestor limits and the outer LXC memory cap. A state directory on another device
is unsupported until its storage mapping is reviewed. Fixed, pinned-host-key SSH
probes use batch mode, no forwarding, no environment forwarding, a twelve-second
timeout and bounded output. Missing privilege, identity or telemetry fails closed;
raw host command output and credentials are not exposed in status.

Admission requires all of:

- The existing root-owned host snapshot at `/run/agent-console-host-resources.json`
  (or `AGENT_CONSOLE_RESOURCE_SNAPSHOT`). The queue honors its maintenance flag,
  RAM/pressure/disk/temp-space vetoes and sixty-second freshness limit before any
  host probe. Missing, malformed, symlinked, non-root-owned or group/world-writable
  snapshots fail closed, with no SSH fallback around the shared gate. A veto resets
  warm-up. This keeps the host maintenance controller authoritative; the detailed
  compute checks below remain stricter. No snapshot producer or live launch setting
  is changed by this source update.
- An enabled dispatcher, released persistent hold and fresh telemetry (at most 30
  seconds old). Five minutes of continuously fresh healthy observations are needed;
  restart, hold, clock discontinuity, stale observations or pressure reset warm-up.
- Verified actual total CPU/RAM. All active/unknown reservations count against the
  physical host; allocations across LXCs are not independent physical capacity.
- Host available RAM at least 4 GiB, with at least 2 GiB remaining after reservations
  and the new attempt. Guest headroom must leave another 512 MiB. Reservation
  subtraction is deliberately conservative even if RSS already reduced availability.
- Projected guest filesystem free space at least 6 GiB after reserved scratch.
  Thin-pool data must be below 80% and metadata below 70%.
- Host memory full PSI average60 below 1%, and I/O some average60 below 10%.

During implementation the live read-only probe found 2,898,460,672 bytes (2.70 GiB)
free in CT115 and thin-pool data at 89.87% (metadata 3.55%). Both violate admission
limits; live dispatch must stay blocked until both storage conditions recover.
No cleanup is included in this change. Root's heavy-work/Console-hunt coordination uses the persistent
hold; unrelated launch paths do not yet acquire this reservation automatically.

The Oct 5 readings above are historical. On Oct 10, prior approved host maintenance
had already restored roughly 10.8 GiB of CT115 free space and reduced pressure.
An additional run of the existing bounded storage-maintenance service returned
freed blocks and reduced thin-pool usage from 81.82% to 80.29%; it found download
caches within budget and deleted none. A bounded root-only trim of CT101 then
reduced pool usage to 79.64%. No files were deleted or services restarted by these
trims. The 80% compute threshold is unchanged.
Recheck current values before admission; never infer present capacity from this note.

## Laptop descriptor and later enrollment

Verified inventory recorded by the approved plan: Windows 11 Pro, Ryzen 7 PRO 7730U,
8 cores/16 logical CPUs, 32 GiB installed RAM and 33,066,012,672 bytes usable by
Windows. Existing staff WSL and desktop applications are preserved. These static
inventory facts are descriptive, never accepted as fresh admission telemetry.

The laptop descriptor and all laptop profiles are disabled. The provisional
16,000,000,000-byte limit is a maximum reclaimable budget including VM overhead,
not pinned or guaranteed available RAM. Owner background/RAM preference is still
an enrollment prerequisite. Laptop jobs remain durable centrally while it is off.
Availability is Wednesday through Sunday in Asia/Singapore, with new admission
closed Sunday at 23:45. Monday/Tuesday remain unavailable even if Windows is awake.
No wake, reboot, shutdown, WSL configuration or power schedule action is implemented.

| Pending adapter | CPU | RAM high/max | Scratch |
|---|---:|---:|---:|
| `yuyutei.collect`, `yuyutei.parse` | 1 | 1.5/2 GiB | 8 GiB |
| `agc.build`, `agc.test` | 2 | 3/4 GiB | 12 GiB |

Enabling these profiles in source alone is insufficient: the dispatcher rejects
any profile without a verified adapter. The next separately reviewed slice needs
an independent nonadministrator worker account and WSL distribution, a restricted
authenticated connection, headless browser profiles without staff cookies, actual
cgroup/VM/disk limits and Windows staff-priority pressure handling. One job at a
time; preserve at least max(8 GiB, 25% usable RAM) for Windows/staff and at least
1 GiB within the worker VM for overhead. Verify reclamation after checkpoint/exit;
freezing a process does not reclaim its RAM. No Windows focus or desktop changes.

Future remote attempts need 15-second heartbeats, checkpoint at 60 seconds without
renewal and termination by 90 seconds, ahead of the central 120-second lease. Add
warm-up, AC checks, Sunday drain, staff CPU/disk throttling and low-memory exit
before enabling remote jobs. Yuyutei must retain sequential 45–90-second request
spacing, durable per-page checkpoints and immutable hashes. Incomplete/stale
scrapes cannot publish; central DB and Sheets publication need separate receipts
and reconciliation for uncertain writes. Existing timers remain in place until
that migration's acceptance checks pass.

## Operator surface

Open `/compute` for a read-only status page: worker availability, queue totals,
latest 100 jobs, blocked reasons and retained reservations. `/api/compute` and
`/api/compute/jobs/{id}` expose the same receipts. All compute HTTP routes require
the configured authenticated owner through the trusted proxy. Headerless LAN
identity and managed-agent capability headers do not grant compute authority.
Mutating browser requests also require the exact allowed Origin. This uses the
Console's existing trusted-proxy boundary; it is not OS-level isolation between
processes sharing the Console account.

The local human-owner CLI rejects managed-session markers before accessing state:

```text
agentctl compute status
agentctl compute enqueue --json-file /path/to/reviewed-job.json
agentctl compute inspect COMPUTE_JOB_ID
agentctl compute hold --json-file /path/to/hold.json
agentctl compute retry COMPUTE_JOB_ID
agentctl compute cancel COMPUTE_JOB_ID
agentctl compute reconcile COMPUTE_ATTEMPT_ID --json-file /path/to/evidence.json
```

The enqueue object has exactly `kind`, `execution_target`, `source_digest`,
`input_digest` and `request_key`; digests are lowercase 64-character SHA-256 strings.
Hold objects are `{"held":true}` or `{"held":false}`. Reconciliation requires a
nonempty `{"evidence":"..."}` object. Equivalent POST API routes are `/api/compute/jobs`,
`/api/compute/hold`, `/api/compute/jobs/{id}/retry`, `/cancel`, and
`/api/compute/attempts/{id}/reconcile`; retry/cancel accept an empty JSON object.
There are no HTTP enable, dispatch, telemetry-upload or publication routes.

`agentctl compute tick` performs one owner-invoked sweep with the same flag, hold
and admission gates. A new CLI process resets warm-up, so repeated one-shot commands
do not bypass the five-minute observation window. Normal dispatch uses the persistent
web-service sampler. Do not strip managed markers to use these owner commands from
an agent session.

## Acceptance and rollback

Focused temporary-fixture tests cover concurrent dispatchers, restart/unknown
receipts, stale generations, immutable inputs, retry/OOM policy, resource-pressure
boundaries, disabled laptop schedules, bounded telemetry and authenticated APIs.
No heavy build, browser, live job or production mutation is needed for this slice.

For root-managed deployment: preserve a consistent backup of the companion store,
deploy through normal exact-source release checks with dispatch still disabled,
verify authenticated status and a queued offline laptop job, then separately review
enabling the report sampler. Verify current headroom and the dedicated read-only
probe authority before releasing the hold. A synthetic canary with tiny private
fixtures verifies dispatch; do not weaken production thresholds just to make it run.
The status UI is covered by response/static checks, not browser rendering tests.

Rollback: hold admission, set `AGENT_CONSOLE_COMPUTE_ENABLED=0`, and use the normal
reviewed release rollback. Preserve the full companion database including WAL via
SQLite backup/normal service shutdown; do not delete compute tables or reservations.
The prior release ignores additive compute tables. Returning to this release will
expire unresolved receipts into unknown and require reconciliation. A rollback
cannot turn uncertain completion into permission to replay work.
