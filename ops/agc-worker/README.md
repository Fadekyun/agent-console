# AGC laptop worker (#203)

This is the laptop half of the central durable queue. It is an independent
Windows standard account (`agc-worker`) with its own WSL distribution
(`AGC-Worker`), a hidden noninteractive task, and a single-slot Linux supervisor.
Source is reviewed and installed by the root coordinator. Provisioning leaves an
operator hold in place; creating an account is not acceptance of any workload.

## Isolation and fixed limits

Microsoft's [WSLg architecture](https://github.com/microsoft/wslg#user-distro)
states that different Windows users have different WSL VMs. Therefore this
account's `.wslconfig` does not change the existing `store-admin` Ubuntu VM.
[WSL configuration documentation](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)
describes the per-Windows-user file and the VM-wide CPU/memory settings. We never
use `wsl --shutdown`, alter a staff profile, borrow browser cookies, or install
workloads in the staff distribution. WSL integration settings are not a sandbox
for hostile code: only reviewed source and fixed adapters may execute here.

* Guest maximum: 14,999,879,680 bytes (14305 MiB), 12 logical CPUs, zero swap,
  WSLg off, gradual reclaim. Windows RAM budget is 16,000,000,000 bytes including
  the guest and overhead. The host watchdog conservatively counts **all** WSL
  VM private commitment plus its own process; this can throttle earlier when
  staff WSL is busy. Host pressure stops the worker; memory is never reserved
  empty. This is a WSL guest hard cap plus a sampled Windows aggregate guard,
  not an atomic cross-OS Windows job-object memory guarantee.
* Jobs slice: 14.5 GB memory maximum, no swap, at most 12 CPU equivalents,
  low CPU/I/O weights. Individual adapters initially use 1 CPU/2 GiB for browser
  or data jobs and up to 12 CPUs/8 GiB for approved builds. One job at a time.
  No increase after OOM; the central queue requires explicit reconciliation.
* Disk: hard NTFS quota 300,000,000,000 bytes for the worker SID, dynamic 260 GiB
  VHD maximum. This does not allocate the space. Both staging and running check
  that at least 15% of C: remains free. Staff quota defaults are preserved;
  enforcement is refused if it would activate finite limits on other users.
* Wednesday–Sunday, AC only, Singapore time; Sunday 23:45 drain. No wake timer
  or power-on change. The startup task exits before WSL on Monday/Tuesday or
  battery. A five-minute task retry during permitted days allows recovery after
  AC reconnect or staff pressure; it never overlaps an existing watchdog. Two
  minutes startup and three healthy minutes precede admission.
* CPU over 85% or available memory below max(8 GiB, 25% of host RAM) reduces
  the jobs slice to one CPU equivalent. Sustained memory pressure requests a
  checkpoint immediately, stops the job at 60 seconds, and stops only the worker
  VM at 75 seconds to reclaim cache. Below 5 GB, stale telemetry, battery, disk
  pressure or schedule closure stops immediately. New jobs additionally reserve
  their full memory allowance plus the staff reserve. Samples are every five
  seconds and expire at 30.
* GPU is **unqualified and disabled for admission**. Existing AMD `/dev/dxg` is
  evidence of a device, not a functioning compute backend. Any future GPU
  qualification must account shared GPU allocations in the same 16 GB Windows
  budget and yield to staff. The five initial workloads do not require a GPU.

## Provisioning, in the existing administrator SSH context

Stage this directory and an independently verified Ubuntu 24.04 WSL rootfs in an
administrator-only directory. Generate a **new** dedicated controller SSH key on
CT115 (private mode 0600); only its public key goes to Windows. The provisioner
never reads, copies or replaces the existing controller or staff private keys.

```powershell
.\Provision-Worker.ps1 -Phase Account -PublicKeyFile C:\approved-stage\worker.pub
.\Provision-Worker.ps1 -Phase Import -RootfsFile C:\approved-stage\ubuntu.wsl -RootfsSha256 <verified-sha256>
```

`Account` creates only this nonadministrator account, adds it to builtin Users,
denies interactive/RDP logon for its SID and hides its own login tile. Random
task credentials are encrypted with Windows **LocalMachine DPAPI**, protected by
an Administrators/SYSTEM-only directory. This works with key-only administrator
SSH, where CurrentUser DPAPI is unavailable. No credentials appear in output.
The rootfs is charged to the worker SID, and its imported VHD owner is verified.

Account mutations are idempotent with an ownership marker. A failure immediately
after account creation may require `-RecoverAccountSid <exact observed SID>`;
recovery is accepted only for that disabled account with the exact worker
description. It cannot adopt an unrelated account. Quota and SSH before-state,
source digests and a held-state receipt remain under the protected `control`
directory. Existing SSH directives must match the inspected baseline; the only
allowlist addition is `agc-worker`. A scoped `Match User` uses the existing port
22 with key-only authentication, a CT115 source restriction, no PTY/forwarding,
and fixed `Dispatch-Worker.ps1`. No firewall or Tailscale policy changes occur.

`Import` starts a hidden limited task under the worker account. Check its
`data/distro-ready.json` and task result before proceeding. The official image
must contain Python 3 and systemd. `Initialize-Distro.ps1` enables systemd only
in the new distro and terminates only that distro once to apply the setting.
Package/dependency installation is a separate serialized operation, not hidden
inside this bootstrap. The imported rootfs copy is removed after success.

Install reviewed adapters, pinned runtimes and browsers, then:

```powershell
.\Provision-Worker.ps1 -Phase Runtime
```

The task starts the supervisor while held. Before removing `/etc/agc-worker/hold`,
the root coordinator must independently verify:

1. Worker standard SID, task noninteractive/limited, quota enforcement and VHD
   ownership; existing staff `.wslconfig`, desktop, SSH and casting unchanged.
2. Distinct worker/staff Linux boot IDs, worker CPU count 12, memory below 15 GB,
   swap zero; cgroup2 slice and per-job memory/CPU/task enforcement.
3. Fresh standard-user host telemetry, 15% free disk, source-pinned adapters and
   exact Debian/Node/pnpm toolchains where required.
4. Lease loss, OOM, timeout, forced power interruption, duplicate start and stale
   completion produce the appropriate durable receipts and fenced outcomes.

Record that evidence and create the administrator-owned
`code/policy-verified.json` with `worker_sid`, `cgroup_verified:true`,
`quota_verified:true`, `separate_vm_verified:true`, `gpu_enabled:false`. The task
only reads this marker; absence means `policy_verified:false`. Release the
Linux hold only after these checks. Keep the central operator hold until its
independent acceptance checks and per-workload cutover are complete.

## Adapter and artifact contract

`/etc/agc-worker/adapters.json` starts empty. An enabled entry is root-owned:

```json
{
  "agc.build": {
    "enabled": true,
    "argv": ["/usr/local/lib/agc-worker-adapters/agc/scripts/compute/adapter.py"],
    "parameter_keys": ["storefront", "public_env"],
    "production_writes": false,
    "memory_bytes": 8589934592,
    "cpus": 12,
    "timeout_seconds": 3600,
    "root_directory": "/var/lib/agc-worker/toolchains/debian13"
  }
}
```

Exact registry names include `yuyutei.collect`, `yuyutei.parse`, `agc.build`,
`agc.test`, `tv.build`, `points.weekly`, `points.player_events` and individually
approved Bushi operations. Register only deployed and qualified adapters. A
fixed executable receives `--manifest`, `--workspace`, `--output`; callers
cannot choose argv, environment, mount paths, executable or UID.

A fixed `root_directory` must be a root-owned, non-writable immediate child of
`/var/lib/agc-worker/toolchains`, with root-owned
`.agc-worker-toolchain.json` (`version:1`) backed by the coordinator's verified
toolchain receipt. systemd binds only the current job, approved adapter runtime
and resolver configuration. The job still runs as unprivileged `agcjob`, with
no new privileges, private devices, low weights and its cgroup limits. TV uses
the approved Debian 12/glibc 2.36 root and AGC the Debian 13 toolchain; Ubuntu's
glibc is not silently substituted for either release build contract.

Manifest: `version:1`, `kind`, `source_sha`, `input_sha256`,
`source_artifact:{sha256,size}`, optional `inputs_artifact:{sha256,size}`,
`parameters`, boolean `production_writes`. Canonical hash uses sorted compact
UTF-8 JSON, without ASCII escaping. Tar files extract to `workspace/source` and
`workspace/inputs`; Points reads `workspace/inputs/input.json`. Symlinks,
hardlinks, devices, traversal, duplicates and expansion above 40 GB are rejected.
The supervisor enriches the on-disk manifest with `worker_binding` and
`worker_lease_file`; it does not change the original canonical input digest.

Child output is immutable only after its cgroup is empty. A result inventory
contains `version:1`, full `binding`, and `files:[{path,sha256,size}]`, at most
4096 files/2 MiB. The terminal receipt's `result.inventory_artifact` identifies
that inventory. Central acceptance must fetch and verify every listed object
before publishing. Failed/interrupted output may contain a checkpoint but can
never become a successful publication. Domain completion/coverage validation
is additionally required by each central publisher.

## Fixed RPC and interruption behavior

CT115 invokes SSH command `agc-worker rpc`; Windows ForceCommand ignores caller
commands and accepts one JSON line, up to 512 KiB. `protocol_version:1` and one of
`status/start/heartbeat/inspect/stop/stage/fetch/ack` is required. No remote shell or
SFTP is exposed for this account. Every job action binds `job_id`, `attempt_id`,
`lease_generation`, `kind`, `source_sha`, `input_sha256`, `manifest_sha256`.
Responses have `ok`, a nested `binding`, and a fixed error code on rejection.

Stage request: `artifact_id` (SHA256), `size`, `offset`, base64 `data` (256 KiB
decoded maximum). Reply: accepted offset and `complete`. Offset mismatch returns
the already stored offset; final content must match the digest. Fetch adds
bounded `length`; response includes `data`, `size`, `offset`, `eof`. Object IDs
never become caller-controlled paths.

After central durable archival of every object, `ack` with the exact binding
and `inventory_sha256` records archival and removes only that attempt's scratch
workspace. Unknown outcomes cannot be acknowledged for automatic cleanup.
Receipts, inventories and bulk object cache remain; future cache cleanup must
preserve unacknowledged dependencies. Every object is hash checked, fsynced and
atomically renamed before a receipt refers to it. Existing cache objects are
revalidated before a stage response can report completion.

Heartbeats every 15 seconds; at 60 seconds without one, a checkpoint-capable
adapter receives SIGUSR1. At 90 seconds systemd stops its whole cgroup (10 second
kill grace), ahead of the central 120-second lease. A new daemon boot stops old
units and records unknown outcomes. Generation fences survive restart. Duplicate
starts return the stored receipt. Unknown external writes require readback and
operator reconciliation; termination proof does not prove a write never occurred.

## Rollback and remaining qualification

Hold/drain the central queue first. `Provision-Worker.ps1 -Phase Disable`
disables only the two worker tasks and account and preserves distro, outputs,
receipts and staff settings. Existing leases expire locally. Do not unregister
the distro or delete receipts while outcomes remain unknown. Quota enforcement
is retained on rollback so worker data cannot escape its cap; use the recorded
before-state only after confirming no intervening administrator quota changes.
Never restore a whole historical SSH file over newer unrelated edits.

Source verification: lightweight Python fault tests and installed PowerShell
parser/C# declaration compilation. These do **not** qualify Windows scheduled
task behavior, WSL launch/termination, enforced job limits, desktop impact or
real workloads. The root coordinator owns those live checks and production
cutovers. GPU execution remains unqualified. Automatic bulk cache retention is
not yet implemented; full disks hold new work rather than discard unacknowledged
output. Acknowledged build scratch is reclaimed immediately.
