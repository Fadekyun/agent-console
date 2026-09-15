# Fixed AGC laptop presence — issue100, source only

This disabled source adds fixed POST /api/integration/device-presence/agc-laptop
and identity-protected GET /api/device-presence/agc-laptop plus a minimal AGC
project card. It creates no task/session/attention/model operation and does not
change SQLite schema. Presence describes Tailscale reachability, not kiosk health.
Project proj-7e8a68b0c8444fa9b97276ef9437a65d, device ngF3guMi1g11CNTRL and sender-owned
Discord channel1488531129777655985 remain fixed. Sender command is !agclaptop.

POST accepts exactly three fields device_id/state/observed_at. State is online, offline or
unknown; UTC timestamps omit zero fractional seconds or use exactly six digits.
Canonical sorted compact UTF8 and seven-field ACK match the unchanged vectors
in tests/fixtures/presence-wire-vectors.json, SHA256
ee69910c87961a7133fa66829102c7080fa71deead7c65cec761384ef8723bb3.
ACK fields are accepted,duplicate,payload_sha256,state,observed_at,received_at,
expires_at. Duplicate ACK changes only duplicate=true; receipt and original
wall/monotonic expiry never renew. Strict0<=age<180; future rejects. Same-timestamp
conflicts/stale ordering and previous-lifetime duplicate reject409. GET shows
Online/Offline only while fresh, otherwise Status unavailable, and writes nothing. A newer unknown is accepted,
ordered and ACKed privately, superseding earlier positive evidence. GET omits
stable device identity, IP and topology; only fixed project/status/times/meaning
are displayed.

## Authoritative startup and private ordering

presence_server is an explicit opt-in web MASTER entrypoint. It starts exactly
one receiver child with spawn before calling uvicorn and supplies one random
generation to every worker. The receiver owns an exclusive lifetime flock and
one in-memory record; web app factories/workers cannot create it or reset it.
Missing/dead authority, wrong generation or duplicate master startup fails closed.
The private receiver serializes requests; backlog8, input deadline1s/4096bytes,
response bound and client1.5s budget. HTTP forwarding admits at most8 in-flight
calls per worker, with slots retained until shielded worker completion even if
HTTP caller cancels. No threaded process-global subreaper or remote command.

Only timestamp+digest ordering is atomically persisted and fsynced; no receipt or
freshness is restored on restart. A current-lifetime observation must be newer
than durable ordering and observed at/after authoritative startup. Disk failure
latches unavailable without success ACK. Clock samples use measured intervals,
5ms+1000ppm slew allowance and <=250ms sample interval, not ideal1microsecond
jitter. Rollback/inconsistent sample latches Unknown for this lifetime. Original
monotonic deadline independently expires both GET and duplicate POST. Receiver
samples while idle too. Process pause/platform clock behavior remains a live
proof requirement; this is a trusted local service, not a hostile-code sandbox.

Private directory must already exist, owned by service UID,0700, without symlink
path components. writer.sha256 is the separately provisioned verifier (hex SHA256
of dedicated writer token), regular/no-follow/single-link/owned0600. No token or
verifier is installed by this source. File descriptors are checked before bounded
reads; FIFO/symlink/changed-file refusal. Atomic ordering writes fsync file and
directory. Socket0600 and kernel SO_PEERCRED require same service UID. Launcher
provisioning, root-owned imports and actual service credential views need their
own reviewed operational scope.

## Scoped identity and proposed startup interface

X-AGC-Presence-Writer is accepted ONLY on the fixed POST. A request bearing it on
any other HTTP route or WebSocket is refused, even from trusted LAN. It cannot
substitute for the normal GET identity policy. Presence GET mirrors existing
Tailscale/allowed-LAN identity policy without audit/database writes. Generic
Console startup still constructs its existing manager; neither presence route
calls it. Isolated receiver app fixtures do not construct a SessionManager.

Activation would require explicit AGCONSOLE_DEVICE_PRESENCE=1 and private
AGCONSOLE_DEVICE_PRESENCE_DIR plus reviewed service startup through:
python -m agent_console.presence_server --host HOST --port PORT --workers N
(1..8 workers). This is a source interface, NOT an instruction to change the live
service. Do not set AGCONSOLE_PRESENCE_GENERATION by hand; launcher owns it. Existing
uvicorn startup remains unchanged and cannot lazily launch a receiver. If a master
is SIGKILLed, a surviving authority can make a subsequent master fail its lock;
report/clean only the verified owned orphan under a separate operational scope,
never delete a live socket or restore old freshness. Graceful exit terminates and
joins its own authority child. No generic helper/service installer is introduced.

## Validation and release boundaries

Focused tests exercise unchanged ACK vectors, strict parsing/future/TTL/clock
rollback, durable failure/no read writes, FIFO/symlink refusal, real spawned
singleton/restart and concurrent-client duplicate ordering, and route isolation.
They use dummy credentials and disposable directories only. Focused validation:18 tests and2 subtests PASS, plus AST/browser-module syntax
and unchanged vector hash. The master launcher fixture runs the actual receiver
child but substitutes uvicorn.run; web-worker factories use TestClient, not a
real multiworker TCP deployment. Independent review is still required before
publication. Actual UID/provisioning, fixed
project identity (API verified AGC in this source slice), multiworker host startup/shutdown and authenticated
n8n E2E/import/bot ACL remain separate gates. No live endpoint/token/host change,
workflow activation, original strict24h waiver or native/laptop enablement follows.

Base128dc491 is deployed alias98/v0.4.1. This API addition is0.5.0 and merges AFTER
alias98; do not edit or republish that frozen candidate. Rollback of this future
source slice requires disabling scoped ingress and preserving ordering files;
never restore old ordering over accepted observations. A web/receiver restart
must show unavailable until a newer observation. Current alias98 forward-only
operational recovery remains separate.

The opted-in master completes normal state-directory, auth-registry and guarded database initialization before spawning the authority or web workers. Fresh WAL/schema creation cannot race worker imports. Bootstrap errors abort startup before children exist; ordinary web startup and worker schema guards are unchanged.
