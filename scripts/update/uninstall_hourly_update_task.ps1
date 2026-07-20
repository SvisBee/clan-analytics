param(
    [switch] $ConfirmRemoval
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ConfirmRemoval) {
    throw 'Pass -ConfirmRemoval to delete the hourly Windows task.'
}

$taskName = 'Clash Clan Analytics - Hourly Update'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host 'Hourly task is not registered.'
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host 'Hourly task removal: PASS'
Write-Host "Removed: $taskName"
