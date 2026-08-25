Set-StrictMode -Version Latest

$script:ClanGamesHealthSchemaVersion = 1
$script:ClanGamesHealthResultCodes = @(
    'no_event_registry', 'no_scan_due', 'event_complete', 'baseline_missed',
    'workspace_busy', 'scheduler_busy', 'success', 'already_recorded',
    'partial_player_failures', 'api_http_403', 'schedule_conflict',
    'schedule_error', 'collector_failed', 'runtime_failure'
)

function Get-ClanGamesUtcTimestamp {
    return [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}

function Test-ClanGamesHealthCode {
    param([Parameter(Mandatory = $true)][string] $Value)
    return $script:ClanGamesHealthResultCodes -contains $Value
}

function Write-ClanGamesHealthJson {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][hashtable] $Payload
    )
    $directory = Split-Path -Parent $Path
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $temporary = Join-Path $directory ('.' + [System.IO.Path]::GetFileName($Path) + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $replacementBackup = Join-Path $directory ('.' + [System.IO.Path]::GetFileName($Path) + '.' + [Guid]::NewGuid().ToString('N') + '.bak')
    try {
        $json = $Payload | ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        if ([System.IO.File]::Exists($Path)) {
            [System.IO.File]::Replace($temporary, $Path, $replacementBackup)
            [System.IO.File]::Delete($replacementBackup)
        }
        else {
            [System.IO.File]::Move($temporary, $Path)
        }
    }
    finally {
        if ([System.IO.File]::Exists($temporary)) { [System.IO.File]::Delete($temporary) }
        if ([System.IO.File]::Exists($replacementBackup)) { [System.IO.File]::Delete($replacementBackup) }
    }
}

function New-ClanGamesHealthRecord {
    param(
        [Parameter(Mandatory = $true)][string] $RunId,
        [Parameter(Mandatory = $true)][ValidateSet('idle', 'success', 'partial_success', 'warning', 'failed')][string] $Status,
        [Parameter(Mandatory = $true)][string] $ResultCode,
        [string] $Action,
        [string] $EventId,
        [string] $ScanId,
        [string] $ScanKind,
        [string] $ScheduledForUtc,
        [string] $OperatorHintCode,
        [int] $ProcessExitCode = 0,
        [bool] $CollectorInvoked = $false,
        [string] $CollectorStatus,
        [int] $RequestedCount = 0,
        [int] $AttemptedCount = 0,
        [int] $SuccessfulCount = 0,
        [int] $FailedCount = 0,
        [int] $SkippedCount = 0,
        [string] $StartedAtUtc,
        [string] $FinishedAtUtc = (Get-ClanGamesUtcTimestamp)
    )
    if (-not (Test-ClanGamesHealthCode -Value $ResultCode)) { $ResultCode = 'runtime_failure' }
    $safeId = '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    foreach ($candidate in @($RunId, $EventId, $ScanId)) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and $candidate -notmatch $safeId) {
            throw 'Health identity contains unsupported characters.'
        }
    }
    $startedValue = [DateTimeOffset]::Parse($StartedAtUtc, [Globalization.CultureInfo]::InvariantCulture)
    $finishedValue = [DateTimeOffset]::Parse($FinishedAtUtc, [Globalization.CultureInfo]::InvariantCulture)
    $duration = [Math]::Max(0.0, ($finishedValue - $startedValue).TotalSeconds)
    $message = switch ($ResultCode) {
        'no_event_registry' { 'Clan Games event registry is not present.' }
        'no_scan_due' { 'No Clan Games scan is due.' }
        'event_complete' { 'The latest relevant event already has a final scan.' }
        'baseline_missed' { 'The baseline window has passed; periodic evidence remains allowed.' }
        'workspace_busy' { 'The site updater owns the workspace; collection was deferred.' }
        'scheduler_busy' { 'Another Clan Games scheduler process is active.' }
        'api_http_403' { 'Clan Games collection was denied by the API.' }
        'success' { 'Clan Games scan completed.' }
        'already_recorded' { 'The deterministic scan was already recorded.' }
        'partial_player_failures' { 'Clan Games scan completed with partial coverage.' }
        default { 'Clan Games scheduler completed with a bounded operational result.' }
    }
    return [ordered]@{
        schema_version = $script:ClanGamesHealthSchemaVersion
        run_id = $RunId
        status = $Status
        result_code = $ResultCode
        action = $Action
        event_id = $EventId
        scan_id = $ScanId
        scan_kind = $ScanKind
        scheduled_for_utc = $ScheduledForUtc
        operator_hint_code = $OperatorHintCode
        collector_invoked = $CollectorInvoked
        collector_status = $CollectorStatus
        requested_count = $RequestedCount
        attempted_count = $AttemptedCount
        successful_count = $SuccessfulCount
        failed_count = $FailedCount
        skipped_count = $SkippedCount
        process_exit_code = $ProcessExitCode
        duration_seconds = [Math]::Round($duration, 3)
        safe_message = $message
        started_at_utc = $StartedAtUtc
        finished_at_utc = $FinishedAtUtc
    }
}

function Complete-ClanGamesHealth {
    param(
        [Parameter(Mandatory = $true)][string] $HealthRoot,
        [Parameter(Mandatory = $true)][hashtable] $Record
    )
    Write-ClanGamesHealthJson -Path (Join-Path $HealthRoot 'latest-run.json') -Payload $Record
    switch ($Record.status) {
        'success' {
            Write-ClanGamesHealthJson -Path (Join-Path $HealthRoot 'last-scan-success.json') -Payload $Record
        }
        'partial_success' {
            Write-ClanGamesHealthJson -Path (Join-Path $HealthRoot 'last-scan-success.json') -Payload $Record
            Write-ClanGamesHealthJson -Path (Join-Path $HealthRoot 'latest-warning.json') -Payload $Record
        }
        'warning' {
            Write-ClanGamesHealthJson -Path (Join-Path $HealthRoot 'latest-warning.json') -Payload $Record
        }
        'failed' {
            Write-ClanGamesHealthJson -Path (Join-Path $HealthRoot 'latest-failure.json') -Payload $Record
        }
    }
}
