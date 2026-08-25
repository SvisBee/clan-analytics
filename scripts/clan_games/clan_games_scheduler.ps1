Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'clan_games_health.ps1')
. (Join-Path $PSScriptRoot '..\update\native_process.ps1')
. (Join-Path $PSScriptRoot '..\update\workspace_mutex.ps1')

function Get-ClanGamesSchedulerMutexName {
    param([Parameter(Mandatory = $true)][string] $WorkspaceRoot)
    $canonical = Get-CanonicalWorkspaceRoot -WorkspaceRoot $WorkspaceRoot
    $bytes = [Text.Encoding]::UTF8.GetBytes($canonical)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { $hex = ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha256.Dispose() }
    return "Local\ClashClanAnalyticsClanGamesScheduler-$($hex.Substring(0, 24))"
}

function Test-NamedMutexAvailable {
    param([Parameter(Mandatory = $true)][string] $Name)
    $created = $false
    $mutex = [Threading.Mutex]::new($false, $Name, [ref]$created)
    try {
        try { $acquired = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if ($acquired) { $mutex.ReleaseMutex() }
        return $acquired
    }
    finally { $mutex.Dispose() }
}

function ConvertFrom-SafeProcessJson {
    param([Parameter(Mandatory = $true)][pscustomobject] $ProcessResult)
    $text = if (-not [string]::IsNullOrWhiteSpace($ProcessResult.stdout)) {
        $ProcessResult.stdout.Trim()
    } else {
        $ProcessResult.stderr_safe
    }
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    try { return $text | ConvertFrom-Json -ErrorAction Stop }
    catch { return $null }
}

function Get-SafeJsonProperty {
    param(
        [AllowNull()] $Object,
        [Parameter(Mandatory = $true)][string] $Name,
        $Default = $null
    )
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}

function Invoke-ClanGamesScheduler {
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)][string] $WorkspaceRoot,
        [Parameter(Mandatory = $true)][string] $PythonPath,
        [Parameter(Mandatory = $true)][string] $PlannerPath,
        [Parameter(Mandatory = $true)][string] $CollectorPath,
        [Parameter(Mandatory = $true)][string] $HealthRoot,
        [bool] $CheckSiteMutex = $true
    )
    $started = Get-ClanGamesUtcTimestamp
    $runId = 'cg-scheduler-' + [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $ownName = Get-ClanGamesSchedulerMutexName -WorkspaceRoot $WorkspaceRoot
    $created = $false
    $ownMutex = [Threading.Mutex]::new($false, $ownName, [ref]$created)
    $ownsMutex = $false
    try {
        try { $ownsMutex = $ownMutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) {
            $record = New-ClanGamesHealthRecord -RunId $runId -Status warning `
                -ResultCode scheduler_busy -Action scheduler_busy -StartedAtUtc $started
            Complete-ClanGamesHealth -HealthRoot $HealthRoot -Record $record
            return 0
        }

        if ($CheckSiteMutex) {
            $siteName = Get-WorkspaceMutexName -WorkspaceRoot $WorkspaceRoot
            if (-not (Test-NamedMutexAvailable -Name $siteName)) {
                $record = New-ClanGamesHealthRecord -RunId $runId -Status warning `
                    -ResultCode workspace_busy -Action workspace_busy -StartedAtUtc $started
                Complete-ClanGamesHealth -HealthRoot $HealthRoot -Record $record
                return 0
            }
        }

        $planProcess = Invoke-NativeProcess -FilePath $PythonPath -Arguments @($PlannerPath, '--json')
        $plan = ConvertFrom-SafeProcessJson -ProcessResult $planProcess
        if (-not $planProcess.succeeded -or $null -eq $plan -or $plan.status -ne 'success') {
            $code = if ($null -ne $plan -and (Test-ClanGamesHealthCode -Value ([string]$plan.result_code))) { [string]$plan.result_code } else { 'schedule_error' }
            $record = New-ClanGamesHealthRecord -RunId $runId -Status failed `
                -ResultCode $code -Action schedule_error -ProcessExitCode 1 -StartedAtUtc $started
            Complete-ClanGamesHealth -HealthRoot $HealthRoot -Record $record
            return 1
        }

        if (-not [bool]$plan.collector_due) {
            $status = if ($plan.result_code -in @('baseline_missed', 'workspace_busy')) { 'warning' } else { 'idle' }
            $record = New-ClanGamesHealthRecord -RunId $runId -Status $status `
                -ResultCode ([string]$plan.result_code) -Action ([string]$plan.action) `
                -EventId ([string]$plan.event_id) -OperatorHintCode ([string]$plan.operator_hint_code) `
                -StartedAtUtc $started
            Complete-ClanGamesHealth -HealthRoot $HealthRoot -Record $record
            return 0
        }

        $collectorProcess = Invoke-NativeProcess -FilePath 'powershell.exe' -Arguments @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $CollectorPath,
            '-EventId', [string]$plan.event_id, '-ScanId', [string]$plan.scan_id,
            '-ScanKind', [string]$plan.scan_kind
        )
        $collector = ConvertFrom-SafeProcessJson -ProcessResult $collectorProcess
        if ($null -eq $collector) {
            $collectorStatus = 'failed'; $collectorCode = 'collector_failed'; $exitCode = 1
        } else {
            $collectorStatus = [string]$collector.status
            $collectorCode = [string]$collector.result_code
            $exitCode = if ($collectorProcess.succeeded -and $collectorStatus -in @('success', 'partial_success', 'no_change')) { 0 } else { 1 }
        }
        $healthStatus = switch ($collectorStatus) {
            'success' { 'success' }
            'no_change' { 'success' }
            'partial_success' { 'partial_success' }
            default { 'failed' }
        }
        if (-not (Test-ClanGamesHealthCode -Value $collectorCode)) {
            if ($collectorCode -eq 'already_recorded') { $collectorCode = 'already_recorded' }
            elseif ($collectorStatus -eq 'success') { $collectorCode = 'success' }
            else { $collectorCode = 'collector_failed' }
        }
        $hint = [string](Get-SafeJsonProperty -Object $collector -Name operator_hint_code)
        $requested = [int](Get-SafeJsonProperty -Object $collector -Name requested_count -Default 0)
        $attempted = [int](Get-SafeJsonProperty -Object $collector -Name attempted_count -Default 0)
        $successful = [int](Get-SafeJsonProperty -Object $collector -Name successful_count -Default 0)
        $failed = [int](Get-SafeJsonProperty -Object $collector -Name failed_count -Default 0)
        $skipped = [int](Get-SafeJsonProperty -Object $collector -Name skipped_count -Default 0)
        $record = New-ClanGamesHealthRecord -RunId $runId -Status $healthStatus `
            -ResultCode $collectorCode -Action ([string]$plan.action) `
            -EventId ([string]$plan.event_id) -ScanId ([string]$plan.scan_id) `
            -ScanKind ([string]$plan.scan_kind) -ScheduledForUtc ([string]$plan.scheduled_for_utc) `
            -OperatorHintCode $hint -ProcessExitCode $exitCode -CollectorInvoked $true `
            -CollectorStatus $collectorStatus -RequestedCount $requested -AttemptedCount $attempted `
            -SuccessfulCount $successful -FailedCount $failed -SkippedCount $skipped `
            -StartedAtUtc $started
        Complete-ClanGamesHealth -HealthRoot $HealthRoot -Record $record
        return $exitCode
    }
    catch {
        if ($ownsMutex) {
            $record = New-ClanGamesHealthRecord -RunId $runId -Status failed `
                -ResultCode runtime_failure -Action runtime_failure -ProcessExitCode 1 -StartedAtUtc $started
            Complete-ClanGamesHealth -HealthRoot $HealthRoot -Record $record
        }
        return 1
    }
    finally {
        if ($ownsMutex) { $ownMutex.ReleaseMutex() }
        $ownMutex.Dispose()
    }
}
