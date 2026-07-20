param(
    [string] $ClanTag,

    [ValidateRange(1, 60)]
    [int] $TimeoutSeconds = 15,

    [switch] $PreviewOnly,

    [switch] $NoPush
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = 'D:\coc\repo'
$RunRoot = 'D:\coc\runs\site_update'
$ApiProbeRoot = 'D:\coc\runs\api_probe'
$HistoryPath = 'D:\coc\data\war_history\history.json'
$LocalConfigPath = 'D:\coc\data\config\clan_site_update.json'
$SiteDataDir = Join-Path $RepoRoot 'site\data'
$LogRoot = 'D:\coc\local\logs\site_update'
$AllowedSiteFiles = @(
    'site/data/roster.json',
    'site/data/current-war.json',
    'site/data/war-log.json',
    'site/data/war-history.json',
    'site/data/site-config.json'
)

function Write-Status {
    param([string] $Message)
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments,
        [Parameter(Mandatory = $true)]
        [string] $Label
    )

    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
}

function Publish-FileAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Source,
        [Parameter(Mandatory = $true)]
        [string] $Destination
    )

    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = Join-Path $parent ('.site-update-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Copy-Item -LiteralPath $Source -Destination $temporary -Force
        Move-Item -LiteralPath $temporary -Destination $Destination -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Restore-Backup {
    param(
        [Parameter(Mandatory = $true)]
        [string] $BackupRoot
    )

    foreach ($relative in $AllowedSiteFiles) {
        $backupFile = Join-Path $BackupRoot ($relative -replace '/', '\')
        $targetFile = Join-Path $RepoRoot ($relative -replace '/', '\')
        $missingMarker = "$backupFile.missing"
        if (Test-Path -LiteralPath $backupFile -PathType Leaf) {
            Publish-FileAtomic -Source $backupFile -Destination $targetFile
        }
        elseif (Test-Path -LiteralPath $missingMarker -PathType Leaf) {
            Remove-Item -LiteralPath $targetFile -Force -ErrorAction SilentlyContinue
        }
    }

    $historyBackup = Join-Path $BackupRoot 'history.json'
    $historyMissing = Join-Path $BackupRoot 'history.json.missing'
    if (Test-Path -LiteralPath $historyBackup -PathType Leaf) {
        Publish-FileAtomic -Source $historyBackup -Destination $HistoryPath
    }
    elseif (Test-Path -LiteralPath $historyMissing -PathType Leaf) {
        Remove-Item -LiteralPath $HistoryPath -Force -ErrorAction SilentlyContinue
    }
}

$createdNew = $false
$mutex = [Threading.Mutex]::new($true, 'Local\ClashClanAnalyticsSiteUpdate', [ref] $createdNew)
if (-not $createdNew) {
    Write-Status 'Another site update is already running. This run is skipped.'
    exit 0
}

$transcriptStarted = $false
try {
    New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
    $logPath = Join-Path $LogRoot ("$(Get-Date -Format 'yyyyMMdd-HHmmss')-update.log")
    Start-Transcript -LiteralPath $logPath -Force | Out-Null
    $transcriptStarted = $true

    Write-Status "Mode: $(if ($PreviewOnly) { 'preview only' } else { 'publish' })"

    if ([string]::IsNullOrWhiteSpace($ClanTag)) {
        if (-not (Test-Path -LiteralPath $LocalConfigPath -PathType Leaf)) {
            throw "Local updater config is missing: $LocalConfigPath"
        }
        $localConfig = Get-Content -LiteralPath $LocalConfigPath -Raw | ConvertFrom-Json
        $ClanTag = [string] $localConfig.clan_tag
    }
    if ($ClanTag -notmatch '^#[A-Z0-9]{3,20}$') {
        throw 'Clan tag in the local config is invalid.'
    }
    Write-Status "Clan tag: [REDACTED]"

    $git = (Get-Command git -ErrorAction Stop).Source
    $python = (Get-Command python -ErrorAction Stop).Source
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $node = if ($null -ne $nodeCommand) { $nodeCommand.Source } else { $null }
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

    foreach ($required in @(
        (Join-Path $RepoRoot 'scripts\api\run_clan_roster_probe.ps1'),
        (Join-Path $RepoRoot 'scripts\api\run_clan_current_war_probe.ps1'),
        (Join-Path $RepoRoot 'scripts\api\run_clan_war_log_probe.ps1'),
        (Join-Path $RepoRoot 'scripts\update\build_site_update.py'),
        (Join-Path $RepoRoot 'site\assets\js\app.js')
    )) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Required file is missing: $required"
        }
    }

    $branch = (& $git -C $RepoRoot branch --show-current | Out-String).Trim()
    if ($branch -cne 'main') {
        throw "Updater requires branch main; current branch is $branch."
    }

    $statusBefore = @(& $git -C $RepoRoot status --porcelain=v1)
    if ($statusBefore.Count -ne 0) {
        throw 'Git working tree must be completely clean before an automated update.'
    }

    $counts = (& $git -C $RepoRoot rev-list --left-right --count origin/main...HEAD | Out-String).Trim() -split '\s+'
    if ($counts.Count -ne 2) {
        throw 'Unable to determine local/origin divergence.'
    }
    $behind = [int] $counts[0]
    $ahead = [int] $counts[1]
    if ($behind -gt 0) {
        throw "Local main is behind origin/main by $behind commit(s). Update it manually first."
    }
    if ($ahead -gt 0 -and -not $PreviewOnly -and -not $NoPush) {
        Write-Status "Publishing $ahead pending local commit(s) before collecting new data."
        Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'push', 'origin', 'main') -Label 'Pending Git push'
    }
    elseif ($ahead -gt 0) {
        throw 'Local main has unpublished commits. Publish or resolve them before preview/no-push mode.'
    }

    New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null
    $runId = "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
    $runDir = Join-Path $RunRoot $runId
    New-Item -ItemType Directory -Path $runDir | Out-Null

    # Existing API probe wrappers require their outputs to stay under
    # D:\coc\runs\api_probe. Orchestration/build artifacts remain under
    # site_update, while each probe uses its approved subtree.
    $rosterDir = Join-Path (Join-Path $ApiProbeRoot 'clan_roster') $runId
    $currentWarDir = Join-Path (Join-Path $ApiProbeRoot 'clan_current_war') $runId
    $warLogDir = Join-Path (Join-Path $ApiProbeRoot 'clan_war_log') $runId
    $buildDir = Join-Path $runDir 'build'

    Write-Status 'Collecting current clan roster (request 1 of 3).'
    Invoke-Checked -FilePath $powershell -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'scripts\api\run_clan_roster_probe.ps1'),
        '-ClanTag', $ClanTag,
        '-OutputDir', $rosterDir,
        '-TimeoutSeconds', $TimeoutSeconds.ToString()
    ) -Label 'Roster probe'

    Write-Status 'Collecting current war (request 2 of 3).'
    Invoke-Checked -FilePath $powershell -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'scripts\api\run_clan_current_war_probe.ps1'),
        '-ClanTag', $ClanTag,
        '-OutputDir', $currentWarDir,
        '-TimeoutSeconds', $TimeoutSeconds.ToString()
    ) -Label 'Current-war probe'

    Write-Status 'Collecting clan war log (request 3 of 3).'
    Invoke-Checked -FilePath $powershell -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'scripts\api\run_clan_war_log_probe.ps1'),
        '-ClanTag', $ClanTag,
        '-OutputDir', $warLogDir,
        '-TimeoutSeconds', $TimeoutSeconds.ToString()
    ) -Label 'War-log probe'

    Write-Status 'Building proposed history and public site JSON.'
    Invoke-Checked -FilePath $python -Arguments @(
        (Join-Path $RepoRoot 'scripts\update\build_site_update.py'),
        '--roster-run', $rosterDir,
        '--current-war-run', $currentWarDir,
        '--war-log-run', $warLogDir,
        '--history-path', $HistoryPath,
        '--site-data-dir', $SiteDataDir,
        '--output-dir', $buildDir
    ) -Label 'Site update builder'

    if ($null -ne $node) {
        Invoke-Checked -FilePath $node -Arguments @(
            '--check', (Join-Path $RepoRoot 'site\assets\js\app.js')
        ) -Label 'JavaScript syntax check'
    }
    else {
        Write-Status 'JavaScript syntax check skipped: Node.js is not installed and app.js is not modified by the hourly data update.'
    }

    Invoke-Checked -FilePath $python -Arguments @(
        '-m', 'unittest', 'discover',
        '-s', (Join-Path $RepoRoot 'tests'),
        '-p', 'test_*.py'
    ) -Label 'Python tests'

    $summary = Get-Content -LiteralPath (Join-Path $buildDir 'summary.json') -Raw | ConvertFrom-Json
    Write-Status "Members: $($summary.members); detailed wars: $($summary.history_wars); current state: $($summary.current_war_state)."

    if ($PreviewOnly) {
        Write-Status "Preview-only update: PASS. Proposed files: $buildDir"
        Write-Status 'Git, persistent history and published site were not changed.'
        exit 0
    }

    $backupRoot = Join-Path $runDir 'backup'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    foreach ($relative in $AllowedSiteFiles) {
        $source = Join-Path $RepoRoot ($relative -replace '/', '\')
        $backup = Join-Path $backupRoot ($relative -replace '/', '\')
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination $backup -Force
        }
        else {
            New-Item -ItemType File -Path "$backup.missing" | Out-Null
        }
    }
    if (Test-Path -LiteralPath $HistoryPath -PathType Leaf) {
        Copy-Item -LiteralPath $HistoryPath -Destination (Join-Path $backupRoot 'history.json') -Force
    }
    else {
        New-Item -ItemType File -Path (Join-Path $backupRoot 'history.json.missing') | Out-Null
    }

    try {
        foreach ($name in @('roster.json', 'current-war.json', 'war-log.json', 'war-history.json', 'site-config.json')) {
            Publish-FileAtomic `
                -Source (Join-Path $buildDir "site-data\$name") `
                -Destination (Join-Path $SiteDataDir $name)
        }
        Publish-FileAtomic `
            -Source (Join-Path $buildDir 'history-next.json') `
            -Destination $HistoryPath

        if ($null -ne $node) {
            Invoke-Checked -FilePath $node -Arguments @(
                '--check', (Join-Path $RepoRoot 'site\assets\js\app.js')
            ) -Label 'Post-publish JavaScript syntax check'
        }
        Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'diff', '--check') -Label 'Git diff check'

        $changedPaths = @(& $git -C $RepoRoot status --porcelain=v1 | ForEach-Object {
            if ($_.Length -ge 4) { $_.Substring(3).Replace('\\', '/') }
        })
        $unexpected = @($changedPaths | Where-Object { $_ -notin $AllowedSiteFiles })
        if ($unexpected.Count -gt 0) {
            throw "Unexpected changed files: $($unexpected -join ', ')"
        }
    }
    catch {
        Restore-Backup -BackupRoot $backupRoot
        throw
    }

    $siteChanges = @(& $git -C $RepoRoot status --porcelain=v1)
    if ($siteChanges.Count -eq 0) {
        Write-Status 'Update: PASS. New API snapshots were stored locally; public site data did not change.'
        Write-Status 'Commit and push were not required.'
        exit 0
    }

    Invoke-Checked -FilePath $git -Arguments (@('-C', $RepoRoot, 'add', '--') + $AllowedSiteFiles) -Label 'Git staging'
    Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'diff', '--cached', '--check') -Label 'Staged diff check'

    $commitMessage = "data: update clan site $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'commit', '-m', $commitMessage) -Label 'Git commit'

    if (-not $NoPush) {
        Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'push', 'origin', 'main') -Label 'Git push'
        Write-Status 'Update: PASS. Public site data committed and pushed.'
    }
    else {
        Write-Status 'Update: PASS. Public site data committed locally; push skipped by -NoPush.'
    }
    Write-Status "Run directory: $runDir"
}
catch {
    Write-Error $_
    exit 2
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    if ($null -ne $mutex) {
        $mutex.ReleaseMutex() | Out-Null
        $mutex.Dispose()
    }
}
