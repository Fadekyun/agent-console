# Discord Planning

The existing `discord-codex-plan-runner.service` remains the only Discord planner. It runs Codex with a read-only sandbox and writes new plans to:

```text
<workspace>/handoffs/discord-JOB_ID/
├── request.json
├── plan.md
├── metadata.json
└── execution-handoff.md
```

Discord can create, inspect, follow up on, and cancel plans. It cannot attach to terminals or execute a plan. Implementation begins from SSH, web, or local shell with:

```bash
agentctl plan execute discord-JOB_ID
```

That command displays the plan, checks the repository revision when recorded, requires confirmation, and creates a separate implementation worktree/session.
