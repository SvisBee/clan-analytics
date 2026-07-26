Set-StrictMode -Version Latest

$script:CollectionHealthSchemaVersion = 1
$script:CollectionHealthStages = @(
    'bootstrap', 'mutex', 'git_preflight', 'roster_probe', 'current_war_probe',
    'war_log_probe', 'builder', 'public_validation', 'tests', 'atomic_apply',
    'git_commit', 'git_push', 'complete'
)
$script:CollectionHealthResultCodes = @(
    'success', 'no_public_change', 'preview_success', 'mutex_held', 'git_dirty',
    'git_branch_ahead', 'git_branch_behind', 'git_branch_diverged', 'api_http_403',
    'api_http_other', 'api_transport_failure', 'probe_validation_failure',
    'history_preflight_failure', 'builder_failure', 'public_validation_failure',
    'tests_failure', 'atomic_apply_failure', 'git_commit_failure', 'git_push_failure',
    'health_write_failure', 'unexpected_failure'
)

function Get-HealthUtcNow { (Get-Date).ToUniversalTime().ToString('o') }

function Get-CollectionHealthRoot {
    param([Parameter(Mandatory = $true)][string] $WorkspaceRoot)
    Join-Path $WorkspaceRoot 'local\health\site_update'
}

function Write-CollectionHealthJsonAtomic {
    param([Parameter(Mandatory = $true)][string] $Path, [Parameter(Mandatory = $true)] $Value)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null
    $temporary = Join-Path $parent ('.health-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding utf8 -NoNewline -ErrorAction Stop
        Move-Item -LiteralPath $temporary -Destination $Path -Force -ErrorAction Stop
    }
    finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}

function Write-CollectionBootstrapLog {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)][string] $Stage,
        [Parameter(Mandatory = $true)][string] $ResultCode, [Parameter(Mandatory = $true)][string] $SafeMessage,
        [Parameter(Mandatory = $true)][int] $ExitCode)
    $entry = [ordered]@{
        run_id = $Health.run_id; mode = $Health.mode; started_at_utc = $Health.started_at_utc
        local_start_time = $Health.local_start_time; process_id = $Health.process_id; stage = $Stage
        result_code = $ResultCode; safe_message = $SafeMessage; exit_code = $ExitCode
    }
    $entry | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $Health.run_directory 'bootstrap.log') -Encoding utf8 -ErrorAction Stop
}

function New-CollectionHealthRun {
    param([Parameter(Mandatory = $true)][string] $WorkspaceRoot, [Parameter(Mandatory = $true)][string] $RunDirectory,
        [Parameter(Mandatory = $true)][string] $RunId, [Parameter(Mandatory = $true)][string] $Mode)
    New-Item -ItemType Directory -Path $RunDirectory -Force -ErrorAction Stop | Out-Null
    $health = [ordered]@{
        schema_version = $script:CollectionHealthSchemaVersion; workspace_root = $WorkspaceRoot; run_id = $RunId; mode = $Mode
        started_at_utc = Get-HealthUtcNow; local_start_time = (Get-Date).ToString('o'); process_id = $PID
        finished_at_utc = $null; duration_seconds = $null; status = 'running'; current_stage = 'bootstrap'
        result_code = 'running'; safe_message = 'Updater run initialized.'; operator_hint_code = $null
        run_directory = $RunDirectory; stages = @(); git_preflight = $null; probes = [ordered]@{}
        builder = $null; validation = $null; publication = $null; freshness = [ordered]@{}
    }
    Write-CollectionBootstrapLog -Health $health -Stage 'bootstrap' -ResultCode 'running' -SafeMessage 'Updater run initialized.' -ExitCode 0
    Save-CollectionHealth -Health $health
    return $health
}

function Add-CollectionHealthStage {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)][string] $Stage,
        [ValidateSet('running', 'success', 'no_change', 'failed', 'skipped')][string] $Status = 'running',
        [string] $ResultCode = 'running')
    if ($Stage -notin $script:CollectionHealthStages) { throw "Unsupported health stage: $Stage" }
    $now = Get-HealthUtcNow
    $record = [ordered]@{ stage = $Stage; status = $Status; started_at_utc = $now; finished_at_utc = $null; duration_seconds = $null; result_code = $ResultCode }
    $Health.stages += [pscustomobject]$record
    $Health.current_stage = $Stage
    Save-CollectionHealth -Health $Health
    return $record
}

function Complete-CollectionHealthStage {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)] $StageRecord,
        [ValidateSet('success', 'no_change', 'failed', 'skipped')][string] $Status, [Parameter(Mandatory = $true)][string] $ResultCode)
    $finished = Get-HealthUtcNow
    $StageRecord.status = $Status; $StageRecord.finished_at_utc = $finished; $StageRecord.result_code = $ResultCode
    $StageRecord.duration_seconds = [math]::Round(((Get-Date).ToUniversalTime() - [datetime]$StageRecord.started_at_utc).TotalSeconds, 3)
    Save-CollectionHealth -Health $Health
}

function Save-CollectionHealth {
    param([Parameter(Mandatory = $true)] $Health)
    $healthPath = Join-Path $Health.run_directory 'health.json'
    Write-CollectionHealthJsonAtomic -Path $healthPath -Value $Health
    Write-CollectionHealthJsonAtomic -Path (Join-Path (Get-CollectionHealthRoot -WorkspaceRoot $Health.workspace_root) 'latest-run.json') -Value $Health
}

function Complete-CollectionHealth {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)][string] $Status,
        [Parameter(Mandatory = $true)][string] $ResultCode, [Parameter(Mandatory = $true)][string] $SafeMessage,
        [string] $OperatorHintCode, [int] $ExitCode = 0)
    $finished = Get-HealthUtcNow
    $Health.status = $Status; $Health.current_stage = 'complete'; $Health.result_code = $ResultCode
    $Health.safe_message = $SafeMessage; $Health.operator_hint_code = $OperatorHintCode
    $Health.finished_at_utc = $finished
    $Health.duration_seconds = [math]::Round(((Get-Date).ToUniversalTime() - [datetime]$Health.started_at_utc).TotalSeconds, 3)
    Write-CollectionBootstrapLog -Health $Health -Stage 'complete' -ResultCode $ResultCode -SafeMessage $SafeMessage -ExitCode $ExitCode
    Save-CollectionHealth -Health $Health
    $root = Get-CollectionHealthRoot -WorkspaceRoot $Health.workspace_root
    if ($Health.mode -eq 'normal' -and $Status -in @('success', 'no_change')) {
        Write-CollectionHealthJsonAtomic -Path (Join-Path $root 'last-success.json') -Value $Health
        $failure = Join-Path $root 'latest-failure.json'
        if (Test-Path -LiteralPath $failure) {
            $previous = Get-Content -LiteralPath $failure -Raw | ConvertFrom-Json
            $previous | Add-Member -NotePropertyName resolved_at_utc -NotePropertyValue $finished -Force
            Write-CollectionHealthJsonAtomic -Path $failure -Value $previous
        }
    }
    elseif ($Health.mode -eq 'normal' -and $Status -eq 'failed') {
        Write-CollectionHealthJsonAtomic -Path (Join-Path $root 'latest-failure.json') -Value $Health
    }
}

function Get-CollectionHealthFailure {
    param([Parameter(Mandatory = $true)][string] $Stage, [Parameter(Mandatory = $true)][string] $Text)
    if ($Stage -in @('roster_probe', 'current_war_probe', 'war_log_probe')) {
        if ($Text -match 'HTTP request failed with status 403') {
            return [ordered]@{ result_code = 'api_http_403'; operator_hint_code = 'enable_approved_vpn'; safe_message = 'Clash API rejected the request with HTTP 403. This installation usually requires the approved VPN for collection. Token and API settings were not changed.' }
        }
        if ($Text -match 'HTTP request failed with status') { return [ordered]@{ result_code = 'api_http_other'; operator_hint_code = $null; safe_message = 'Clash API returned an HTTP error. No data was applied.' } }
        return [ordered]@{ result_code = 'api_transport_failure'; operator_hint_code = $null; safe_message = 'Clash API probe did not complete. No data was applied.' }
    }
    $codes = @{ 'git_preflight'='git_dirty'; 'builder'='builder_failure'; 'public_validation'='public_validation_failure'; 'tests'='tests_failure'; 'atomic_apply'='atomic_apply_failure'; 'git_commit'='git_commit_failure'; 'git_push'='git_push_failure'; 'mutex'='mutex_held'; 'bootstrap'='history_preflight_failure' }
    $code = if ($codes.ContainsKey($Stage)) { $codes[$Stage] } else { 'unexpected_failure' }
    return [ordered]@{ result_code = $code; operator_hint_code = $null; safe_message = 'Updater stopped safely before applying unverified data.' }
}
