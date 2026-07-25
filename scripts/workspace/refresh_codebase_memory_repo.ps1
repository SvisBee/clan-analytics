[CmdletBinding()]
param(
    [string] $Project = 'D-coc-repo',
    [string] $RepoPath = 'D:/coc/repo',
    [string[]] $ControlPath = @(),
    [string[]] $ControlPhrase = @(),
    [switch] $ConfirmStopProcesses,
    [switch] $Force,
    [ValidateRange(60, 7200)][int] $TimeoutSeconds = 1800
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:CanonicalBinary = 'C:\Users\nshhi\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe'
$script:CacheRoot = 'C:\Users\nshhi\.cache\codebase-memory-mcp'

function New-CbmFailure {
    param([int] $Code, [string] $Message)
    $exception = [System.Exception]::new($Message)
    $exception.Data['CbmExitCode'] = $Code
    throw $exception
}

function Get-CbmExitCode {
    param($Exception)
    if ($Exception.Data.Contains('CbmExitCode')) { return [int] $Exception.Data['CbmExitCode'] }
    return 23
}

function Normalize-CbmRoot {
    param([Parameter(Mandatory)][string] $Path)
    return ([IO.Path]::GetFullPath($Path.Replace('/', '\')).TrimEnd('\', '/') -replace '\\', '/')
}

function Get-CbmProjectFiles {
    param([Parameter(Mandatory)][string] $TargetProject)
    if ($TargetProject -notmatch '^[A-Za-z0-9-]+$') { New-CbmFailure 20 'Project name contains unsafe characters.' }
    return @(
        (Join-Path $script:CacheRoot "$TargetProject.db"),
        (Join-Path $script:CacheRoot "$TargetProject.db-wal"),
        (Join-Path $script:CacheRoot "$TargetProject.db-shm")
    )
}

function Get-CbmFileMetadata {
    param([Parameter(Mandatory)][string] $Path)
    $item = Get-Item -LiteralPath $Path -Force
    [pscustomobject]@{
        Name = $item.Name; Length = $item.Length
        LastWriteTimeUtc = $item.LastWriteTimeUtc.ToString('o')
        Sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    }
}

function Normalize-CbmControlPaths {
    param([string[]] $Paths, [string] $Root)
    $rootFull = [IO.Path]::GetFullPath($Root.Replace('/', '\')).TrimEnd('\', '/')
    $prefix = $rootFull + '\'
    $normalized = @()
    foreach ($value in $Paths) {
        if ([string]::IsNullOrWhiteSpace($value)) { New-CbmFailure 20 'ControlPath cannot contain an empty value.' }
        $candidate = if ([IO.Path]::IsPathRooted($value)) { [IO.Path]::GetFullPath($value) } else { [IO.Path]::GetFullPath((Join-Path $rootFull $value)) }
        if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { New-CbmFailure 20 "Control path is outside repo: $value" }
        if ($candidate -match '(^|[\\/])\.\.([\\/]|$)') { New-CbmFailure 20 "Control path contains ..: $value" }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { New-CbmFailure 20 "Control path is not an existing file: $value" }
        $normalized += [IO.Path]::GetRelativePath($rootFull, $candidate).Replace('\', '/')
    }
    return @($normalized)
}

function Get-CbmProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name='codebase-memory-mcp.exe'" | ForEach-Object {
        [pscustomobject]@{
            ProcessId = [int] $_.ProcessId; ParentProcessId = [int] $_.ParentProcessId
            ExecutablePath = [string] $_.ExecutablePath; CommandLine = [string] $_.CommandLine
            CreationDate = [string] $_.CreationDate
        }
    })
}

function Stop-CbmProcesses {
    param([object[]] $Processes)
    foreach ($processInfo in @($Processes)) {
        Stop-Process -Id $processInfo.ProcessId -Force -ErrorAction Stop
    }
    $deadline = (Get-Date).AddSeconds(30)
    do { Start-Sleep -Milliseconds 250; $remaining = @(Get-CbmProcesses) } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) { New-CbmFailure 21 "Codebase Memory processes remain: $($remaining.ProcessId -join ', ')." }
}

function Invoke-CbmNative {
    param([Parameter(Mandatory)][string] $Arguments, [AllowNull()][string] $StandardInput,
        [Parameter(Mandatory)][string] $StdoutPath, [Parameter(Mandatory)][string] $StderrPath,
        [Parameter(Mandatory)][int] $Timeout, [Parameter(Mandatory)][string] $Label)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $script:CanonicalBinary; $info.Arguments = $Arguments
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    try {
        if (-not $process.Start()) { New-CbmFailure 23 "$Label could not start." }
        if ($null -ne $StandardInput) { $process.StandardInput.Write($StandardInput) }
        $process.StandardInput.Close(); $stdoutTask = $process.StandardOutput.ReadToEndAsync(); $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($Timeout * 1000)) { try { $process.Kill() } catch {}; New-CbmFailure 23 "$Label timed out." }
        if (-not [Threading.Tasks.Task]::WaitAll(@([Threading.Tasks.Task]$stdoutTask, [Threading.Tasks.Task]$stderrTask), 5000)) { New-CbmFailure 23 "$Label stream drain timed out." }
        [IO.File]::WriteAllText($StdoutPath, $stdoutTask.Result, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($StderrPath, $stderrTask.Result, [Text.UTF8Encoding]::new($false))
        if ($process.ExitCode -ne 0) { New-CbmFailure 23 "$Label exited with code $($process.ExitCode)." }
        return $stdoutTask.Result
    } finally { $process.Dispose() }
}

function Get-CbmSafePreview {
    param([AllowNull()][string] $Text, [int] $Limit = 240)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '<empty>' }
    $safe = [regex]::Replace($Text, '(?i)\b(authorization|password|passwd|pwd|token|api[_-]?key|secret)(\s*[:=]\s*)(?:"[^"]*"|''[^'']*''|\S+)', '$1$2[REDACTED]')
    $safe = [regex]::Replace($safe, '(?i)\bBearer\s+\S+', 'Bearer [REDACTED]')
    if ($safe.Length -gt $Limit) { return $safe.Substring(0, $Limit) + ' ...[truncated]' }
    return $safe
}

function Get-CbmJsonFileDiagnostics {
    param([Parameter(Mandatory)][string] $Path)
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    $bytes = [IO.File]::ReadAllBytes($Path)
    $bom = if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { 'UTF-8' } else { 'none' }
    $encoding = 'UTF-8'
    try { $null = [Text.UTF8Encoding]::new($false, $true).GetString($bytes, $(if ($bom -eq 'UTF-8') { 3 } else { 0 }), $bytes.Length - $(if ($bom -eq 'UTF-8') { 3 } else { 0 })) }
    catch { $encoding = 'invalid-UTF-8' }
    # Get-Content -Raw deliberately produces one string; do not parse a string[].
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding utf8 -ErrorAction Stop
    [pscustomobject]@{
        path = $item.FullName; size = [long]$item.Length; encoding = $encoding; bom = $bom
        line_count = if ($bytes.Length -eq 0) { 0 } else { @($raw -split "`r?`n").Count }
        preview_start = Get-CbmSafePreview ($raw.Substring(0, [Math]::Min(240, $raw.Length)))
        preview_end = Get-CbmSafePreview ($raw.Substring([Math]::Max(0, $raw.Length - 240)))
        raw = $raw
    }
}

function ConvertFrom-CbmProjectsFile {
    param([Parameter(Mandatory)][string] $Path, [AllowNull()][System.Collections.IDictionary] $Manifest)
    $diagnostic = $null
    try {
        $diagnostic = Get-CbmJsonFileDiagnostics -Path $Path
        if ($diagnostic.size -le 0) { throw 'stdout file is empty.' }
        if ($diagnostic.encoding -ne 'UTF-8') { throw "stdout file is not valid UTF-8 ($($diagnostic.encoding))." }
        # JsonDocument rejects a second document and any non-whitespace prefix/suffix.
        $document = [Text.Json.JsonDocument]::Parse([string]$diagnostic.raw)
        try {
            if ($document.RootElement.ValueKind -notin @([Text.Json.JsonValueKind]::Object, [Text.Json.JsonValueKind]::Array)) { throw 'JSON root must be an object or array.' }
        } finally { $document.Dispose() }
        $parsed = $diagnostic.raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        $reason = $_.Exception.Message
        if ($null -ne $diagnostic) {
            $ManifestData = [ordered]@{ path=$diagnostic.path; size=$diagnostic.size; encoding=$diagnostic.encoding; bom=$diagnostic.bom; line_count=$diagnostic.line_count; parse_failure=$reason; preview_start=$diagnostic.preview_start; preview_end=$diagnostic.preview_end }
            if ($null -ne $Manifest) { $Manifest['projects_after_json'] = $ManifestData }
            New-CbmFailure 25 "list_projects returned invalid JSON: file=$($diagnostic.path); size=$($diagnostic.size); encoding=$($diagnostic.encoding); bom=$($diagnostic.bom); reason=$reason"
        }
        New-CbmFailure 25 "list_projects returned invalid JSON: could not read $Path; reason=$reason"
    }
    if ($null -ne $parsed.PSObject.Properties['projects']) { return @($parsed.projects) }
    return @($parsed)
}

function Get-CbmStructuredResult {
    param([string] $Json)
    try { $parsed = $Json | ConvertFrom-Json } catch { New-CbmFailure 24 'Indexer stdout is not valid JSON.' }
    if ($null -ne $parsed.PSObject.Properties['structuredContent']) { return $parsed.structuredContent }
    if ($null -ne $parsed.PSObject.Properties['content'] -and @($parsed.content).Count -gt 0) { return (($parsed.content[0].text) | ConvertFrom-Json) }
    return $parsed
}

function Find-ExactCbmProject {
    param([object[]] $Projects, [string] $TargetProject, [string] $ExpectedRoot, [int] $FailureCode)
    $matches = @($Projects | Where-Object { $_.name -ceq $TargetProject })
    if ($matches.Count -ne 1) { New-CbmFailure $FailureCode "Expected exactly one project named $TargetProject; found $($matches.Count)." }
    $projectInfo = $matches[0]
    if ((Normalize-CbmRoot ([string]$projectInfo.root_path)) -cne $ExpectedRoot) { New-CbmFailure $FailureCode "Project root does not equal $ExpectedRoot." }
    return $projectInfo
}

function Backup-CbmGraph {
    param([string] $TargetProject, [string] $BackupDirectory)
    $files = @(Get-CbmProjectFiles $TargetProject | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($files.Count -eq 0) { return [pscustomobject]@{ Present = $false; Files = @(); Path = $BackupDirectory } }
    New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
    $metadata = @()
    foreach ($file in $files) {
        $destination = Join-Path $BackupDirectory ([IO.Path]::GetFileName($file))
        Copy-Item -LiteralPath $file -Destination $destination -Force
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) { New-CbmFailure 22 "Backup copy is missing: $destination" }
        $metadata += Get-CbmFileMetadata $destination
    }
    return [pscustomobject]@{ Present = $true; Files = @($metadata); Path = $BackupDirectory }
}

function Remove-CbmGraph { param([string] $TargetProject) foreach ($file in (Get-CbmProjectFiles $TargetProject)) { if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force } } }

function Restore-CbmGraph {
    param([string] $TargetProject, [string] $BackupDirectory, [object[]] $BackupFiles)
    if (@($BackupFiles).Count -eq 0) { return $false }
    Remove-CbmGraph $TargetProject
    foreach ($metadata in @($BackupFiles)) {
        $source = Join-Path $BackupDirectory $metadata.Name; $destination = Join-Path $script:CacheRoot $metadata.Name
        Copy-Item -LiteralPath $source -Destination $destination -Force
        if ((Get-CbmFileMetadata $destination).Sha256 -ne $metadata.Sha256) { New-CbmFailure 26 "Restored graph hash mismatch: $($metadata.Name)" }
    }
    return $true
}

function Invoke-CbmCleanFullRebuild {
    param([string] $TargetProject, [string] $TargetRoot, [string] $RunRoot, [string] $StatePath,
        [string[]] $Paths, [string[]] $Phrases, [switch] $RequireGit, [switch] $UseHeadNoChange,
        [switch] $Confirm, [switch] $RebuildForce, [int] $IndexTimeout)
    $exitCode = 0; $runDirectory = $null; $backup = [pscustomobject]@{ Present = $false; Files = @(); Path = $null }; $removed = $false
    $manifest = [ordered]@{ project = $TargetProject; repo_path = (Normalize-CbmRoot $TargetRoot); started_at = (Get-Date).ToString('o'); rollback = 'not-needed' }
    try {
        $root = Normalize-CbmRoot $TargetRoot
        if (-not (Test-Path -LiteralPath $script:CanonicalBinary -PathType Leaf)) { New-CbmFailure 20 "Canonical binary was not found: $script:CanonicalBinary" }
        if ($RequireGit -and -not (Test-Path -LiteralPath (Join-Path ($root -replace '/', '\\') '.git'))) { New-CbmFailure 20 "Git directory is missing under $root." }
        if ($Paths.Count -ne $Phrases.Count) { New-CbmFailure 20 'ControlPath and ControlPhrase counts must match.' }
        $controls = @(Normalize-CbmControlPaths -Paths $Paths -Root $root)
        $gitHead = if ($RequireGit) { (git -C ($root -replace '/', '\\') rev-parse HEAD).Trim() } else { $null }
        if ($RequireGit -and $LASTEXITCODE -ne 0) { New-CbmFailure 20 'Could not obtain Git HEAD.' }
        if ($UseHeadNoChange -and -not $RebuildForce -and (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
            try { $state = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json } catch { New-CbmFailure 20 'Last-success state is invalid JSON.' }
            if ($state.project -ceq $TargetProject -and $state.repo_path -ceq $root -and $state.git_head -ceq $gitHead) { Write-Host "NO_CHANGE: HEAD $gitHead matches $StatePath (exit 10)."; return 10 }
        }
        $preflightOut = Join-Path ([IO.Path]::GetTempPath()) ("cbm-$([guid]::NewGuid()).out")
        $preflightErr = "$preflightOut.err"
        try { $null = Invoke-CbmNative -Arguments 'cli list_projects' -StandardInput $null -StdoutPath $preflightOut -StderrPath $preflightErr -Timeout 60 -Label 'preflight list_projects'; $projects = ConvertFrom-CbmProjectsFile -Path $preflightOut -Manifest $null } finally { Remove-Item -LiteralPath $preflightOut,$preflightErr -Force -ErrorAction SilentlyContinue }
        $existing = @($projects | Where-Object { $_.name -ceq $TargetProject })
        if ($existing.Count -gt 0) { $null = Find-ExactCbmProject $projects $TargetProject $root 20 }
        if (-not $Confirm) { New-CbmFailure 20 'Close Codex, then rerun with -ConfirmStopProcesses. No process or graph files were changed.' }
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'; $runDirectory = Join-Path (Join-Path $RunRoot (Get-Date -Format 'yyyy-MM-dd')) "${stamp}_clean_full"
        New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null; $manifest.run_dir = $runDirectory; $manifest.git_head = $gitHead; $manifest.control_paths = $controls
        $processes = @(Get-CbmProcesses); $manifest.processes_before_stop = $processes; Stop-CbmProcesses $processes; $manifest.processes_after_stop = @(Get-CbmProcesses)
        $backup = Backup-CbmGraph $TargetProject (Join-Path $runDirectory 'backup'); $manifest.backup_present = $backup.Present; $manifest.backup_files = $backup.Files
        Remove-CbmGraph $TargetProject; $removed = $true
        $oldWorkers = [Environment]::GetEnvironmentVariable('CBM_WORKERS', 'Process'); $oldBudget = [Environment]::GetEnvironmentVariable('CBM_MEM_BUDGET_MB', 'Process')
        try { [Environment]::SetEnvironmentVariable('CBM_WORKERS', '1', 'Process'); [Environment]::SetEnvironmentVariable('CBM_MEM_BUDGET_MB', '3000', 'Process'); $payload = @{ repo_path = $root; name = $TargetProject; mode = 'full' } | ConvertTo-Json -Compress; $indexJson = Invoke-CbmNative -Arguments 'cli index_repository' -StandardInput $payload -StdoutPath (Join-Path $runDirectory 'index.stdout.log') -StderrPath (Join-Path $runDirectory 'index.stderr.log') -Timeout $IndexTimeout -Label 'index_repository' } finally { [Environment]::SetEnvironmentVariable('CBM_WORKERS', $oldWorkers, 'Process'); [Environment]::SetEnvironmentVariable('CBM_MEM_BUDGET_MB', $oldBudget, 'Process') }
        $index = Get-CbmStructuredResult $indexJson; $stderr = Get-Content -Raw -LiteralPath (Join-Path $runDirectory 'index.stderr.log')
        if ($index.status -ne 'indexed' -or [long]$index.skipped_count -ne 0 -or [long]$index.nodes -le 0 -or [long]$index.edges -le 0 -or [long]$index.nodes -ne [long]$index.expected_nodes -or [long]$index.edges -ne [long]$index.expected_edges -or $stderr -match '(?im)\b(error|fatal)\b') { New-CbmFailure 24 'Indexer actual/expected or clean-worker validation failed.' }
        $projectsAfterPath = Join-Path $runDirectory 'projects_after.json'
        $null = Invoke-CbmNative -Arguments 'cli list_projects' -StandardInput $null -StdoutPath $projectsAfterPath -StderrPath (Join-Path $runDirectory 'projects_after.stderr.log') -Timeout 60 -Label 'postflight list_projects'
        $persisted = Find-ExactCbmProject (ConvertFrom-CbmProjectsFile -Path $projectsAfterPath -Manifest $manifest) $TargetProject $root 25
        if ($RequireGit -and -not [bool]$persisted.git.is_git) { New-CbmFailure 25 'Persisted project is not Git-backed.' }
        if ([long]$persisted.nodes -ne [long]$index.expected_nodes -or [long]$persisted.edges -ne [long]$index.expected_edges) { New-CbmFailure 25 'Persisted project counts do not equal expected counts.' }
        $postflight = [ordered]@{ project=$TargetProject; repo_path=$root; git_head=$gitHead; control_paths=$controls; control_phrases=$Phrases; postflight_required_after_codex_restart=$true }
        $postflight | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runDirectory 'postflight_controls.json') -Encoding utf8
        if ($StatePath) { New-Item -ItemType Directory -Path (Split-Path -Parent $StatePath) -Force | Out-Null; ([ordered]@{ project=$TargetProject; repo_path=$root; git_head=$gitHead; finished_at=(Get-Date).ToString('o'); nodes=[long]$persisted.nodes; edges=[long]$persisted.edges; run_dir=$runDirectory; control_paths=$controls } | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $StatePath -Encoding utf8 }
        $manifest.result = 'PASS'; $manifest.nodes = [long]$persisted.nodes; $manifest.edges = [long]$persisted.edges
        Write-Host "PASS: $TargetProject"; Write-Host "Root: $root"; Write-Host "Nodes: $($persisted.nodes)"; Write-Host "Edges: $($persisted.edges)"; Write-Host "Run directory: $runDirectory"; Write-Host $(if($backup.Present){"Backup retained: $($backup.Path)"}else{'Backup retained: none (initial build).' }); Write-Host 'Open Codex and perform Phase 2 content postflight.'
    } catch {
        $exitCode = Get-CbmExitCode $_.Exception; $manifest.result = 'FAIL'; $manifest.error = $_.Exception.Message
        if ($removed) { try { if (Restore-CbmGraph $TargetProject $backup.Path $backup.Files) { $manifest.rollback = 'restored' } else { $manifest.rollback = 'unavailable-initial-build' } } catch { $manifest.rollback = 'failed'; $manifest.rollback_error = $_.Exception.Message; $exitCode = 26 } }
        Write-Host "FAIL: $($_.Exception.Message)"; if ($runDirectory) { Write-Host "Run directory: $runDirectory"; Write-Host "Rollback: $($manifest.rollback)" }; Write-Host "Exit code: $exitCode"
    } finally {
        $manifest.finished_at = (Get-Date).ToString('o')
        if ($runDirectory) { $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runDirectory 'refresh.manifest.json') -Encoding utf8; @("result=$($manifest.result)", "exit_code=$exitCode", "project=$TargetProject", "repo_path=$(Normalize-CbmRoot $TargetRoot)", "run_dir=$runDirectory", "rollback=$($manifest.rollback)") | Set-Content -LiteralPath (Join-Path $runDirectory 'refresh.status.txt') -Encoding utf8 }
    }
    return [int]$exitCode
}

if ($MyInvocation.InvocationName -ne '.') {
    [int]$code = Invoke-CbmCleanFullRebuild -TargetProject $Project -TargetRoot $RepoPath -RunRoot 'D:\coc\runs\codebase_memory_repo_refresh' -StatePath 'D:\coc\runs\codebase_memory_repo_refresh\state\D-coc-repo.last_success.json' -Paths $ControlPath -Phrases $ControlPhrase -RequireGit -UseHeadNoChange -Confirm:$ConfirmStopProcesses -RebuildForce:$Force -IndexTimeout $TimeoutSeconds
    exit $code
}
