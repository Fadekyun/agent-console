# Connected release verification — 2026-10-02

The feature branch `feature/connected-work-staging-20261001` published v0.17 at `dfe2a67d6626cf62a072aec095bb1beea4d003c6` and the live status fix v0.17.1 at `b579a03a265908beac39081a5bd4c5ffae0efcb0`. It retains the independently selected current console. Source contract: [WORKFLOW_RELEASES.md](WORKFLOW_RELEASES.md).

## Tests and actual external effect

The combined workflow suite passed 58 tests, including real adapter processes, exact candidate/causal check matching, unsupported target/action, superseded/failed prerequisites, changed adapter, operator authentication, stable request keys, lost acknowledgment, explicit retry after not-applied proof, uncertain-target serialization, timeout, orphan workers, Pause and Stop. Work overview/history tests also passed; unknown releases are attention items while their candidate result remains completed. Browser scenarios passed at desktop, 360px and 390px; the focused refresh test confirms pending status polling preserves an edited evidence selection. These are emulated browser dimensions, not physical-phone certification.

Live staging created an isolated Git fixture and configured a temporary target labelled **Verification sandbox (isolated files)**. This target copies the exact selected commit archive into private verification storage and records its operation ID/hash. It does not deploy an application or change any production service.

- Source session: `sess-88e5973c08834e249db6ae71f322284e` (`release-verification-1790889307`), stopped after publishing.
- Candidate result: `result-d7d99c8026fc423e86472e569278ff62`.
- Full commit: `1d7a71a6091847222606b8938f56bc87cbbc66e8`.
- Selected archive SHA-256: `9c24648367b82a4bcb4b9fa878cf3b12ca4533a124e38070a4010173de4373f0`.
- Explicit UI grant: `release-1e7dbfec917e4dd791f083a8850e99c4`, deploy → verification-sandbox.
- Apply attempt: `release-attempt-504843e149dc4a76a15300e4bd4f9f61`.

The real UI preview did not execute. Authorize and run performed **one** target write, then the adapter deliberately returned a failed acknowledgment and its probe reported unknown. The same attempt request key returned the existing receipt. Work showed release attention while retaining candidate success. After a web-service restart, the UI's Check external outcome ran only a read-only probe, observed the original full SHA/archive/operation, recorded applied, and cleared the attention item. Target apply count stayed one. Downloaded selected artifact bytes also matched the published hash.

The first live run exposed an obsolete queued card during concurrent UI refresh; the backend receipt correctly remained unknown. v0.17.1 fixes this by updating/polling release history separately, preserving the form and discarding old loads. Verification resumed the **same** grant/attempt after the fix; it did not create a replacement apply to obtain a passing result.

Screenshots and full test/deploy/runtime/recovery records live in the task workspace under `handoffs/console-release-20261002/`. They include the failed initial browser check, subsequent preserved-request verification, and before/after restart receipts. The temporary target config is removed after verification; its private fixture files and durable records are retained as evidence.

## Canonical guide and limits

The canonical n100 package `/home/fadekyun/codex/skills/agent-console-release` and staging mirror carry revision `2026-10-02.1`, file SHA-256 `ece63bf13af4ed01dbb0862ef048e72a187342319ff7e51230e7cf166071a472`, package hash `9b15244119adafdaba9a5a0f2c03e7955bbac562ba614c88dd1309ae2c671e0d`. Skill validation passed. It is assigned only to staging's release role; a task-free Codex Pro release session received matching bytes in its actual copied snapshot and was stopped. This proves delivery, not that a model used the guide.

The adapter protocol is tested, not every possible operator-installed adapter. A target's probe must correctly establish applied/not-applied/unknown from authoritative external evidence. No production target, release credential, production merge/deployment or current-console cutover was authorized by this test. Remaining guide bundles, skills/provider discovery, exact launch fidelity and the final full-revamp acceptance audit remain open.
