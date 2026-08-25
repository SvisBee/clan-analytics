param(
    [switch] $Json,
    [Parameter(DontShow = $true)][string] $TestHealthRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$healthRoot = 'D:\coc\local\health\clan_games'
if (-not [string]::IsNullOrWhiteSpace($TestHealthRoot)) {
    $candidate = [System.IO.Path]::GetFullPath($TestHealthRoot)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
    if (-not $candidate.StartsWith("$temporaryRoot\", [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Test health root must be inside the system temporary directory.'
    }
    $healthRoot = $candidate
}

function Get-HealthValue {
    param($Record, [string] $Name, $Default = '')
    if ($null -eq $Record) { return $Default }
    $property = $Record.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}
$names = @('latest-run', 'last-scan-success', 'latest-failure', 'latest-warning')
$result = [ordered]@{ schema_version = 1; health_available = $false; records = [ordered]@{} }
foreach ($name in $names) {
    $path = Join-Path $healthRoot ($name + '.json')
    if (-not [System.IO.File]::Exists($path)) { $result.records[$name] = $null; continue }
    try {
        $record = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json -ErrorAction Stop
        $result.records[$name] = $record
        if ($name -eq 'latest-run') { $result.health_available = $true }
    }
    catch {
        $result.records[$name] = [ordered]@{ status = 'unreadable'; result_code = 'health_file_invalid' }
    }
}
if ($Json) { $result | ConvertTo-Json -Depth 8; exit 0 }
if (-not $result.health_available) { Write-Output 'Clan Games collection has not run yet.'; exit 0 }
$latest = $result.records['latest-run']
Write-Output "Clan Games health: $(Get-HealthValue $latest status unknown)"
Write-Output "Result: $(Get-HealthValue $latest result_code unknown)"
Write-Output "Finished (UTC): $(Get-HealthValue $latest finished_at_utc unknown)"
Write-Output "Action: $(Get-HealthValue $latest action unknown)"
Write-Output "Event: $(Get-HealthValue $latest event_id none)"
$invoked = [bool](Get-HealthValue $latest collector_invoked $false)
Write-Output "Collector invoked: $invoked"
if ($invoked) {
    Write-Output "Scan: $(Get-HealthValue $latest scan_kind unknown) / $(Get-HealthValue $latest scan_id unknown)"
    Write-Output "Coverage: requested=$(Get-HealthValue $latest requested_count 0), attempted=$(Get-HealthValue $latest attempted_count 0), successful=$(Get-HealthValue $latest successful_count 0), failed=$(Get-HealthValue $latest failed_count 0), skipped=$(Get-HealthValue $latest skipped_count 0)"
}
$hint = [string](Get-HealthValue $latest operator_hint_code '')
if (-not [string]::IsNullOrWhiteSpace($hint)) {
    Write-Output "Operator hint: $hint"
}
foreach ($name in @('last-scan-success', 'latest-failure', 'latest-warning')) {
    $record = $result.records[$name]
    if ($null -ne $record) { Write-Output "$name`: $(Get-HealthValue $record result_code unknown) at $(Get-HealthValue $record finished_at_utc unknown)" }
}
