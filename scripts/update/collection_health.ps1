Set-StrictMode -Version Latest

$script:CollectionHealthSchemaVersion = 1
$script:CollectionHealthStages = @('bootstrap', 'mutex', 'git_preflight', 'roster_probe', 'current_war_probe', 'war_log_probe', 'builder', 'public_validation', 'tests', 'snapshot_history', 'atomic_apply', 'git_commit', 'git_push', 'complete')
$script:CollectionHealthResultCodes = @('success', 'no_public_change', 'preview_success', 'mutex_held', 'git_dirty', 'git_branch_ahead', 'git_branch_behind', 'git_branch_diverged', 'api_http_403', 'api_http_other', 'api_transport_failure', 'probe_validation_failure', 'history_preflight_failure', 'builder_failure', 'public_validation_failure', 'tests_failure', 'snapshot_history_success', 'snapshot_history_idempotent', 'snapshot_history_initialization_failure', 'snapshot_history_validation_failure', 'snapshot_history_schema_unsupported', 'snapshot_history_conflict', 'snapshot_history_out_of_order', 'snapshot_history_locked', 'snapshot_history_write_failure', 'snapshot_history_result_write_failure', 'snapshot_history_unexpected_failure', 'snapshot_history_skipped_preview', 'atomic_apply_failure', 'git_commit_failure', 'git_push_failure', 'health_write_failure', 'unexpected_failure')
$script:CollectionHealthRunStopwatches = @{}
$script:CollectionHealthStageStopwatches = @{}

function Get-HealthUtcNow { [DateTimeOffset]::UtcNow.ToString('o') }
function Get-CollectionHealthRoot { param([Parameter(Mandatory = $true)][string] $WorkspaceRoot) Join-Path $WorkspaceRoot 'local\health\site_update' }

function Write-CollectionHealthJsonAtomic {
    param([Parameter(Mandatory = $true)][string] $Path, [Parameter(Mandatory = $true)] $Value)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null
    $temporary = Join-Path $parent ('.health-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try { $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding utf8 -NoNewline -ErrorAction Stop; Move-Item -LiteralPath $temporary -Destination $Path -Force -ErrorAction Stop }
    finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}

function Write-CollectionBootstrapLog {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)][string] $Stage, [Parameter(Mandatory = $true)][string] $ResultCode, [Parameter(Mandatory = $true)][string] $SafeMessage, [Parameter(Mandatory = $true)][int] $ProcessExitCode)
    [ordered]@{ run_id=$Health.run_id; mode=$Health.mode; started_at_utc=$Health.started_at_utc; local_start_time=$Health.local_start_time; process_id=$Health.process_id; stage=$Stage; result_code=$ResultCode; safe_message=$SafeMessage; process_exit_code=$ProcessExitCode; exit_code=$ProcessExitCode } | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $Health.run_directory 'bootstrap.log') -Encoding utf8 -ErrorAction Stop
}

function Save-CollectionHealth {
    param([Parameter(Mandatory = $true)] $Health)
    Write-CollectionHealthJsonAtomic -Path (Join-Path $Health.run_directory 'health.json') -Value $Health
    Write-CollectionHealthJsonAtomic -Path (Join-Path (Get-CollectionHealthRoot -WorkspaceRoot $Health.workspace_root) 'latest-run.json') -Value $Health
}

function New-CollectionHealthRun {
    param([Parameter(Mandatory = $true)][string] $WorkspaceRoot, [Parameter(Mandatory = $true)][string] $RunDirectory, [Parameter(Mandatory = $true)][string] $RunId, [Parameter(Mandatory = $true)][string] $Mode)
    New-Item -ItemType Directory -Path $RunDirectory -Force -ErrorAction Stop | Out-Null
    $health = [pscustomobject][ordered]@{ schema_version=$script:CollectionHealthSchemaVersion; workspace_root=$WorkspaceRoot; run_id=$RunId; mode=$Mode; started_at_utc=Get-HealthUtcNow; local_start_time=(Get-Date).ToString('o'); process_id=$PID; finished_at_utc=$null; duration_seconds=$null; status='running'; current_stage='bootstrap'; result_code='running'; process_exit_code=$null; safe_message='Updater run initialized.'; operator_hint_code=$null; run_directory=$RunDirectory; stages=@(); diagnostics=@(); git_preflight=$null; probes=[ordered]@{}; builder=$null; failure_bundle_created=$false; failure_bundle_capture_status='not_required'; failure_bundle_artifact_count=0; failure_bundle_logical_reference=$null; validation=$null; snapshot_history=$null; publication=$null; freshness=[ordered]@{} }
    $script:CollectionHealthRunStopwatches[$RunId] = [Diagnostics.Stopwatch]::StartNew()
    Write-CollectionBootstrapLog -Health $health -Stage 'bootstrap' -ResultCode 'running' -SafeMessage 'Updater run initialized.' -ProcessExitCode 0
    Save-CollectionHealth -Health $health
    return $health
}

function Start-HealthStage {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)][string] $Stage)
    if ($Stage -notin $script:CollectionHealthStages) { throw "Unsupported health stage: $Stage" }
    $record = [pscustomobject][ordered]@{ stage=$Stage; status='running'; started_at_utc=Get-HealthUtcNow; finished_at_utc=$null; duration_seconds=$null; result_code='running' }
    $Health.stages = @($Health.stages) + @($record)
    $Health.current_stage = $Stage
    $script:CollectionHealthStageStopwatches[$record] = [Diagnostics.Stopwatch]::StartNew()
    Save-CollectionHealth -Health $Health
    return $record
}

function Complete-HealthStage {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)] $StageRecord, [ValidateSet('success','no_change','failed','skipped')][string] $Status, [Parameter(Mandatory = $true)][string] $ResultCode)
    if ($StageRecord.status -ne 'running') { return }
    $timer = $script:CollectionHealthStageStopwatches[$StageRecord]
    $StageRecord.status=$Status; $StageRecord.result_code=$ResultCode; $StageRecord.finished_at_utc=Get-HealthUtcNow
    $StageRecord.duration_seconds=[math]::Round($(if($timer){$timer.Elapsed.TotalSeconds}else{0}),3)
    if($timer){$timer.Stop();$script:CollectionHealthStageStopwatches.Remove($StageRecord)}
    Save-CollectionHealth -Health $Health
}
function Fail-HealthStage { param($Health,$StageRecord,[string]$ResultCode) Complete-HealthStage -Health $Health -StageRecord $StageRecord -Status failed -ResultCode $ResultCode }
function Skip-HealthStage { param($Health,$StageRecord,[string]$ResultCode) Complete-HealthStage -Health $Health -StageRecord $StageRecord -Status skipped -ResultCode $ResultCode }
function Add-CollectionHealthDiagnostic { param($Health,[string]$Stage,[string]$Code,[string]$SafeMessage,[int]$ProcessExitCode) $Health.diagnostics=@($Health.diagnostics)+@([pscustomobject][ordered]@{stage=$Stage;code=$Code;safe_message=$SafeMessage;process_exit_code=$ProcessExitCode}); Write-CollectionBootstrapLog -Health $Health -Stage $Stage -ResultCode $Code -SafeMessage $SafeMessage -ProcessExitCode $ProcessExitCode; Save-CollectionHealth -Health $Health }

function Finalize-HealthRun {
    param([Parameter(Mandatory = $true)] $Health, [Parameter(Mandatory = $true)][string] $Status, [Parameter(Mandatory = $true)][string] $ResultCode, [Parameter(Mandatory = $true)][string] $SafeMessage, [string] $OperatorHintCode, [Parameter(Mandatory = $true)][int] $ProcessExitCode)
    $complete = Start-HealthStage -Health $Health -Stage complete
    $stageStatus = if($Status -eq 'failed'){'failed'}elseif($Status -eq 'skipped'){'skipped'}elseif($Status -eq 'no_change'){'no_change'}else{'success'}
    Complete-HealthStage -Health $Health -StageRecord $complete -Status $stageStatus -ResultCode $ResultCode
    $timer=$script:CollectionHealthRunStopwatches[$Health.run_id]
    $Health.status=$Status; $Health.current_stage='complete'; $Health.result_code=$ResultCode; $Health.process_exit_code=$ProcessExitCode; $Health.safe_message=$SafeMessage; $Health.operator_hint_code=$OperatorHintCode; $Health.finished_at_utc=Get-HealthUtcNow; $Health.duration_seconds=[math]::Round($(if($timer){$timer.Elapsed.TotalSeconds}else{0}),3)
    if($timer){$timer.Stop();$script:CollectionHealthRunStopwatches.Remove($Health.run_id)}
    Write-CollectionBootstrapLog -Health $Health -Stage complete -ResultCode $ResultCode -SafeMessage $SafeMessage -ProcessExitCode $ProcessExitCode
    Save-CollectionHealth -Health $Health
    $root=Get-CollectionHealthRoot -WorkspaceRoot $Health.workspace_root
    if($Health.mode -eq 'normal' -and $Status -in @('success','no_change')) { Write-CollectionHealthJsonAtomic -Path (Join-Path $root 'last-success.json') -Value $Health; $failure=Join-Path $root 'latest-failure.json'; if(Test-Path -LiteralPath $failure){$previous=Get-Content -LiteralPath $failure -Raw|ConvertFrom-Json;$previous|Add-Member -NotePropertyName resolved_at_utc -NotePropertyValue $Health.finished_at_utc -Force;Write-CollectionHealthJsonAtomic -Path $failure -Value $previous} } elseif($Health.mode -eq 'normal' -and $Status -eq 'failed'){Write-CollectionHealthJsonAtomic -Path (Join-Path $root 'latest-failure.json') -Value $Health}
}

# Compatibility wrappers keep existing isolated callers on the centralized
# lifecycle while the updater uses the explicit Start/Complete/Finalize names.
function Add-CollectionHealthStage { param($Health,[string]$Stage) Start-HealthStage -Health $Health -Stage $Stage }
function Complete-CollectionHealthStage { param($Health,$StageRecord,[string]$Status,[string]$ResultCode) Complete-HealthStage -Health $Health -StageRecord $StageRecord -Status $Status -ResultCode $ResultCode }
function Complete-CollectionHealth { param($Health,[string]$Status,[string]$ResultCode,[string]$SafeMessage,[string]$OperatorHintCode,[int]$ExitCode = 0) Finalize-HealthRun -Health $Health -Status $Status -ResultCode $ResultCode -SafeMessage $SafeMessage -OperatorHintCode $OperatorHintCode -ProcessExitCode $ExitCode }

function Get-CollectionHealthFailure {
    param([Parameter(Mandatory = $true)][string] $Stage, [Parameter(Mandatory = $true)][string] $Text)
    if($Stage -in @('roster_probe','current_war_probe','war_log_probe')) { if($Text -match 'HTTP request failed with status 403'){return [ordered]@{result_code='api_http_403';operator_hint_code='enable_approved_vpn';safe_message='Clash API rejected the request with HTTP 403. This installation usually requires the approved VPN for collection. Token and API settings were not changed.'}};if($Text -match 'HTTP request failed with status'){return [ordered]@{result_code='api_http_other';operator_hint_code=$null;safe_message='Clash API returned an HTTP error. No data was applied.'}};return [ordered]@{result_code='api_transport_failure';operator_hint_code=$null;safe_message='Clash API probe did not complete. No data was applied.'} }
    if($Stage -eq 'snapshot_history') { foreach($code in @('snapshot_history_initialization_failure','snapshot_history_validation_failure','snapshot_history_schema_unsupported','snapshot_history_conflict','snapshot_history_out_of_order','snapshot_history_locked','snapshot_history_write_failure','snapshot_history_result_write_failure','snapshot_history_unexpected_failure')) { if($Text -match $code){return [ordered]@{result_code=$code;operator_hint_code=$null;safe_message='Confirmed roster history was not recorded; public data was not applied.'}} }; return [ordered]@{result_code='snapshot_history_unexpected_failure';operator_hint_code=$null;safe_message='Confirmed roster history was not recorded; public data was not applied.'} }
    $codes=@{'git_preflight'='git_dirty';'builder'='builder_failure';'public_validation'='public_validation_failure';'tests'='tests_failure';'atomic_apply'='atomic_apply_failure';'git_commit'='git_commit_failure';'git_push'='git_push_failure';'mutex'='mutex_held';'bootstrap'='history_preflight_failure'};$code=if($codes.ContainsKey($Stage)){$codes[$Stage]}else{'unexpected_failure'};return [ordered]@{result_code=$code;operator_hint_code=$null;safe_message='Updater stopped safely before applying unverified data.'}
}
