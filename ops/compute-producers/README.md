# Scoped producer enrollment (#203)

`provision.py` installs only the seven fixed workload identities listed in `SPECS`.
Run its client phase as root on the named source host and its server phase as root
on CT115. The JSON stdin carries reviewed helper bytes/base64 plus their SHA256;
server enrollment additionally receives only the generated public key. Private
keys stay on their originating host and Bearer tokens stay on CT115. A rerun
preserves identity and rejects changed existing helper/policy bytes.

The CT115 host key was independently read through N100/Proxmox before enrollment.
A changed host key requires inspection and a reviewed source update. Clients use
root-owned configuration at `/etc/agc-compute/producers/NAME/client.json` and the
root-installed helper `/opt/agent-console-producer/compute_producer_bridge.py`.
Python controllers use root-only keys; existing unprivileged JavaScript services
read their own root-owned group-readable key. All remote keys have fixed source
addresses, fixed commands and no forwarding, PTY, user rc or password authority.
They cannot manipulate owner holds, retries, reconciliation or other jobs.

CT115 uses `ssh.socket`; restarting its listener is required to retain inherited
socket activation. SIGHUP/reload caused a port-binding failure during enrollment
and was recovered without changing the socket or established sessions. The
installer now validates first, restarts only `ssh.service`, and checks active state.
No listener, firewall, Tailscale grant or staff account is added.

Install `AGENT_CONSOLE_COMPUTE_PRODUCERS_CONFIG=/etc/agent-console/compute/producers.json`
in the existing runtime configuration, then restart only Console's web service.
Enrollment leaves compute dispatch and the durable owner hold unchanged. Test with
a read-only missing-job lookup: a scoped application 400 shows authenticated
transport; a 403 means identity has not been admitted. No production job is implied.
Rollback disables the corresponding producer policy/revokes its single public key;
retain journals, keys and ambiguous outcomes for reconciliation.
