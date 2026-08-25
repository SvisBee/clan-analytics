param(
    [switch] $ConfirmSchedule,
    [switch] $ValidateOnly,
    [switch] $ShowContract
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskName = 'Clash Clan Analytics - Clan Games Collector'
$scriptPath = 'D:\coc\repo\scripts\clan_games\run_clan_games_scheduler.ps1'
$workingDirectory = 'D:\coc\repo'
$executionLimit = [TimeSpan]::FromMinutes(20)

function Get-ExpectedContract {
    $contract = [ordered]@{
        schema_version = 1
        task_name = $taskName
        executable = 'powershell.exe'
        arguments = '-NoProfile -ExecutionPolicy Bypass -File "D:\coc\repo\scripts\clan_games\run_clan_games_scheduler.ps1"'
        working_directory = $workingDirectory
        repetition_interval = 'PT1H'
        trigger_minute = 20
        at_logon = $true
        multiple_instances = 'IgnoreNew'
        execution_time_limit = 'PT20M'
        start_when_available = $true
        run_only_if_network_available = $true
        wake_to_run = $true
        logon_type = 'Interactive'
        run_level = 'Limited'
    }
    $json = $contract | ConvertTo-Json -Depth 5 -Compress
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = ([BitConverter]::ToString($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($json)))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha256.Dispose() }
    $contract['semantic_hash'] = $hash
    return $contract
}

function Test-ExistingTask {
    param([Parameter(Mandatory = $true)] $Task)
    $actions = @($Task.Actions); $triggers = @($Task.Triggers)
    $hourly = @($triggers | Where-Object { $_.Repetition.Interval -eq 'PT1H' -and ([datetime]$_.StartBoundary).Minute -eq 20 })
    $logon = @($triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' })
    return $actions.Count -eq 1 -and $actions[0].Execute -ieq 'powershell.exe' -and
        $actions[0].Arguments -eq '-NoProfile -ExecutionPolicy Bypass -File "D:\coc\repo\scripts\clan_games\run_clan_games_scheduler.ps1"' -and
        $actions[0].WorkingDirectory -ieq $workingDirectory -and $hourly.Count -eq 1 -and
        $logon.Count -eq 1 -and $Task.Settings.MultipleInstances -eq 'IgnoreNew' -and
        $Task.Settings.ExecutionTimeLimit -eq 'PT20M' -and $Task.Settings.StartWhenAvailable -and
        $Task.Settings.RunOnlyIfNetworkAvailable -and $Task.Settings.WakeToRun -and
        $Task.Principal.LogonType -eq 'Interactive' -and $Task.Principal.RunLevel -eq 'Limited'
}

$contract = Get-ExpectedContract
if ($ShowContract) { $contract | ConvertTo-Json -Depth 5; exit 0 }
if (-not [System.IO.File]::Exists($scriptPath)) { throw 'Clan Games scheduler entrypoint is missing.' }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    if (-not (Test-ExistingTask -Task $existing)) { throw 'Existing Clan Games task differs from the approved contract.' }
    [ordered]@{ status = 'no_change'; result_code = 'task_contract_matches'; task_name = $taskName; semantic_hash = $contract.semantic_hash } | ConvertTo-Json
    exit 0
}
if ($ValidateOnly) {
    [ordered]@{ status = 'ready'; result_code = 'task_absent'; task_name = $taskName; semantic_hash = $contract.semantic_hash } | ConvertTo-Json
    exit 0
}
if (-not $ConfirmSchedule) { throw 'Use -ConfirmSchedule to create the approved scheduled task.' }
$next = [datetime]::Today.AddHours(([datetime]::Now.Hour + 1) % 24).AddMinutes(20)
if ($next -le [datetime]::Now) { $next = $next.AddDays(1) }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument $contract.arguments -WorkingDirectory $workingDirectory
$hourly = New-ScheduledTaskTrigger -Once -At $next `
    -RepetitionInterval ([TimeSpan]::FromHours(1))
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit $executionLimit `
    -RunOnlyIfNetworkAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger @($hourly, $logon) `
    -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null
$created = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if (-not (Test-ExistingTask -Task $created)) { throw 'Created task failed contract validation.' }
[ordered]@{ status = 'success'; result_code = 'task_created'; task_name = $taskName; semantic_hash = $contract.semantic_hash } | ConvertTo-Json
