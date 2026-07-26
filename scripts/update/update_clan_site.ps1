param(
    [string] $ClanTag,

    [string] $WorkspaceRoot = 'D:\coc',

    [ValidateRange(1, 60)]
    [int] $TimeoutSeconds = 15,

    [switch] $PreviewOnly,

    [switch] $NoPush
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'workspace_mutex.ps1')
. (Join-Path $PSScriptRoot 'collection_health.ps1')
. (Join-Path $PSScriptRoot 'native_process.ps1')

$RepoRoot = Join-Path $WorkspaceRoot 'repo'
$RunRoot = Join-Path $WorkspaceRoot 'runs\site_update'
$ApiProbeRoot = Join-Path $WorkspaceRoot 'runs\api_probe'
$HistoryPath = Join-Path $WorkspaceRoot 'data\war_history\history.json'
$SnapshotDatabasePath = Join-Path $WorkspaceRoot 'data\clan_snapshot_history\clan_snapshot_history.v1.sqlite3'
$LocalConfigPath = Join-Path $WorkspaceRoot 'data\config\clan_site_update.json'
$SiteDataDir = Join-Path $RepoRoot 'site\data'
$LogRoot = Join-Path $WorkspaceRoot 'local\logs\site_update'
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

    $result = Invoke-NativeProcess -FilePath $FilePath -Arguments $Arguments
    $script:LastCheckedOutput = @($result.stderr_safe)
    if (-not [string]::IsNullOrWhiteSpace($result.stdout)) { Write-Output $result.stdout }
    if (-not [string]::IsNullOrWhiteSpace($result.stderr_safe)) {
        Add-CollectionHealthDiagnostic -Health $health -Stage $currentStage -Code 'native_stderr' -SafeMessage "$Label reported stderr: $($result.stderr_safe)" -ProcessExitCode $result.process_exit_code
        Write-Status "$Label diagnostic: $($result.stderr_safe)"
    }
    if (-not $result.succeeded) { throw "$Label failed with exit code $($result.process_exit_code)." }
    return $result
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

function Test-HistorySchemaPreflight {
    $result = Invoke-NativeProcess -FilePath $python -Arguments @((Join-Path $RepoRoot 'scripts\update\validate_war_history.py'), '--source', $HistoryPath)
    $script:LastCheckedOutput = @($result.stderr_safe)
    if (-not [string]::IsNullOrWhiteSpace($result.stderr_safe)) { Add-CollectionHealthDiagnostic -Health $health -Stage 'bootstrap' -Code 'native_stderr' -SafeMessage "History validation reported stderr: $($result.stderr_safe)" -ProcessExitCode $result.process_exit_code }
    if (-not $result.succeeded) {
        throw "History validation preflight failed before network: $HistoryPath"
    }
}

$mode = if ($PreviewOnly) { 'preview' } else { 'normal' }
$script:LastCheckedOutput = @()
New-Item -ItemType Directory -Path $RunRoot -Force | Out-Null
$runId = "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$runDir = Join-Path $RunRoot $runId
$health = New-CollectionHealthRun -WorkspaceRoot $WorkspaceRoot -RunDirectory $runDir -RunId $runId -Mode $mode
$currentStage = 'bootstrap'
$currentHealthStage = Start-HealthStage -Health $health -Stage 'bootstrap'

$createdNew = $false
$mutexName = Get-WorkspaceMutexName -WorkspaceRoot $WorkspaceRoot
$mutex = [Threading.Mutex]::new($true, $mutexName, [ref] $createdNew)
if (-not $createdNew) {
    Write-Status 'Another site update is already running. This run is skipped.'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $currentStage = 'mutex'; $currentHealthStage = Start-HealthStage -Health $health -Stage 'mutex'
    Skip-HealthStage -Health $health -StageRecord $currentHealthStage -ResultCode 'mutex_held'
    Finalize-HealthRun -Health $health -Status 'skipped' -ResultCode 'mutex_held' -SafeMessage 'Another updater run already holds the workspace mutex.' -ProcessExitCode 0
    $mutex.Dispose()
    exit 0
}
$mutexStage = Start-HealthStage -Health $health -Stage 'mutex'
Complete-HealthStage -Health $health -StageRecord $mutexStage -Status success -ResultCode success

$transcriptStarted = $false
try {
    New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
    $logPath = Join-Path $LogRoot ("$(Get-Date -Format 'yyyyMMdd-HHmmss')-update.log")
    Start-Transcript -LiteralPath $logPath -Force | Out-Null
    $transcriptStarted = $true

    Write-Status "Mode: $(if ($PreviewOnly) { 'preview only' } else { 'publish' })"

    $python = (Get-Command python -ErrorAction Stop).Source

    # This must remain before local API configuration and all probe invocations.
    Test-HistorySchemaPreflight
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success

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
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $node = if ($null -ne $nodeCommand) { $nodeCommand.Source } else { $null }
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

    foreach ($required in @(
        (Join-Path $RepoRoot 'scripts\api\run_clan_roster_probe.ps1'),
        (Join-Path $RepoRoot 'scripts\api\run_clan_current_war_probe.ps1'),
        (Join-Path $RepoRoot 'scripts\api\run_clan_war_log_probe.ps1'),
        (Join-Path $RepoRoot 'scripts\update\build_site_update.py'),
        (Join-Path $RepoRoot 'scripts\update\record_clan_snapshot_history.py'),
        (Join-Path $RepoRoot 'site\assets\js\app.js'),
        (Join-Path $RepoRoot 'site\assets\js\current-war-contract.js'),
        (Join-Path $RepoRoot 'scripts\update\validate_war_history.py'),
        (Join-Path $RepoRoot 'scripts\update\check_update_git_state.py'),
        (Join-Path $RepoRoot 'scripts\update\native_process.ps1')
    )) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Required file is missing: $required"
        }
    }

    $currentStage = 'git_preflight'
    $currentHealthStage = Start-HealthStage -Health $health -Stage 'git_preflight'
    $gitPreflightResult = Invoke-NativeProcess -FilePath $python -Arguments @((Join-Path $RepoRoot 'scripts\update\check_update_git_state.py'), '--repo', $RepoRoot, '--json')
    $script:LastCheckedOutput = @($gitPreflightResult.stderr_safe)
    $gitPreflightText = $gitPreflightResult.stdout
    $health.git_preflight = $gitPreflightText | ConvertFrom-Json
    if (-not $gitPreflightResult.succeeded) {
        Fail-HealthStage -Health $health -StageRecord $currentHealthStage -ResultCode $health.git_preflight.result_code
        throw 'Git preflight failed before API probes.'
    }
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success

    # Existing API probe wrappers require their outputs to stay under
    # D:\coc\runs\api_probe. Orchestration/build artifacts remain under
    # site_update, while each probe uses its approved subtree.
    $rosterDir = Join-Path (Join-Path $ApiProbeRoot 'clan_roster') $runId
    $currentWarDir = Join-Path (Join-Path $ApiProbeRoot 'clan_current_war') $runId
    $warLogDir = Join-Path (Join-Path $ApiProbeRoot 'clan_war_log') $runId
    $buildDir = Join-Path $runDir 'build'

    Write-Status 'Collecting current clan roster (request 1 of 3).'
    $currentStage = 'roster_probe'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    Invoke-Checked -FilePath $powershell -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'scripts\api\run_clan_roster_probe.ps1'),
        '-ClanTag', $ClanTag,
        '-OutputDir', $rosterDir,
        '-TimeoutSeconds', $TimeoutSeconds.ToString()
    ) -Label 'Roster probe'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $health.probes.roster = [ordered]@{ status='success'; result_code='success' }

    Write-Status 'Collecting current war (request 2 of 3).'
    $currentStage = 'current_war_probe'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    Invoke-Checked -FilePath $powershell -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'scripts\api\run_clan_current_war_probe.ps1'),
        '-ClanTag', $ClanTag,
        '-OutputDir', $currentWarDir,
        '-TimeoutSeconds', $TimeoutSeconds.ToString()
    ) -Label 'Current-war probe'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $health.probes.current_war = [ordered]@{ status='success'; result_code='success' }

    Write-Status 'Collecting clan war log (request 3 of 3).'
    $currentStage = 'war_log_probe'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    Invoke-Checked -FilePath $powershell -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $RepoRoot 'scripts\api\run_clan_war_log_probe.ps1'),
        '-ClanTag', $ClanTag,
        '-OutputDir', $warLogDir,
        '-TimeoutSeconds', $TimeoutSeconds.ToString()
    ) -Label 'War-log probe'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $health.probes.war_log = [ordered]@{ status='success'; result_code='success' }

    Write-Status 'Building proposed history and public site JSON.'
    $currentStage = 'builder'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    Invoke-Checked -FilePath $python -Arguments @(
        (Join-Path $RepoRoot 'scripts\update\build_site_update.py'),
        '--roster-run', $rosterDir,
        '--current-war-run', $currentWarDir,
        '--war-log-run', $warLogDir,
        '--history-path', $HistoryPath,
        '--site-data-dir', $SiteDataDir,
        '--output-dir', $buildDir
    ) -Label 'Site update builder'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $health.builder = [ordered]@{ status='success'; result_code='success' }

    $currentStage = 'public_validation'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    if ($null -ne $node) {
        Invoke-Checked -FilePath $node -Arguments @(
            '--check', (Join-Path $RepoRoot 'site\assets\js\app.js')
        ) -Label 'JavaScript syntax check'
        Invoke-Checked -FilePath $node -Arguments @(
            '--check', (Join-Path $RepoRoot 'site\assets\js\current-war-contract.js')
        ) -Label 'Current-war contract syntax check'
    }
    else {
        Write-Status 'JavaScript syntax check skipped: Node.js is not installed and app.js is not modified by the hourly data update.'
    }
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success

    $currentStage = 'tests'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    Invoke-Checked -FilePath $python -Arguments @(
        '-m', 'unittest', 'discover',
        '-s', (Join-Path $RepoRoot 'tests'),
        '-p', 'test_*.py'
    ) -Label 'Python tests'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $health.validation = [ordered]@{ status='success'; result_code='success'; tests='success' }

    $summary = Get-Content -LiteralPath (Join-Path $buildDir 'summary.json') -Raw | ConvertFrom-Json
    Write-Status "Members: $($summary.members); detailed wars: $($summary.history_wars); current state: $($summary.current_war_state)."

    if ($PreviewOnly) {
        Write-Status "Preview-only update: PASS. Proposed files: $buildDir"
        Write-Status 'Git, persistent history and published site were not changed.'
        Finalize-HealthRun -Health $health -Status success -ResultCode 'preview_success' -SafeMessage 'Preview-only collection completed; no persistent or public data was changed.' -ProcessExitCode 0
        exit 0
    }

    # This normal-only local persistence boundary consumes the already verified
    # roster probe. It performs no additional API request and precedes every
    # public file/history/Git mutation.
    $currentStage = 'snapshot_history'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    $snapshotResultPath = Join-Path $runDir 'snapshot-history-result.json'
    $snapshotProcess = Invoke-NativeProcess -FilePath $python -Arguments @(
        (Join-Path $RepoRoot 'scripts\update\record_clan_snapshot_history.py'),
        '--roster-json', (Join-Path $rosterDir 'raw_clan_response.json'),
        '--roster-metadata', (Join-Path $rosterDir 'probe_metadata.json'),
        '--database', $SnapshotDatabasePath,
        '--workspace-root', $WorkspaceRoot,
        '--source-run-id', $runId,
        '--result-json', $snapshotResultPath
    )
    $script:LastCheckedOutput = @($snapshotProcess.stderr_safe)
    if (-not $snapshotProcess.succeeded) { throw "Snapshot history recording failed: $($snapshotProcess.stderr_safe)" }
    if (-not (Test-Path -LiteralPath $snapshotResultPath -PathType Leaf)) { throw 'snapshot_history_result_write_failure' }
    try { $snapshotResult = Get-Content -LiteralPath $snapshotResultPath -Raw | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'snapshot_history_result_write_failure' }
    if ($snapshotResult.status -ne 'success' -or $snapshotResult.result_code -notin @('snapshot_history_success', 'snapshot_history_idempotent')) { throw [string]$snapshotResult.result_code }
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode $snapshotResult.result_code
    $currentHealthStage | Add-Member -NotePropertyName process_exit_code -NotePropertyValue 0 -Force
    $health.snapshot_history = [ordered]@{ status='success'; result_code=$snapshotResult.result_code; logical_database_path='data/clan_snapshot_history/clan_snapshot_history.v1.sqlite3'; initialized_store=[bool]$snapshotResult.initialized_store; inserted_payload=[bool]$snapshotResult.inserted_payload; inserted_observation=[bool]$snapshotResult.inserted_observation; observation_id=[string]$snapshotResult.observation_id; observed_at_utc=[string]$snapshotResult.observed_at_utc; recorded_at_utc=[string]$snapshotResult.recorded_at_utc; process_exit_code=0; safe_message=[string]$snapshotResult.safe_message }

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
        $currentStage = 'atomic_apply'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
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
            Invoke-Checked -FilePath $node -Arguments @(
                '--check', (Join-Path $RepoRoot 'site\assets\js\current-war-contract.js')
            ) -Label 'Post-publish current-war contract syntax check'
        }
        Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'diff', '--check') -Label 'Git diff check'

        $changedPaths = @(& $git -C $RepoRoot status --porcelain=v1 | ForEach-Object {
            if ($_.Length -ge 4) { $_.Substring(3).Replace('\\', '/') }
        })
        $unexpected = @($changedPaths | Where-Object { $_ -notin $AllowedSiteFiles })
        if ($unexpected.Count -gt 0) {
            throw "Unexpected changed files: $($unexpected -join ', ')"
        }
        Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
        $health.publication = [ordered]@{ apply='success'; commit='not_required'; push='not_required' }
    }
    catch {
        Restore-Backup -BackupRoot $backupRoot
        throw
    }

    $siteChanges = @(& $git -C $RepoRoot status --porcelain=v1)
    if ($siteChanges.Count -eq 0) {
        Write-Status 'Update: PASS. New API snapshots were stored locally; public site data did not change.'
        Write-Status 'Commit and push were not required.'
        Finalize-HealthRun -Health $health -Status no_change -ResultCode 'no_public_change' -SafeMessage 'Collection succeeded; public data did not change.' -ProcessExitCode 0
        exit 0
    }

    $currentStage = 'git_commit'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
    Invoke-Checked -FilePath $git -Arguments (@('-C', $RepoRoot, 'add', '--') + $AllowedSiteFiles) -Label 'Git staging'
    Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'diff', '--cached', '--check') -Label 'Staged diff check'

    $commitMessage = "data: update clan site $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'commit', '-m', $commitMessage) -Label 'Git commit'
    Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
    $health.publication.commit = 'success'

    if (-not $NoPush) {
        $currentStage = 'git_push'; $currentHealthStage = Start-HealthStage -Health $health -Stage $currentStage
        Invoke-Checked -FilePath $git -Arguments @('-C', $RepoRoot, 'push', 'origin', 'main') -Label 'Git push'
        Complete-HealthStage -Health $health -StageRecord $currentHealthStage -Status success -ResultCode success
        $health.publication.push = 'success'
        Write-Status 'Update: PASS. Public site data committed and pushed.'
    }
    else {
        Write-Status 'Update: PASS. Public site data committed locally; push skipped by -NoPush.'
    }
    Write-Status "Run directory: $runDir"
    $health.freshness = [ordered]@{ last_successful_normal_collection_utc = Get-HealthUtcNow; last_public_apply_utc = Get-HealthUtcNow; last_push_utc = if ($NoPush) { $null } else { Get-HealthUtcNow } }
    Finalize-HealthRun -Health $health -Status success -ResultCode success -SafeMessage 'Collection, validation and publication completed.' -ProcessExitCode 0
}
catch {
    $failureText = ((@($script:LastCheckedOutput) + @($_.ToString())) | ForEach-Object { $_.ToString() }) -join "`n"
    $failure = if ($currentStage -eq 'git_preflight' -and $null -ne $health.git_preflight -and -not $health.git_preflight.ok) {
        [ordered]@{ result_code = $health.git_preflight.result_code; operator_hint_code = $null; safe_message = $health.git_preflight.safe_message }
    } else { Get-CollectionHealthFailure -Stage $currentStage -Text $failureText }
    try {
        if ($null -ne $currentHealthStage -and $currentHealthStage.status -eq 'running') { Fail-HealthStage -Health $health -StageRecord $currentHealthStage -ResultCode $failure.result_code }
        if ($currentStage -in @('roster_probe', 'current_war_probe', 'war_log_probe')) { $health.probes[$currentStage] = [ordered]@{ status='failed'; result_code=$failure.result_code } }
        if ($currentStage -eq 'snapshot_history') { $currentHealthStage | Add-Member -NotePropertyName process_exit_code -NotePropertyValue 1 -Force; $health.snapshot_history = [ordered]@{ status='failed'; result_code=$failure.result_code; logical_database_path='data/clan_snapshot_history/clan_snapshot_history.v1.sqlite3'; process_exit_code=1; safe_message=$failure.safe_message } }
        Finalize-HealthRun -Health $health -Status failed -ResultCode $failure.result_code -SafeMessage $failure.safe_message -OperatorHintCode $failure.operator_hint_code -ProcessExitCode 1
    }
    catch { [Console]::Error.WriteLine('Collection health write failed: health_write_failure.') }
    [Console]::Error.WriteLine($_.ToString())
    exit 1
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
