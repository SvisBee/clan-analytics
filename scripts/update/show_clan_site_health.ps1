param([string] $WorkspaceRoot = 'D:\coc', [switch] $Json)
Set-StrictMode -Version Latest
$root = Join-Path $WorkspaceRoot 'local\health\site_update'
function Read-Health([string] $name) { $p=Join-Path $root $name; if (Test-Path -LiteralPath $p) { Get-Content -LiteralPath $p -Raw | ConvertFrom-Json } else { $null } }
$latest=Read-Health 'latest-run.json'; $success=Read-Health 'last-success.json'; $failure=Read-Health 'latest-failure.json'
$result=[ordered]@{ latest_run=$latest; last_success=$success; latest_failure=$failure }
if ($Json) { $result | ConvertTo-Json -Depth 12; exit 0 }
if ($null -eq $latest) { Write-Output 'Collection health has not been recorded yet.'; exit 0 }
Write-Output "Latest run: $($latest.run_id)"
Write-Output "Latest status: $($latest.status) / $($latest.result_code)"
Write-Output "Latest stage: $($latest.current_stage)"
Write-Output "Last successful normal collection: $(if($success){$success.finished_at_utc}else{'not recorded'})"
if ($success) { Write-Output "Age of last success: $([math]::Round(((Get-Date).ToUniversalTime()-[datetime]$success.finished_at_utc).TotalMinutes,1)) minutes" }
Write-Output "Probes: $($latest.probes | ConvertTo-Json -Compress)"
Write-Output "Builder/tests/apply: $($latest.builder) / $($latest.validation) / $($latest.publication)"
Write-Output "Commit and push: $($latest.publication | ConvertTo-Json -Compress)"
if ($latest.result_code -eq 'api_http_403') { Write-Output 'Сбор не выполнен: Clash API вернул 403. Проверьте, включён ли настроенный разрешённый VPN. Последние опубликованные данные не изменялись.' }
elseif ($latest.operator_hint_code) { Write-Output "Operator hint: $($latest.operator_hint_code)" }
Write-Output "Run directory: $($latest.run_directory)"
