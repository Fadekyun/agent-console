# Exact-artifact review integration (#103)

This uses the existing managed admission, durable integration request table, native
Codex exec runner, receipts, timeout and ownership reconciliation. It does not grant
permissions or install a release. Existing plan-request/status contracts stay unchanged.

## Configuration and bundle

Provision `review-integration.json` alongside `plan-integration.json`, mode0600,
using the same strict schema and a separately generated host-local capability digest.
Set provider.tool to `codex-pro`, provider.auth_context to its ready named context,
project `agc` to the active AGC project ID, and context_root/context_dir to the private
review staging root. Owners/channels must contain only the fixed worker identities.
`provider_capability_verified` must remain false until the installed provider's exact
read-only native exec command is verified. No credentials belong in this repository.

Each `bundle_id` names a directory below projects.agc.context_dir, containing:

- `package.tar.gz`: complete release archive, maximum256MiB.
- `rollout.py`: complete self-contained deployment/rollback controller, maximum1MiB.
- `context/`: bounded textual evidence (maximum64 files,64KiB/file,256KiB total),
  including exact source revision, release diff, CI result, rollback and health policy.

Submit `agentctl integration review-request` with JSON on stdin:

```json
{"request_id":"20260914000000055","requester_id":"191524132624531458","channel_id":"1493588468884836402","project":"agc","text":"Review queued update55","review":{"source_sha":"<40 lowercase hex>","package_sha256":"<64 lowercase hex>","rollout_sha256":"<64 lowercase hex>","target":"shop-kiosk-01","bundle_id":"release-55"}}
```

The wrapper opens its private capability file as an inherited FD3..255 and sets
`AGENT_CONSOLE_RELAY_CAPABILITY_FD`; the capability must not appear in arguments,
JSON, logs, or n8n workflow data. Supply the same identity triple to
`agentctl integration review-status`. Persist request ID before submitting, retry
busy with the same payload, and never create replacement IDs after delivery_unknown.
A duplicate exact payload reuses the durable request; changed payload conflicts.

The actual archive and controller are copied and SHA256 checked before native launch.
They are rechecked before provider execution, after review, and when returning result.
Frozen context is read-only. The command pins codex-pro, gpt-6-astra, low,
read-only sandbox and approval_policy=never (cannot elevate). No terminal injection.

Status adds `review`, `session_id`, `model`, `reasoning_effort`, and terminal `result`:
`{outcome,summary,steps,verification,blockers,review}`. Outcomes are approved,
rejected,needs_input. Only `state=completed`, `result.outcome=approved`, no blockers,
matching exact binding, model,effort and retained session evidence can satisfy the
technical review gate. Rejected is a completed review, never approval. Unknown,
failed, needs_input, malformed and mismatched results must remain held.

## Installation and rollback

After independent review, package a new immutable Console release containing the
changed modules; follow the existing selected-release installer. Do not overwrite
current release files. Existing sessions and SQLite schema require no migration.
Enable only the dedicated review config. A CLI-only invocation from the selected
release can operate without restarting the web service; its launcher pins source_root.
Review the selected-release aliases/manifest during promotion. Rollback disables the
review config and restores the prior selected release using the guarded installer;
preserve durable review request rows/artifacts and never retry an uncertain delivery.

Tests: `python3 -m unittest tests.test_artifact_review.ArtifactReviewTests tests.test_integration_requests -q`.

Post-install checks (run as the service user with the selected-release environment):

```sh
agentctl doctor
agentctl auth context status --tool codex-pro --context default
agentctl integration --help
curl --fail --silent http://192.168.1.115:3210/healthz
```

Substitute the configured named context for default. Confirm existing managed sessions
remain attachable and existing AGC presence route still returns its fixed observation.
Run a known non-production review fixture through review-request/status first; inspect
its native provider version, receipts, actual argv, session model and strict result.
Do not set provider_capability_verified based only on a mocked unit test. Enable the
review config only after the exact installed provider supports the pinned flags.
