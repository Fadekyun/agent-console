# AGC laptop worker (#203)

This is the laptop half of the central durable queue. It is an independent
Windows standard account (`agc-worker`) with its own WSL distribution
(`AGC-Worker`), a hidden noninteractive task, and a single-slot Linux supervisor.
Source is reviewed and installed by the root coordinator. Provisioning leaves an
operator hold in place; creating an account is not acceptance of any workload.

## Per-job filesystem visibility

The daemon and its `jobs` parent remain root-only 0700. Each job root is explicitly
root:the job user's primary group 0710; its workspace/output belong to that job
user. The production daemon's 0077 umask is preserved. Systemd maps only that
one job read-only at `/run/agc-job` in the unit's mount namespace, then mounts
`workspace` and `output` writable below it. WorkingDirectory, all three adapter
arguments, HOME/TMPDIR/cache variables and `worker_lease_file` use those fixed
unit-visible paths. The same mapping applies to native CT102 jobs and laptop
RootDirectory toolchains; the host state directory is never a bind source.

Manifest/lease files are root:job-group 0640. Atomic heartbeat replacements get
their mode/group before rename, and the whole job-directory bind makes each
replacement visible without letting job code replace the supervisor metadata.
This follows systemd's [unit-specific bind mount contract](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml):
with RootDirectory, sources remain host paths and destinations are inside the
unit's root. Mounts are not exposed through the host's `/run/agc-job` path.

Hold and drain before upgrading. `qualify-native.sh` and `qualify-toolchains.sh`
now create their actual fixtures under 0077. The isolated native fixture checks
real CHDIR, mapped paths, lease replacement visibility, denied metadata writes,
denied host/sibling reads and no host-namespace mount exposure. It grants no
central lease and does not submit a production job; its isolated systemd/lease
fixture is not queue admission evidence. Runtime acceptance must be recorded on
each reachable target before its hold is released. Do not broaden host parent
permissions to make an old qualifier pass.

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
denies interactive/RDP logon for its SID, grants that SID batch logon for its
hidden tasks, and hides its own login tile. Random
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
adapter receives SIGUSR1. The independent Python watchdog begins stopping at
75 seconds, with a five-second kill grace. A separate root-owned systemd timer
kills the exact job cgroup after 80 seconds without a renewed heartbeat, even
if the daemon stalls. Both paths must prove termination within 90 seconds,
ahead of the central 120-second lease. Job code cannot renew the timer. RPC
input has a total deadline, and transfers do not own the watchdog state lock.
A new daemon boot stops old
units and records unknown outcomes. Generation fences survive restart. Duplicate
starts return the stored receipt. Unknown external writes require readback and
operator reconciliation; termination proof does not prove a write never occurred.

## Rollback and remaining qualification

Hold/drain the central queue first. `Provision-Worker.ps1 -Phase Disable`
disables the worker background/bootstrap/setup tasks, then runs a dedicated
limited-account stop task. It terminates only `AGC-Worker`, confirms the distro is absent from that user's
running list, and only then disables the account. A failed or timed-out stop
retains an unknown receipt and does not claim shutdown; the account stays
enabled with automatic tasks disabled for operator recovery. The stop never
starts a stopped distro, including on Monday/Tuesday: it uses direct termination
and never `wsl -d --exec`, which could race a concurrent stop and restart WSL.
Distro, outputs, receipts
and staff settings remain intact. Central reservations still require outcome
reconciliation; VM termination does not prove an external write failed. Do not unregister
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

`qualify-native.sh` is an operator-only qualification script for the installed
worker core. Run it through `Setup-Worker.ps1` with a reviewed SHA and a 360-second
timeout after toolchain work finishes. It requires the production worker held
and drained, creates its own registry/state and cgroup slice, and runs five jobs
sequentially: success/duplicate, real 64 MiB OOM, timeout with a child process,
lease loss with checkpoint and unknown-write classification, and a separate
timer case with the Python watchdog deliberately idle. The latter two require
actual whole-group termination within the 90-second budget.
The adapter is local and harmless; the write flag only exercises classification.
Jobs use at most 128 MiB/one CPU and the test slice 256 MiB/one CPU. It permits
only the missing policy-verification marker and skips the isolated instance's
warmup; actual Windows telemetry, AC, schedule and headroom checks stay active.
This is **not** proof of production queue admission or real application behavior.
JSON evidence is retained in `/var/lib/agc-worker-qualification-results/`.
It stops only its recorded test units and slice; successful scratch is removed,
and failed scratch/receipts remain for investigation. No policy marker is set.

## Operator setup and per-adapter credentials

`Setup-Worker.ps1 -ScriptFile <reviewed-linux-script> -Sha256 <exact-hash>
-Label <operation>` runs only from the existing Windows administrator context.
It copies the reviewed script to administrator-owned storage and registers one
hidden limited-account task. The task checks schedule/AC/headroom, holds new
worker admissions, refuses an active job, verifies transferred bytes, and runs
only inside `AGC-Worker` with systemd limits: 2 GiB RAM, zero swap, one CPU,
low CPU/I/O weights, 256 tasks and a bounded timeout. Setup receipts and logs
live under `data/setup-<label>-<hash>.*`. It leaves the local hold in place.
Setup belongs to the worker jobs slice so watchdog shutdown also stops setup.
Physical SSD capacity/free space come from `GetDiskSpaceInformationW` Actual
fields; the Caller fields record the separate NTFS quota headroom. Standard
worker identity validation confirmed the physical 510,194,085,888-byte volume
and approximately 300 GB quota independently. Neither check uses DriveInfo's
quota-limited capacity as the physical staff reserve denominator.
The coordinator serializes this operation with other heavy work; do not put
credentials in setup scripts or their logs. No shell operation is added to the
worker's public SSH/RPC protocol. This helper cannot restart staff WSL.

Only the exact `bushi.run` or dedicated `bushi.emergency` registry entry may set
`credential_ids:["agc-bushi-organizer-state.json"]`. Its source must be a regular
root-owned file under root-only `/etc/agc-worker/credentials/`, mode0600 and at
most1MiB. systemd `LoadCredential` makes a private read-only copy for that unit;
the wrapper validates the trusted `CREDENTIALS_DIRECTORY` and fixed filename.
systemd can retain root ownership and grant the job UID read access via ACL, so
0440/0550 ACL-mask modes are legitimate; world access and nonowner write remain
forbidden. General builds receive no organizer credential. A Bushi domain
`write_unknown` result becomes an unknown worker receipt even if its process
exited successfully; artifacts remain available for reconciliation and cannot
be acknowledged for automatic cleanup.

### Pinned build roots

`install-toolchains.sh` is an operator setup script for the held worker. It pins
Debian 12/13 AMD64 OCI manifests, verifies the Node archives and the pnpm package,
and creates separate roots matching the TV/site ABI. The source image and package
inventory stay in each root's receipt. It creates only the worker's numeric Linux
identity; no staff account, home, key or browser data is copied.

The setup runs with 2 GiB RAM and one CPU, under the worker jobs slice. A failed
installation retains its `.debian12-setup` or `.debian13-setup` directory for
inspection and refuses an automatic restart. Reconcile that incomplete directory
before an explicit retry; never remove an installed root or its receipt to retry.
`check-installation.sh` reads the guest limits and cgroup configuration. Live
workload/interruption qualification is still required before admission.

## CT102 emergency policy

`emergency_worker.py` selects a separate root-installed policy and state/socket
directories. It reuses the binding, receipt, cgroup termination, artifact and
unknown-outcome behavior of `worker.py`. A job cannot select this policy itself.
The initial capability is only `bushi.emergency` paid participation: an explicit
urgent deadline within 20 minutes, canonical paid-order UUID, and exact
installed source revision/archive are mandatory. A supplied business key must
match that order; the central queue independently derives the same claim when
it is omitted. No input archive or uploaded
source is accepted. The fixed wrapper and dedicated organizer credential remain
root controlled. The adapter also checks the absolute deadline before submission.

`install-emergency.sh <worker-source-dir> <reviewed-source-tar> <tar-sha256>
<git-revision> <dedicated-public-key-file> <dedicated-credential-file>
<fresh-boundary-enrollment-file>` requires
root in CT102 (`bushi-automation`), an already installed reviewed adapter closure with
`.agc-worker-reviewed.json` containing `source_sha` and `wrapper_sha256`, and a
verified cgroup memory ceiling no greater than 2 GiB. Generate the final argument
with `enroll-emergency-boundary.py` on CT115 after any approved host cap change,
transfer its public JSON through the authenticated route to a root-owned file
under protected CT102 ancestors, and install within 300 seconds. The fixed probe
checks the actual N100 `/lxc/102` limit/current and CT102 init/mount mapping;
the visible `/lxc/102/ns` namespace root can legitimately have `memory.max=max`.
The paired sampler and worker require fresh outer usage matching the persisted
enrollment and combine it conservatively with local namespace headroom. A cold
worker reports unknown capacity and remains held until a valid sample arrives.
Missing enrollment or a changed cap fails closed; never set a synthetic visible
limit to bypass verification. See `docs/COMPUTE_REMOTE_WORKER.md` for the exact
seven-field enrollment and RPC schema. It installs no packages and
does not touch existing Bushi timers. The installer refuses unmanaged worker
accounts and existing enabled/active emergency policy. It creates only its
dedicated account, forced SSH dispatcher, systemd units, tmpfs and configuration.
SSH is limited to CT115's direct LAN source `192.168.1.115`, its dedicated key,
and fixed JSON RPC on the existing SSH port. Existing incompatible SSH access
rules cause installation to stop for operator review; no allowlist is widened.

The browser job has a 1 GiB RAM ceiling, zero swap, 50% CPU and at most 20 minutes
or its remaining deadline. The workspace/output mount is a 512 MiB tmpfs.
An aggregate slice caps browser plus supervisor CPU at 50% and RAM at 1280 MiB.
The durable receipts and verified output objects remain outside scratch.
Central host samples arrive through the fixed `host-sample` RPC every 15 seconds;
all fields are validated and samples older than 30 seconds or more than 5 seconds
in the future are refused. Local cgroup memory and scratch are sampled separately.
Start needs at least 4 GiB N100 available RAM and 512 MiB guest reserve after the
job reservation. Running work stops on N100 RAM below 2 GiB, memory full PSI >=1,
I/O some PSI >=10, thin-pool data >=80%, metadata >=70%, stale samples, or local
guest/scratch pressure. This policy runs on any day and never starts the laptop.

Installation leaves both `/etc/bushi-emergency/hold` and `policy.json` enabled=false.
`qualify-emergency-native.py` exercises the installed shared launcher under the
real 0077 umask using a private fixture and the emergency job identity. Keep
authenticated CT115 host samples flowing every 10–15 seconds during the run.
It checks a network-isolated local browser page, current-job mounts, inaccessible
fixture secrets and sibling state, atomic lease visibility, duplicate suppression,
and the independent 80-second kill timer. Real credential files are checked by
metadata only and are never loaded. Production policy and queue stay held; this
is native launcher evidence, not organizer or end-to-end workload acceptance.
Root must qualify the actual CT102 cgroup/source/credential behavior and global
organizer fence before explicitly enabling and removing the hold. The central
queue independently requires warmup and headroom. For rollback, hold the queue,
create the local hold, set enabled=false, stop `bushi-emergency-jobs.slice` and
`bushi-emergency.service`, then disable only that service. Preserve unknown
receipts, source and output objects until explicit outcome reconciliation; do
not unmount/delete scratch with an active or uncertain job. No live CT102
qualification is implied by the source tests.

### Reviewed adapter upgrades

`prepare-adapter-install.py --previous-install-receipt` validates the exact prior receipt, all installed file hashes and fixed dependency links before replacing a held, drained adapter tree. It retains the previous directory and fsyncs files/directories before success. The two directory renames and registry replacement are separate operations: an interrupted upgrade remains held and requires explicit reconciliation using the retained tree. Never unhold solely because setup stopped. `Verify-WorkerPolicy.ps1` records completed native failure/quota/isolation checks and preserves that hold.


TV release test support uses `install-tv-test-runtime.sh` through the existing
held setup task. It installs python3-venv only inside the Debian12 worker root;
the repository builder creates a disposable pinned Cast test environment.
`Provision-OrganizerCredential.ps1` accepts only the separately authorized
Bushi automation session on stdin. Its dedicated limited task installs a private
Linux credential while held. Verify its success receipt and remove
`code/organizer-input.json` and the one-time credential task after completion.
Never supply credential values in script arguments, transcripts or source.
