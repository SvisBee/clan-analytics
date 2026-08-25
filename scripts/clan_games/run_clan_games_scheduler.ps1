Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'clan_games_scheduler.ps1')

$workspaceRoot = 'D:\coc'
$python = (Get-Command python -ErrorAction Stop).Source
$planner = 'D:\coc\repo\scripts\clan_games\plan_clan_games_scan.py'
$collector = 'D:\coc\repo\scripts\clan_games\run_games_champion_collector.ps1'
$healthRoot = 'D:\coc\local\health\clan_games'
foreach ($required in @($planner, $collector)) {
    if (-not [System.IO.File]::Exists($required)) { throw 'Required Clan Games entrypoint is missing.' }
}
$code = Invoke-ClanGamesScheduler -WorkspaceRoot $workspaceRoot -PythonPath $python `
    -PlannerPath $planner -CollectorPath $collector -HealthRoot $healthRoot
exit $code
