param(
    [switch] $ConfirmSchedule,
    [bool] $WakeComputer = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ConfirmSchedule) {
    throw 'Pass -ConfirmSchedule to register the hourly Windows task.'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows Task Scheduler is available only on Windows.'
}

$taskName = 'Clash Clan Analytics - Hourly Update'
$scriptPath = 'D:\coc\repo\scripts\update\update_clan_site.ps1'
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Updater script is missing: $scriptPath"
}
$configPath = 'D:\coc\data\config\clan_site_update.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Local updater config is missing: $configPath"
}

$powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
    '-File "D:\coc\repo\scripts\update\update_clan_site.ps1"'
$action = New-ScheduledTaskAction `
    -Execute $powerShellPath `
    -Argument $arguments `
    -WorkingDirectory 'D:\coc\repo'

$now = Get-Date
$nextHour = $now.Date.AddHours($now.Hour + 1)
$hourly = New-ScheduledTaskTrigger `
    -Once `
    -At $nextHour `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$atLogOn = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settingsParameters = @{
    StartWhenAvailable = $true
    MultipleInstances = 'IgnoreNew'
    ExecutionTimeLimit = (New-TimeSpan -Minutes 30)
    RunOnlyIfNetworkAvailable = $true
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
}
if ($WakeComputer) {
    $settingsParameters.WakeToRun = $true
}
$settings = New-ScheduledTaskSettingsSet @settingsParameters
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($hourly, $atLogOn) `
    -Settings $settings `
    -Principal $principal `
    -Description 'Updates Clash clan roster, current war, war log, local history and GitHub Pages data.'

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop

Write-Host 'Hourly task registration: PASS'
Write-Host "Task: $($registered.TaskName)"
Write-Host 'Frequency: hourly plus user logon'
Write-Host "Wake computer: $([bool]$WakeComputer)"
Write-Host 'Multiple instances: IgnoreNew'
Write-Host 'Missed run: StartWhenAvailable'
Write-Host 'The task runs only while this Windows user is logged on.'
