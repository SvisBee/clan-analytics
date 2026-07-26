param([string] $WorkspaceRoot = 'D:\coc', [switch] $Json)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Join-Path $WorkspaceRoot 'local\health\site_update'

function Read-Health([string] $Name) {
    $path = Join-Path $root $Name
    if (Test-Path -LiteralPath $path) { return Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json }
    return $null
}

function ConvertTo-OperatorHealth($Health) {
    if ($null -eq $Health) { return $null }
    return [ordered]@{
        schema_version = $Health.schema_version
        run_id = $Health.run_id
        mode = $Health.mode
        started_at_utc = $Health.started_at_utc
        finished_at_utc = $Health.finished_at_utc
        duration_seconds = $Health.duration_seconds
        status = $Health.status
        current_stage = $Health.current_stage
        result_code = $Health.result_code
        process_exit_code = $Health.process_exit_code
        safe_message = $Health.safe_message
        operator_hint_code = $Health.operator_hint_code
        logical_run_path = "runs/site_update/$($Health.run_id)"
        health_file = 'health.json'
        stages = $Health.stages
        git_preflight = $Health.git_preflight
        probes = $Health.probes
        builder = $Health.builder
        validation = $Health.validation
        publication = $Health.publication
        freshness = $Health.freshness
    }
}

try {
    $latest = ConvertTo-OperatorHealth (Read-Health 'latest-run.json')
    $success = ConvertTo-OperatorHealth (Read-Health 'last-success.json')
    $failure = ConvertTo-OperatorHealth (Read-Health 'latest-failure.json')
    $result = [ordered]@{ latest_run=$latest; last_success=$success; latest_failure=$failure }
    if ($Json) { $result | ConvertTo-Json -Depth 12; exit 0 }
    if ($null -eq $latest) { Write-Output 'Collection health has not been recorded yet.'; exit 0 }
    Write-Output "Latest run: $($latest.run_id)"
    Write-Output "Latest status: $($latest.status) / $($latest.result_code)"
    Write-Output "Latest stage: $($latest.current_stage)"
    Write-Output "Last successful normal collection: $(if($success){$success.finished_at_utc}else{'not recorded'})"
    if ($success) { Write-Output "Age of last success: $([math]::Round(([DateTimeOffset]::UtcNow-[DateTimeOffset]$success.finished_at_utc).TotalMinutes,1)) minutes" }
    Write-Output "Probes: $($latest.probes | ConvertTo-Json -Compress)"
    Write-Output "Builder/tests/apply: $($latest.builder) / $($latest.validation) / $($latest.publication)"
    Write-Output "Commit and push: $($latest.publication | ConvertTo-Json -Compress)"
    if ($latest.result_code -eq 'api_http_403') { Write-Output 'Сбор не выполнен: Clash API вернул 403. Проверьте, включён ли настроенный разрешённый VPN. Последние опубликованные данные не изменялись.' }
    elseif ($latest.operator_hint_code) { Write-Output "Operator hint: $($latest.operator_hint_code)" }
    Write-Output "Run: $($latest.logical_run_path)/$($latest.health_file)"
}
catch {
    if ($Json) { [ordered]@{ error='Collection health is unavailable.' } | ConvertTo-Json -Compress } else { Write-Output 'Collection health is unavailable.' }
    exit 1
}
