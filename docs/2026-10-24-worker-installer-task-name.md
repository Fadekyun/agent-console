# Worker resident installer task-name correction (#214)

Date: 2026-10-24
Issue: Fadekyun/agent-console #214
Base: `feat/203-laptop-migration` at `2183c6ef` (resident worker executor #213)

## Failure

The real Windows PowerShell 5.1 first-rollout install failed at
`Disable-ScheduledTask` with `0x80070002` after registering
`AGC-Worker-OwnerExecutor` disabled. PowerShell variable names are
case-insensitive, so

```powershell
$ExecutorTask = 'AGC-Worker-OwnerExecutor'
...
$executorTask = Get-ScheduledTask -TaskName $ExecutorTask
```

overwrote the fixed task-name string with the registered CIM task object.
Later `Disable-ScheduledTask`, `Enable-ScheduledTask` and
`Start-ScheduledTask` received that object instead of the name. The task itself
was registered disabled; root rolled back exact files, ACLs and tasks, with the
B4 coordinator Ready and all launchers restored.

## Correction

The registered task object now uses `$registeredExecutorTask`; the fixed
`$ExecutorTask` constant is never reassigned. Only those three references
changed; the task names, principals, actions, triggers, settings, credentials,
ACLs and resident protocol are unchanged.

## Regression

`ops/agc-worker/tests/WorkerInstallerResidentBlock.Tests.ps1` extracts the real
resident block from `Install-LifecycleStopTask.ps1` and executes it with mocked
ScheduledTasks cmdlets, including the actual receipt assignment. It covers the
deferred (register disabled, no enable/start) and immediate (enable, start,
fresh readiness) paths on Windows PowerShell 5.1 and PowerShell 7:

- every `Register/Disable/Enable/Start-ScheduledTask` receives the exact
  `AGC-Worker-OwnerExecutor` string;
- the fixed constant survives both paths;
- the exported task XML and final `owner_executor_task` receipt metadata retain
  the exact name and string type;
- `registered-disabled`/`ready` states are recorded.

The pre-fix source fails these assertions with the same CIM-object substitution
seen in the live failure; the corrected source passes.

## Rollback

Restore the prior `Install-LifecycleStopTask.ps1` from the reviewed package; no
task/config/credential state was changed by this source fix. Live installation,
rollback and any restart remain root-owned.
