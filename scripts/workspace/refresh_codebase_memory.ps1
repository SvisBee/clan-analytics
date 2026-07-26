[CmdletBinding()]
param(
    [string] $Project = 'D-coc',
    [string] $WorkspacePath = 'D:/coc',
    [string[]] $ControlPath = @(),
    [string[]] $ControlPhrase = @(),
    [switch] $ConfirmCodexClosed,
    [switch] $ConfirmStopProcesses,
    [ValidateRange(60, 7200)][int] $TimeoutSeconds = 3600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The daily file supplies a local implementation only. Dot-sourcing does not run
# its entrypoint, and no dependency on D:\work is introduced. Its param block is
# isolated by saving and restoring this entrypoint's inputs.
$broadProject = $Project; $broadWorkspacePath = $WorkspacePath
$broadControlPath = @($ControlPath); $broadControlPhrase = @($ControlPhrase)
$broadConfirm = $ConfirmStopProcesses; $broadTimeout = $TimeoutSeconds
. "$PSScriptRoot\refresh_codebase_memory_repo.ps1"
$Project = $broadProject; $WorkspacePath = $broadWorkspacePath
$ControlPath = $broadControlPath; $ControlPhrase = $broadControlPhrase
$ConfirmStopProcesses = $broadConfirm; $TimeoutSeconds = $broadTimeout

if ($Project -cne 'D-coc') { throw 'Broad maintenance script only permits project D-coc.' }
if ((Normalize-CbmRoot $WorkspacePath) -cne 'D:/coc') { throw 'Broad maintenance script only permits root D:/coc.' }
if (-not $ConfirmCodexClosed) { throw 'Close Codex completely, then rerun with -ConfirmCodexClosed. No process or graph files were changed.' }
$codexProcesses = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @('codex', 'codex-code-mode-host') })
if ($codexProcesses.Count -gt 0) { throw 'Codex is still running. Close Codex completely before the broad clean rebuild.' }

[int]$code = Invoke-CbmCleanFullRebuild -TargetProject $Project -TargetRoot $WorkspacePath -RunRoot 'D:\coc\runs\codebase_memory_broad_rebuild' -StatePath '' -Paths $ControlPath -Phrases $ControlPhrase -Confirm:$ConfirmStopProcesses -IndexTimeout $TimeoutSeconds
exit $code
