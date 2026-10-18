#Requires -RunAsAdministrator
# Operator-only, fail-held watchdog handover. No provisioning or release authority.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare','Finalize','Inspect')][string]$Phase,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$Transaction,
    [ValidateRange(1,2147483647)][int]$ExpectedPid,
    [string]$ExpectedCreationUtc,
    [ValidatePattern('^S-1-5-21-[0-9-]+$')][string]$ExpectedWorkerSid,
    [string]$ExpectedTaskInstance,
    [ValidatePattern('^[a-f0-9-]{36}$')][string]$WorkerBootId,
    [ValidatePattern('^[a-f0-9-]{36}$')][string]$StaffBootId,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$StaffConfigSha256,
    [string]$StaffObservedAt,
    [string]$NewStartFile,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$GuardSha256,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$GuestGuardSha256
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Base='C:\ProgramData\AGC-Worker'
$Code=Join-Path $Base 'code'
$Control=Join-Path $Base 'control'
$Data=Join-Path $Base 'data'
$Provision='C:\ProgramData\AGC-Worker-Provision-20261017'
$TaskName='AGC-Worker-Background'
$GuardTask='AGC-Worker-HandoverGuard'
$StartFile=Join-Path $Code 'Start-Worker.ps1'
$OldStart='af163166235c9221843b2807dcf355c3090874cd4dce7e7600072239ff999225'
$NewStart='4214a5aa9738f2367c85d76a3b00c48d85e5018f6d123d27c4f491da5ebfd103'
$NewWorker='c6dacde95e76069672f4ab1a1524e0f997de83388c3700d57af0982ae23018d8'
$ExpectedArguments='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\AGC-Worker\code\Start-Worker.ps1'
$JournalDir=Join-Path $Control "watchdog-handover\$Transaction"
$Journal=Join-Path $JournalDir 'journal.json'
$GuardCode=Join-Path $Code 'handover'
$Receipt=Join-Path $Data "handover-$Transaction.json"
$ExpectedImage="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$state=$null; $oldHandle=$null; $lock=$null; $mutationStarted=$false
$clients=New-Object 'Collections.Generic.List[object]'
$SystemWsl="$env:SystemRoot\System32\wsl.exe"
$ShimWsl='C:\Program Files\WSL\wsl.exe'

Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Security.Principal;
public sealed class AgcPinnedProcess : IDisposable {
  IntPtr h;
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr h,out long creation,out long exit,out long kernel,out long user);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr h,uint flags,StringBuilder value,ref int length);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr h,uint access,out IntPtr token);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr h,uint exitCode);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h,uint milliseconds);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  public readonly int Pid; public readonly long Creation; public readonly string Image; public readonly string Sid;
  public AgcPinnedProcess(int pid) {
    Pid=pid; h=OpenProcess(0x00100000|0x1000|0x0001,false,pid);
    if(h==IntPtr.Zero)throw new InvalidOperationException("Cannot pin process");
    try {
      long c,e,k,u;if(!GetProcessTimes(h,out c,out e,out k,out u))throw new InvalidOperationException("Cannot read creation"); Creation=c;
      var image=new StringBuilder(32768);int length=image.Capacity;
      if(!QueryFullProcessImageName(h,0,image,ref length))throw new InvalidOperationException("Cannot read image");Image=image.ToString();
      IntPtr token;if(!OpenProcessToken(h,8,out token))throw new InvalidOperationException("Cannot read owner");
      try{using(var identity=new WindowsIdentity(token)){Sid=identity.User.Value;}}finally{CloseHandle(token);}
      if(Exited)throw new InvalidOperationException("Process already exited");
    } catch {Dispose();throw;}
  }
  public bool Exited {get {return WaitForSingleObject(h,0)==0;}}
  public void TerminateExact() {
    if(Exited)throw new InvalidOperationException("Pinned process already exited");
    if(!TerminateProcess(h,0x48414E44)||WaitForSingleObject(h,5000)!=0)throw new InvalidOperationException("Pinned termination unconfirmed");
  }
  public void Dispose(){if(h!=IntPtr.Zero){CloseHandle(h);h=IntPtr.Zero;}}
}
'@

function Principal-Sid([string]$Name){
    if($Name -match '^S-1-') {return ([Security.Principal.SecurityIdentifier]::new($Name)).Value}
    return ([Security.Principal.NTAccount]::new($Name)).Translate([Security.Principal.SecurityIdentifier]).Value
}
function Hash([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Assert-Protected([string]$Path){
    $full=[IO.Path]::GetFullPath($Path)
    if(-not ($full -eq $Base -or $full.StartsWith($Base+'\',[StringComparison]::OrdinalIgnoreCase) -or $full -eq $Provision -or $full.StartsWith($Provision+'\',[StringComparison]::OrdinalIgnoreCase))){throw 'Authority path is outside fixed protected roots'}
    while($full -and $full -ne [IO.Path]::GetPathRoot($full)){
        $item=Get-Item -LiteralPath $full -Force
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse point in authority path'}
        $acl=Get-Acl -LiteralPath $full
        $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        # Windows/ProgramData ancestors may be owned by TrustedInstaller.
        $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
        if($owner -notin $trusted){throw 'Authority path owner is not trusted'}
        $mut=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
        foreach($rule in $acl.Access){
            if($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly){continue}
            if($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $mut) -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Unsafe mutation authority in protected path'}
        }
        # ProgramData itself deliberately permits creation of unrelated folders;
        # our separately secured Base is the fixed authority boundary.
        if($full -eq $Base -or $full -eq $Provision){break}
        $full=[IO.Path]::GetDirectoryName($full)
    }
}
function Write-Durable([string]$Path,[byte[]]$Bytes){
    $temporary="$Path.tmp"
    if(Test-Path -LiteralPath $temporary){throw 'Unreconciled temporary authority file'}
    $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temporary,$Path,$null)}else{[IO.File]::Move($temporary,$Path)}
    Assert-Protected $Path
}
function Save-Journal {
    $state.updated_at=[DateTimeOffset]::UtcNow.ToString('o')
    Write-Durable $Journal ([Text.UTF8Encoding]::new($false).GetBytes(($state | ConvertTo-Json -Depth 12 -Compress)))
}
function Read-Bounded([string]$Path){
    $item=Get-Item -LiteralPath $Path -Force
    if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576){throw 'Invalid bounded JSON'}
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try{$reader=[IO.StreamReader]::new($stream);$text=$reader.ReadToEnd();if($text.Length -gt 1048576){throw 'Oversize JSON'};return ($text | ConvertFrom-Json)}finally{$stream.Dispose()}
}
function Assert-StaffObservation {
    if(-not $StaffBootId -or -not $StaffConfigSha256 -or -not $StaffObservedAt){throw 'Fresh operator-observed staff baseline required'}
    $when=[DateTimeOffset]::Parse($StaffObservedAt,[Globalization.CultureInfo]::InvariantCulture)
    $age=([DateTimeOffset]::UtcNow-$when).TotalSeconds
    if($age -lt -5 -or $age -gt 120){throw 'Staff observation is stale'}
    # The operator observes the existing staff boot separately; this helper and
    # its guard never invoke a staff distribution. This is an explicit attestation.
    if((Hash 'C:\Users\store-admin\.wslconfig') -ne $StaffConfigSha256){throw 'Staff WSL configuration drift'}
    if($state -and ($state.staff_boot_id -ne $StaffBootId -or $state.staff_config_sha256 -ne $StaffConfigSha256)){throw 'Staff baseline changed'}
}
$service=New-Object -ComObject 'Schedule.Service'; $service.Connect()
function Background { $service.GetFolder('\').GetTask($TaskName) }
function Instances { @($service.GetRunningTasks(1) | Where-Object {$_.Path -eq "\$TaskName"}) }
function Normalize-Task([string]$Text){
    $xml=[xml]$Text
    $ns=New-Object Xml.XmlNamespaceManager($xml.NameTable);$ns.AddNamespace('t',$xml.DocumentElement.NamespaceURI)
    $settings=$xml.SelectSingleNode('/t:Task/t:Settings',$ns)
    $enabled=$settings.SelectSingleNode('t:Enabled',$ns)
    if($enabled){[void]$settings.RemoveChild($enabled)}
    return $xml.OuterXml
}
function Assert-Task([bool]$Enabled){
    $task=Background
    if($task.Enabled -ne $Enabled -or (Normalize-Task $task.Xml) -cne $state.task_definition){throw 'Background definition or enabled state changed'}
    return $task
}
function Assert-Old {
    if($oldHandle.Exited -or $oldHandle.Creation -ne $state.creation_filetime -or $oldHandle.Sid -ne $state.worker_sid -or $oldHandle.Image -ine $ExpectedImage){throw 'Pinned old process changed'}
    $cim=Get-CimInstance Win32_Process -Filter "ProcessId=$($state.old_pid)"
    if(-not $cim -or $cim.CommandLine -cne $state.old_command_line -or $cim.ParentProcessId -ne $state.old_parent_pid){throw 'Old process metadata changed'}
    $instances=@(Instances)
    if($instances.Count -ne 1 -or $instances[0].InstanceGuid -ne $state.instance_guid -or $instances[0].EnginePID -ne $state.old_pid){throw 'Old task instance changed'}
    $watchdogs=@(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like '*\code\Start-Worker.ps1*'})
    if($watchdogs.Count -ne 1 -or $watchdogs[0].ProcessId -ne $state.old_pid){throw 'Another watchdog exists'}
}
function Client-Action([string]$CommandLine){
    $command=$CommandLine.Trim()
    if($command -notmatch '^"([^"]+)" (.+)$'){throw 'Unexpected old WSL command syntax'}
    if($Matches[1] -ine $SystemWsl){throw 'Unexpected old WSL command executable'}
    $action=$Matches[2]
    if($action -cnotin @('-d AGC-Worker -u root --exec /usr/bin/sleep infinity','-d AGC-Worker -u root --exec /usr/local/bin/agc-worker host-sample')){throw 'Old WSL client action is not allowlisted'}
    return $action
}
function Pin-OldClients {
    # Inspect only wsl.exe client children. conhost/wslhost are not client
    # handles and must never be terminated by this helper.
    $parents=New-Object 'Collections.Generic.List[object]'
    $parents.Add(@{pid=$state.old_pid;creation=$oldHandle.Creation;depth=0;action=$null})
    for($index=0;$index -lt $parents.Count;$index++){
        $parent=$parents[$index]
        $rows=@(Get-CimInstance Win32_Process -Filter "Name='wsl.exe' AND ParentProcessId=$($parent.pid)")
        foreach($row in $rows){
            if($parent.depth -ge 2 -or $clients.Count -ge 8){throw 'Unexpected WSL client hierarchy'}
            $action=Client-Action $row.CommandLine
            $handle=$null
            try{
                try{$handle=[AgcPinnedProcess]::new([int]$row.ProcessId)}catch{
                    # A short host-sample client can finish before handle open.
                    # If still visible, refuse uncertainty rather than infer death.
                    if(-not (Get-CimInstance Win32_Process -Filter "ProcessId=$($row.ProcessId)")){continue}
                    throw
                }
                $fresh=Get-CimInstance Win32_Process -Filter "ProcessId=$($row.ProcessId)"
                if(-not $fresh -and $handle.Exited){$handle.Dispose();$handle=$null;continue}
                $cimTime=$fresh.CreationDate.ToUniversalTime().ToFileTimeUtc()
                $image=if($parent.depth -eq 0){$SystemWsl}else{$ShimWsl}
                if($handle.Sid -ne $state.worker_sid -or $handle.Image -ine $image -or $handle.Creation -lt $parent.creation -or [Math]::Abs($handle.Creation-$cimTime) -ge 10 -or $fresh.ParentProcessId -ne $parent.pid -or $fresh.CommandLine -cne $row.CommandLine){throw 'Old WSL native identity/parent changed'}
                if($parent.depth -eq 1 -and $action -cne $parent.action){throw 'WSL shim action differs from pinned client'}
                $entry=@{pid=$handle.Pid;creation_filetime=$handle.Creation;sid=$handle.Sid;image=$handle.Image;parent_pid=$parent.pid;parent_creation_filetime=$parent.creation;depth=($parent.depth+1);action=$action;command_line=$fresh.CommandLine;handle=$handle}
                $clients.Add($entry)
                $parents.Add(@{pid=$handle.Pid;creation=$handle.Creation;depth=$entry.depth;action=$action})
                $handle=$null # collection owns this retained handle
            }finally{if($handle){$handle.Dispose()}}
        }
    }
    $keepers=@($clients | Where-Object {$_.action -ceq '-d AGC-Worker -u root --exec /usr/bin/sleep infinity'})
    if(@($keepers | Where-Object {$_.depth -eq 1}).Count -ne 1 -or @($keepers | Where-Object {$_.depth -eq 2}).Count -ne 1){throw 'Exact existing keeper and shim were not pinned'}
    foreach($keeper in $keepers){if($keeper.handle.Exited){throw 'Old keeper exited before parent termination'}}
    $state.old_clients=@($clients | ForEach-Object {@{pid=$_.pid;creation_filetime=$_.creation_filetime;sid=$_.sid;image=$_.image;parent_pid=$_.parent_pid;parent_creation_filetime=$_.parent_creation_filetime;depth=$_.depth;action=$_.action;command_line=$_.command_line}})
}
function Remaining-OldClientRows {
    # Observation only: a late client must never become a new kill target.
    # Retained native handles prevent the identities above from being guessed.
    $parents=New-Object 'Collections.Generic.List[int]'
    $parents.Add([int]$state.old_pid)
    foreach($client in $clients){if(-not $parents.Contains([int]$client.pid)){$parents.Add([int]$client.pid)}}
    $seen=New-Object 'Collections.Generic.HashSet[int]'
    $remaining=New-Object 'Collections.Generic.List[object]'
    for($index=0;$index -lt $parents.Count;$index++){
        foreach($row in @(Get-CimInstance Win32_Process -Filter "Name='wsl.exe' AND ParentProcessId=$($parents[$index])")){
            if($seen.Add([int]$row.ProcessId)){
                $remaining.Add($row)
                if($parents.Count -ge 16){throw 'Unexpected late WSL client hierarchy'}
                if(-not $parents.Contains([int]$row.ProcessId)){$parents.Add([int]$row.ProcessId)}
            }
        }
    }
    return $remaining.ToArray()
}
function Stop-PinnedOldClients {
    if(-not $oldHandle.Exited){throw 'Old PowerShell is not confirmed dead'}
    [void](Assert-Task $false)
    if(@(Instances).Count){throw 'A Background instance remains before client cleanup'}
    [void](Read-Guard -MinimumSeconds 410 -RequireHealthy)
    $state.state='old_client_cleanup_intent';Save-Journal
    foreach($client in @($clients | Sort-Object -Property depth -Descending)){
        # Only the pre-termination retained handle is used. Never reopen a PID,
        # issue a process-tree kill, kill conhost/wslhost, or send WSL a command.
        if(-not $client.handle.Exited){$client.handle.TerminateExact()}
        if(-not $client.handle.Exited){throw 'Pinned old WSL client termination unconfirmed'}
    }
    $until=[DateTimeOffset]::UtcNow.AddSeconds(25)
    do{
        $remaining=@(Remaining-OldClientRows)
        if(-not $remaining.Count){break}
        # A host-sample process born between snapshot and parent termination may
        # finish normally. It is never killed by this observation or by PID.
        Start-Sleep -Milliseconds 250
    }while([DateTimeOffset]::UtcNow -lt $until)
    if($remaining.Count){
        $state.unresolved_old_client_pids=@($remaining | ForEach-Object {$_.ProcessId})
        Save-Journal
        throw 'Uncaptured old WSL clients require reconciliation'
    }
    foreach($client in $clients){if(-not $client.handle.Exited){throw 'Retained client death proof changed'}}
    [void](Read-Guard -MinimumSeconds 400 -RequireHealthy)
    $state.old_clients_confirmed_dead=$true;Save-Journal
}
function Read-Guard([int]$MinimumSeconds=60,[switch]$RequireHealthy){
    $value=Read-Bounded $Receipt
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    if($value.version -ne 1 -or $value.transaction -ne $Transaction -or $value.worker_sid -ne $state.worker_sid -or $value.deadline -ne $state.deadline -or $value.state -ne 'guarding' -or $now-$value.observed_at -gt 8 -or $now-$value.observed_at -lt -2 -or $state.deadline-$now -lt $MinimumSeconds){throw 'Fresh independent guard/deadline proof unavailable'}
    $g=$value.guest
    if($g.state -ne 'guarding' -or $g.worker_boot_id -ne $state.worker_boot_id -or $g.hold_present -ne $true -or $now-$g.observed_at -gt 8 -or $now-$g.observed_at -lt -2){throw 'Existing worker boot/hold proof failed'}
    $guardInstances=@($service.GetRunningTasks(1) | Where-Object {$_.Path -eq "\$GuardTask"})
    if($guardInstances.Count -ne 1 -or $guardInstances[0].EnginePID -ne $value.guard_pid){throw 'Guard task instance unavailable'}
    $guardHandle=[AgcPinnedProcess]::new([int]$value.guard_pid)
    try{
        if($guardHandle.Sid -ne $state.worker_sid -or $guardHandle.Image -ine $ExpectedImage){throw 'Wrong guard process owner/image'}
        $guardCim=Get-CimInstance Win32_Process -Filter "ProcessId=$($value.guard_pid)"
        if($guardCim.CommandLine -notlike "* -File $GuardCode\Invoke-WatchdogHandoverGuard.ps1 -Transaction $Transaction"){throw 'Wrong guard process action'}
    }finally{$guardHandle.Dispose()}
    if($RequireHealthy -and ($g.status.ok -ne $true -or $g.status.idle -ne $true -or $g.status.held -ne $true -or $g.status.boot_id -ne $state.worker_boot_id -or $g.populated_job_cgroups -ne 0)){throw 'Worker is not held, drained and empty'}
    return $value
}
function Assert-NoSetup {
    $setup=Get-ScheduledTask -TaskName 'AGC-Worker-Setup' -ErrorAction SilentlyContinue
    if($setup -and $setup.State -eq 'Running'){throw 'Serialize with existing operator Setup'}
}
function Assert-ScheduleAndPower {
    $time=[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Singapore Standard Time')
    if([int]$time.DayOfWeek -in @(1,2) -or ([int]$time.DayOfWeek -eq 0 -and $time.TimeOfDay -ge [TimeSpan]::FromMinutes(1415))){throw 'No complete ten-minute maintenance window'}
    Add-Type -AssemblyName System.Windows.Forms
    if([Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -ne 'Online'){throw 'Worker is not on AC'}
}
try {
    foreach($path in @($Base,$Code,$Control,(Join-Path $Control 'account.json'),(Join-Path $Control 'credential.dpapi'),$StartFile,$PSCommandPath)){Assert-Protected $path}
    if((Get-Item -LiteralPath $Data -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Worker receipt directory is redirected'}
    $worker=Get-LocalUser -Name agc-worker
    $marker=Read-Bounded (Join-Path $Control 'account.json')
    if(-not $worker.Enabled -or $worker.SID.Value -ne $marker.sid){throw 'Worker enrollment changed'}
    if(@(Get-LocalGroupMember -SID S-1-5-32-544 | Where-Object {$_.SID -eq $worker.SID}).Count){throw 'Worker unexpectedly has administrator rights'}
    # This authority lock is not a scheduler/start mutex. The persisted disabled
    # bit and pinned COM instance provide that fence.
    $lockPath=Join-Path $Control 'watchdog-handover.lock'
    $lock=[IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    Assert-Protected $lockPath
    if($Phase -eq 'Inspect'){
        Assert-Protected $Journal; $state=Read-Bounded $Journal
        [pscustomobject]@{state=$state.state;transaction=$Transaction;deadline=$state.deadline;task_enabled=(Background).Enabled;instances=@(Instances).Count;start_sha256=(Hash $StartFile)} | ConvertTo-Json -Compress
        return
    }
    Assert-NoSetup; Assert-ScheduleAndPower
    if($Phase -eq 'Prepare'){
        if(Test-Path -LiteralPath $JournalDir){throw 'Transaction already exists; inspect, never replay prepare'}
        if(-not $ExpectedPid -or -not $ExpectedCreationUtc -or -not $ExpectedWorkerSid -or -not $ExpectedTaskInstance -or -not $WorkerBootId -or -not $NewStartFile -or -not $GuardSha256 -or -not $GuestGuardSha256){throw 'Exact live and source pins required'}
        if($worker.SID.Value -ne $ExpectedWorkerSid){throw 'Worker SID mismatch'}
        Assert-StaffObservation
        if((Hash $StartFile) -ne $OldStart){throw 'Expected old watchdog not installed'}
        foreach($path in @($NewStartFile,(Join-Path $PSScriptRoot 'Invoke-WatchdogHandoverGuard.ps1'),(Join-Path $PSScriptRoot 'watchdog_handover_guard.py'))){Assert-Protected $path}
        if((Hash $NewStartFile) -ne $NewStart -or (Hash (Join-Path $PSScriptRoot 'Invoke-WatchdogHandoverGuard.ps1')) -ne $GuardSha256 -or (Hash (Join-Path $PSScriptRoot 'watchdog_handover_guard.py')) -ne $GuestGuardSha256){throw 'Reviewed source mismatch'}
        foreach($path in @($NewStartFile,(Join-Path $PSScriptRoot 'Invoke-WatchdogHandoverGuard.ps1'))){$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'PowerShell source does not parse'}}
        $oldHandle=[AgcPinnedProcess]::new($ExpectedPid)
        # Capture from native Get-Process.StartTime, never CIM CreationDate: CIM
        # can truncate the final 100ns digit and is not an exact handle guard.
        $creation=[DateTimeOffset]::Parse($ExpectedCreationUtc,[Globalization.CultureInfo]::InvariantCulture).UtcDateTime.ToFileTimeUtc()
        $task=Background
        $definition=$task.Definition
        if(-not $task.Enabled -or $definition.Actions.Count -ne 1 -or $definition.Actions.Item(1).Path -ine 'powershell.exe' -or $definition.Actions.Item(1).Arguments -cne $ExpectedArguments -or $definition.Principal.RunLevel -ne 0 -or $definition.Principal.LogonType -ne 1 -or (Principal-Sid $definition.Principal.UserId) -ne $worker.SID.Value){throw 'Unexpected Background task definition'}
        $cim=Get-CimInstance Win32_Process -Filter "ProcessId=$ExpectedPid"
        if($cim.CommandLine -cne "powershell.exe $ExpectedArguments" -and $cim.CommandLine -cne "`"powershell.exe`" $ExpectedArguments" -and $cim.CommandLine -cne "`"$ExpectedImage`" $ExpectedArguments" -and $cim.CommandLine -cne "$ExpectedImage $ExpectedArguments"){throw 'Unexpected watchdog command line'}
        $state=@{version=1;transaction=$Transaction;state='prepared';worker_sid=$ExpectedWorkerSid;worker_boot_id=$WorkerBootId;staff_boot_id=$StaffBootId;staff_config_sha256=$StaffConfigSha256;staff_before_observed_at=$StaffObservedAt;old_pid=$ExpectedPid;old_parent_pid=$cim.ParentProcessId;old_command_line=$cim.CommandLine;creation_filetime=$creation;instance_guid=$ExpectedTaskInstance;task_definition=(Normalize-Task $task.Xml);original_enabled=$task.Enabled;old_start_sha256=$OldStart;new_start_sha256=$NewStart;start_sddl=(Get-Acl -LiteralPath $StartFile).Sddl;deadline=([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()+600);guard_sha256=$GuardSha256;guest_sha256=$GuestGuardSha256}
        Assert-Old
        if(Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue){throw 'Existing guard task requires explicit reconciliation'}
        if(Test-Path -LiteralPath $GuardCode){throw 'Existing guard source directory requires reconciliation'}
        if(Test-Path -LiteralPath $Receipt){throw 'Unexpected existing guard receipt'}
        New-Item -ItemType Directory -Path $JournalDir -Force | Out-Null
        Assert-Protected $JournalDir
        Write-Durable (Join-Path $JournalDir 'Start-Worker.ps1.before') ([IO.File]::ReadAllBytes($StartFile))
        Write-Durable (Join-Path $JournalDir 'background.before.xml') ([Text.UTF8Encoding]::new($false).GetBytes($task.Xml))
        Save-Journal
        New-Item -ItemType Directory -Path $GuardCode | Out-Null
        foreach($name in @('Invoke-WatchdogHandoverGuard.ps1','watchdog_handover_guard.py')){Write-Durable (Join-Path $GuardCode $name) ([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot $name)))}
        $plan=@{version=1;worker_sid=$state.worker_sid;worker_boot_id=$WorkerBootId;deadline=$state.deadline;guard_sha256=$GuardSha256;guest_sha256=$GuestGuardSha256}
        Write-Durable (Join-Path $GuardCode "$Transaction.json") ([Text.UTF8Encoding]::new($false).GetBytes(($plan | ConvertTo-Json -Compress)))
        $state.state='guard_start_intent';Save-Journal
        Add-Type -AssemblyName System.Security
        $bytes=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $Control 'credential.dpapi')),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        try{
            $password=[Text.Encoding]::UTF8.GetString($bytes)
            $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File $GuardCode\Invoke-WatchdogHandoverGuard.ps1 -Transaction $Transaction"
            $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Seconds 600) -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName $GuardTask -Action $action -Settings $settings -User "$env:COMPUTERNAME\agc-worker" -Password $password -RunLevel Limited | Out-Null
        }finally{[Array]::Clear($bytes,0,$bytes.Length);$password=$null}
        $guardDefinition=$service.GetFolder('\').GetTask($GuardTask).Definition
        if($guardDefinition.Triggers.Count -ne 0 -or $guardDefinition.Settings.RestartCount -ne 0 -or $guardDefinition.Principal.RunLevel -ne 0){throw 'Guard task bounds changed'}
        Start-ScheduledTask -TaskName $GuardTask
        $until=[DateTimeOffset]::UtcNow.AddSeconds(30)
        do{
            Start-Sleep -Milliseconds 250
            try{$ready=Read-Guard -MinimumSeconds 540 -RequireHealthy}catch{$ready=$null}
        }while(-not $ready -and [DateTimeOffset]::UtcNow -lt $until)
        if(-not $ready){throw 'Independent keeper did not establish existing held boot'}
        if($ready.guest.worker_sha256 -ne 'c4e193682e3cdcbce5ce90ede8cb00687550b67908db29b40ef908b9d6782e02'){throw 'Expected old Linux worker changed before handover'}
        Assert-Old; [void](Assert-Task $true)
        $state.state='disable_intent';Save-Journal; $mutationStarted=$true
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        [void](Assert-Task $false); Assert-Old
        [void](Read-Guard -MinimumSeconds 480 -RequireHealthy)
        $state.state='disabled';Save-Journal
        if((Hash $StartFile) -ne $OldStart -or (Get-Acl -LiteralPath $StartFile).Sddl -cne $state.start_sddl){throw 'Watchdog bytes/ACL drift before replacement'}
        $state.state='replace_intent';Save-Journal
        Write-Durable $StartFile ([IO.File]::ReadAllBytes($NewStartFile))
        if((Hash $StartFile) -ne $NewStart -or (Get-Acl -LiteralPath $StartFile).Sddl -cne $state.start_sddl){throw 'Replacement bytes/ACL verification failed'}
        Assert-Old; [void](Assert-Task $false)
        [void](Read-Guard -MinimumSeconds 450 -RequireHealthy)
        Pin-OldClients
        Assert-Old; [void](Read-Guard -MinimumSeconds 450 -RequireHealthy)
        $state.state='terminate_old_intent';Save-Journal
        # This retained handle bypasses the exact old PowerShell finally block.
        # Never terminate an engine/parent PID or a newly looked-up PID by name.
        $oldHandle.TerminateExact()
        if(-not $oldHandle.Exited){throw 'Old process termination unconfirmed'}
        $state.state='old_process_dead';Save-Journal
        [void](Assert-Task $false); [void](Read-Guard -MinimumSeconds 420 -RequireHealthy)
        $instances=@(Instances)
        if(@($instances | Where-Object {$_.InstanceGuid -ne $state.instance_guid}).Count){throw 'Replacement task instance appeared'}
        if($instances.Count -gt 1){throw 'Multiple task instances appeared'}
        if($instances.Count -eq 1){
            # Only now may Task Scheduler clean the pinned old instance's child
            # WSL clients. Its PowerShell is confirmed dead; guard is independent.
            $instances[0].Stop()
        }
        $until=[DateTimeOffset]::UtcNow.AddSeconds(10)
        while(@(Instances).Count -and [DateTimeOffset]::UtcNow -lt $until){Start-Sleep -Milliseconds 250}
        if(@(Instances).Count){throw 'Old task instance cleanup unconfirmed'}
        Stop-PinnedOldClients
        $state.state='awaiting_linux_upgrade';Save-Journal
    } else {
        Assert-Protected $Journal
        $loaded=Read-Bounded $Journal
        $state=@{};foreach($property in $loaded.PSObject.Properties){$state[$property.Name]=$property.Value}
        if($state.version -ne 1 -or $state.transaction -ne $Transaction -or $state.worker_sid -ne $worker.SID.Value -or ($state.state -ne 'awaiting_linux_upgrade' -or $state.old_clients_confirmed_dead -ne $true)){throw 'Incomplete or completed transaction requires reconciliation; no replay'}
        Assert-StaffObservation
        [void](Assert-Task $false)
        if(@(Instances).Count){throw 'Unexpected Background task before finalize'}
        if((Hash $StartFile) -ne $NewStart -or (Get-Acl -LiteralPath $StartFile).Sddl -cne $state.start_sddl){throw 'New watchdog bytes/ACL changed'}
        $guard=Read-Guard -MinimumSeconds 90 -RequireHealthy
        if($guard.guest.worker_sha256 -ne $NewWorker){throw 'Reviewed Linux worker has not been installed'}
        $state.staff_after_observed_at=$StaffObservedAt
        $state.state='finalize_intent';Save-Journal; $mutationStarted=$true
        # Restore only the originally enabled bit. Original triggers/actions,
        # principal and settings remain byte-equivalent apart from Enabled.
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        [void](Assert-Task $true)
        if(@(Instances).Count -eq 0){Start-ScheduledTask -TaskName $TaskName}
        $until=[DateTimeOffset]::UtcNow.AddSeconds(30)
        $newHandle=$null
        do{
            Start-Sleep -Milliseconds 250
            $instances=@(Instances)
            if($instances.Count -gt 1){throw 'Multiple replacement task instances'}
            if($instances.Count -eq 1 -and $instances[0].InstanceGuid -ne $state.instance_guid){
                try{$newHandle=[AgcPinnedProcess]::new([int]$instances[0].EnginePID)}catch{$newHandle=$null}
            }
        }while(-not $newHandle -and [DateTimeOffset]::UtcNow -lt $until)
        if(-not $newHandle){throw 'Replacement watchdog did not start'}
        try{
            $cim=Get-CimInstance Win32_Process -Filter "ProcessId=$($newHandle.Pid)"
            if($newHandle.Sid -ne $state.worker_sid -or $newHandle.Image -ine $ExpectedImage -or $cim.CommandLine -cne $state.old_command_line -or $newHandle.Creation -le $state.creation_filetime){throw 'Wrong replacement watchdog process'}
            # Wait beyond Start-Worker's initial five-second warmup. The guard
            # alone keeping the boot alive is insufficient proof of new startup.
            $until=[DateTimeOffset]::UtcNow.AddSeconds(20)
            do{
                Start-Sleep -Milliseconds 500
                if($newHandle.Exited){throw 'Replacement watchdog exited'}
                $guard=Read-Guard -MinimumSeconds 30 -RequireHealthy
                $sampleAfterStart=$guard.guest.status.host_sampled_at -ge ([DateTime]::FromFileTimeUtc($newHandle.Creation)-[DateTime]'1970-01-01').TotalSeconds -and $guard.guest.status.new_watchdog_sample -eq $true
                $survived=([DateTime]::UtcNow-[DateTime]::FromFileTimeUtc($newHandle.Creation)).TotalSeconds -ge 8
                $children=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($newHandle.Pid)" | Where-Object {$_.Name -eq 'wsl.exe' -and $_.CommandLine -like '*-d AGC-Worker -u root --exec /usr/bin/sleep infinity*'})
            }while((-not $sampleAfterStart -or -not $survived -or $children.Count -ne 1) -and [DateTimeOffset]::UtcNow -lt $until)
            if(-not $sampleAfterStart -or -not $survived -or $children.Count -ne 1){throw 'Replacement keepalive/fresh new telemetry not established'}
            $state.new_pid=$newHandle.Pid; $state.new_creation_filetime=$newHandle.Creation
            $state.new_instance_guid=$instances[0].InstanceGuid
            $state.new_keeper_pid=$children[0].ProcessId
            $state.state='completed_held_guard_expires';Save-Journal
        }finally{$newHandle.Dispose()}
    }
    [pscustomobject]@{state=$state.state;transaction=$Transaction;deadline=$state.deadline;journal=$Journal;worker_boot_id=$state.worker_boot_id;staff_boot_id=$state.staff_boot_id;admission='held'} | ConvertTo-Json -Compress
} catch {
    if($state -and (Test-Path -LiteralPath $Journal)){
        $state.failure_phase=$state.state; $state.state='failed_requires_reconciliation'; $state.error_type=$_.Exception.GetType().Name
        if($mutationStarted){
            try{Disable-ScheduledTask -TaskName $TaskName | Out-Null;$state.failure_disabled= -not (Background).Enabled}catch{$state.failure_disabled=$false}
        }
        # No automatic process stop, task restart, code rollback or VM shutdown.
        # A newly running watchdog on finalize failure needs operator review.
        try{Save-Journal}catch{Write-Warning 'Journal update failed; preserve previous intent and reconcile'}
    }
    throw
} finally {
    foreach($client in $clients){$client.handle.Dispose()}
    if($oldHandle){$oldHandle.Dispose()}
    if($lock){$lock.Dispose()}
}
