param([string] $WorkspaceRoot = 'D:\coc', [switch] $Json)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Join-Path $WorkspaceRoot 'local\health\site_update'
$warnings = [System.Collections.Generic.List[string]]::new()
$summaryNames = @('latest-run.json', 'last-success.json', 'latest-failure.json')
$anySummaryFile = @($summaryNames | Where-Object { Test-Path -LiteralPath (Join-Path $root $_) }).Count -gt 0

function Get-OptionalHealthProperty($Record, [string] $Name, $Default = $null) {
    if ($null -eq $Record) { return $Default }
    $property = $Record.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Read-HealthRecord([string] $Name) {
    $path = Join-Path $root $Name
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
    catch { $script:warnings.Add("$Name is unavailable."); return $null }
}

function ConvertTo-OperatorHealth($Health, [string] $SourceName) {
    if ($null -eq $Health) { return $null }
    try {
        $runId = Get-OptionalHealthProperty $Health 'run_id'
        if ([string]::IsNullOrWhiteSpace([string]$runId)) { throw 'missing run ID' }
        $hasExitCode = $null -ne $Health.PSObject.Properties['process_exit_code']
        $legacy = -not $hasExitCode
        if ($legacy) { $script:warnings.Add("$SourceName uses legacy health schema.") }
        return [ordered]@{
            schema_version = Get-OptionalHealthProperty $Health 'schema_version'
            legacy_record = $legacy
            run_id = $runId
            mode = Get-OptionalHealthProperty $Health 'mode'
            started_at_utc = Get-OptionalHealthProperty $Health 'started_at_utc'
            finished_at_utc = Get-OptionalHealthProperty $Health 'finished_at_utc'
            duration_seconds = Get-OptionalHealthProperty $Health 'duration_seconds'
            status = Get-OptionalHealthProperty $Health 'status'
            current_stage = Get-OptionalHealthProperty $Health 'current_stage'
            result_code = Get-OptionalHealthProperty $Health 'result_code'
            process_exit_code = Get-OptionalHealthProperty $Health 'process_exit_code'
            safe_message = Get-OptionalHealthProperty $Health 'safe_message'
            operator_hint_code = Get-OptionalHealthProperty $Health 'operator_hint_code'
            logical_run_path = "runs/site_update/$runId"
            health_file = 'health.json'
            stages = Get-OptionalHealthProperty $Health 'stages' @()
            git_preflight = Get-OptionalHealthProperty $Health 'git_preflight'
            probes = Get-OptionalHealthProperty $Health 'probes'
            builder = Get-OptionalHealthProperty $Health 'builder'
            validation = Get-OptionalHealthProperty $Health 'validation'
            snapshot_history = Get-OptionalHealthProperty $Health 'snapshot_history'
            publication = Get-OptionalHealthProperty $Health 'publication'
            freshness = Get-OptionalHealthProperty $Health 'freshness'
        }
    }
    catch { $script:warnings.Add("$SourceName could not be projected."); return $null }
}

$latest = ConvertTo-OperatorHealth (Read-HealthRecord 'latest-run.json') 'latest-run'
$success = ConvertTo-OperatorHealth (Read-HealthRecord 'last-success.json') 'last-success'
$failure = ConvertTo-OperatorHealth (Read-HealthRecord 'latest-failure.json') 'latest-failure'
$available = ($null -ne $latest -or $null -ne $success -or $null -ne $failure)
$result = [ordered]@{ status = if($available){'available'}else{'unavailable'}; latest_run=$latest; last_success=$success; latest_failure=$failure; warnings=@($warnings) }
if ($Json) { $result | ConvertTo-Json -Depth 12; if($available -or -not $anySummaryFile){exit 0}else{exit 1} }
if (-not $available) {
    if (-not $anySummaryFile) { Write-Output 'Collection health has not been recorded yet.'; exit 0 }
    Write-Output 'Collection health is unavailable.'; $warnings | ForEach-Object { Write-Output "Warning: $_" }; exit 1
}
if ($latest) {
    Write-Output "Latest run: $($latest.run_id)"
    Write-Output "Latest status: $($latest.status) / $($latest.result_code)"
    Write-Output "Latest process exit: $(if($null -eq $latest.process_exit_code){'unavailable (legacy record)'}else{$latest.process_exit_code})"
    Write-Output "Latest stage: $($latest.current_stage)"
    Write-Output "Probes: $($latest.probes | ConvertTo-Json -Compress)"
    Write-Output "Builder/tests/apply: $($latest.builder) / $($latest.validation) / $($latest.publication)"
    if ($null -eq $latest.snapshot_history) {
        Write-Output 'Snapshot history: not recorded (legacy run or PreviewOnly).'
    }
    else {
        Write-Output "Snapshot history: $($latest.snapshot_history.status) / $($latest.snapshot_history.result_code); database $($latest.snapshot_history.logical_database_path); observation recorded $($latest.snapshot_history.inserted_observation); store initialized $($latest.snapshot_history.initialized_store)"
    }
    Write-Output "Commit and push: $($latest.publication | ConvertTo-Json -Compress)"
    Write-Output "Run: $($latest.logical_run_path)/$($latest.health_file)"
}
Write-Output "Last successful normal collection: $(if($success){$success.finished_at_utc}else{'not recorded'})"
if ($success -and $success.finished_at_utc) { Write-Output "Age of last success: $([math]::Round(([DateTimeOffset]::UtcNow-[DateTimeOffset]$success.finished_at_utc).TotalMinutes,1)) minutes" }
if ($failure) { Write-Output "Latest failure: $($failure.run_id) / $($failure.result_code) / exit $(if($null -eq $failure.process_exit_code){'unavailable (legacy record)'}else{$failure.process_exit_code})" }
if ($latest -and $latest.result_code -eq 'api_http_403') { Write-Output 'Сбор не выполнен: Clash API вернул 403. Проверьте, включён ли настроенный разрешённый VPN. Последние опубликованные данные не изменялись.' }
elseif ($latest -and $latest.operator_hint_code) { Write-Output "Operator hint: $($latest.operator_hint_code)" }
$warnings | ForEach-Object { Write-Output "Warning: $_" }
