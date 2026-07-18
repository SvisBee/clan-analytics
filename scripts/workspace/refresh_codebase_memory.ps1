[CmdletBinding()]
param(
    [ValidateRange(10, 600)][int]$ListTimeoutSeconds = 60,
    [ValidateRange(60, 7200)][int]$IndexTimeoutSeconds = 1800,
    [ValidateRange(10, 600)][int]$ArchitectureTimeoutSeconds = 120,
    [ValidateRange(100, 60000)][int]$StreamDrainTimeoutMs = 5000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectName = 'D-coc'
$workspaceRoot = 'D:/coc'
$workspacePath = 'D:\coc'
$gitRoot = 'D:\coc\repo'
$ignorePath = 'D:\coc\.cbmignore'
$requiredIgnoreRules = @(
    '**/.git/**',
    '**/.codebase-memory/**',
    '/data/**',
    '/runs/**',
    '/local/**',
    '/repo/site/data/**',
    '**/.env',
    '**/.env.*',
    '**/*.pem',
    '**/*.key',
    '**/*.crt',
    '**/*.p12',
    '**/*.pfx',
    '**/*.sqlite',
    '**/*.sqlite3',
    '**/*.db',
    '**/*.log',
    '**/*.tmp',
    '**/*.bak',
    '**/*.zip',
    '**/*.7z',
    '**/*.csv',
    '**/*.xlsx',
    '**/*.xls',
    '**/*.parquet',
    '**/*.jsonl',
    '**/__pycache__/**',
    '**/.pytest_cache/**',
    '**/.mypy_cache/**',
    '**/.ruff_cache/**',
    '**/.venv/**',
    '**/venv/**',
    '**/node_modules/**',
    '**/dist/**',
    '**/build/**',
    '**/coverage/**',
    '**/htmlcov/**'
)

$script:CurrentStage = 'initialization'
$script:IndexRuns = 0

function ConvertTo-HexHash {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    return (($Bytes | ForEach-Object { $_.ToString('x2') }) -join '').ToUpperInvariant()
}

function Get-TextHash {
    param([Parameter(Mandatory = $true)][string]$Text)

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ConvertTo-HexHash -Bytes ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))
    }
    finally {
        $sha.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ConvertTo-HexHash -Bytes ($sha.ComputeHash($stream))
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-RelativeWorkspacePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $root = [IO.Path]::GetFullPath($workspacePath).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the fixed workspace: $fullPath"
    }
    return $fullPath.Substring($root.Length).Replace('\', '/')
}

function Test-IsExcludedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $path = $RelativePath.Replace('\', '/').TrimStart('/')
    if ($path -match '(^|/)\.git(/|$)' -or $path -match '(^|/)\.codebase-memory(/|$)') { return $true }
    if ($path -match '^(data|runs|local)(/|$)' -or $path -match '^repo/site/data(/|$)') { return $true }
    if ($path -match '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.venv|venv|node_modules|dist|build|coverage|htmlcov)(/|$)') { return $true }

    $name = [IO.Path]::GetFileName($path)
    if ($name -eq '.env' -or $name.StartsWith('.env.', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    if ($name -match '(?i)\.(pem|key|crt|p12|pfx|sqlite|sqlite3|db|log|tmp|bak|zip|7z|csv|xlsx|xls|parquet|jsonl)$') { return $true }
    return $false
}

function Get-ContentManifestHash {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$MarkdownOnly
    )

    $rows = [Collections.Generic.List[string]]::new()
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File -ErrorAction Stop)) {
        $relative = Get-RelativeWorkspacePath -Path $file.FullName
        if (Test-IsExcludedPath -RelativePath $relative) { continue }
        if ($MarkdownOnly -and $file.Extension -ne '.md') { continue }
        $rows.Add("$relative|$(Get-FileSha256 -Path $file.FullName)")
    }
    $sorted = $rows.ToArray()
    [Array]::Sort($sorted, [StringComparer]::Ordinal)
    return Get-TextHash -Text ($sorted -join "`n")
}

function Get-WorkspaceInventoryHash {
    $rows = [Collections.Generic.List[string]]::new()
    foreach ($file in @(Get-ChildItem -LiteralPath $workspacePath -Recurse -Force -File -ErrorAction Stop)) {
        $relative = Get-RelativeWorkspacePath -Path $file.FullName
        if ($relative -match '(^|/)\.git(/|$)' -or $relative -match '(^|/)\.codebase-memory(/|$)') { continue }
        $rows.Add($relative)
    }
    $sorted = $rows.ToArray()
    [Array]::Sort($sorted, [StringComparer]::Ordinal)
    return Get-TextHash -Text ($sorted -join "`n")
}

function Wait-BoundedStreamTasks {
    param(
        [Parameter(Mandatory = $true)][System.Threading.Tasks.Task]$StdoutTask,
        [Parameter(Mandatory = $true)][System.Threading.Tasks.Task]$StderrTask,
        [Parameter(Mandatory = $true)][int]$TimeoutMs,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $tasks = [System.Threading.Tasks.Task[]]@($StdoutTask, $StderrTask)
    if (-not [System.Threading.Tasks.Task]::WaitAll($tasks, $TimeoutMs)) {
        throw "$Label STREAM_DRAIN_TIMEOUT"
    }
    if ($StdoutTask.IsFaulted -or $StderrTask.IsFaulted -or $StdoutTask.IsCanceled -or $StderrTask.IsCanceled) {
        throw "$Label STREAM_READ_FAILED"
    }
    return [pscustomobject]@{ Stdout = $StdoutTask.Result; Stderr = $StderrTask.Result }
}

function Invoke-CbmCli {
    param(
        [Parameter(Mandatory = $true)][string]$Tool,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [AllowNull()][string]$StandardInput
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'codebase-memory-mcp'
    $startInfo.Arguments = "cli $Tool"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.RedirectStandardInput = $true
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw "$Tool PROCESS_START_FAILED" }
        if ($null -ne $StandardInput) { $process.StandardInput.Write($StandardInput) }
        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch {}
            try { $null = $process.WaitForExit(5000) } catch {}
            $null = Wait-BoundedStreamTasks -StdoutTask $stdoutTask -StderrTask $stderrTask -TimeoutMs $StreamDrainTimeoutMs -Label $Tool
            throw "$Tool PROCESS_TIMEOUT"
        }

        $streams = Wait-BoundedStreamTasks -StdoutTask $stdoutTask -StderrTask $stderrTask -TimeoutMs $StreamDrainTimeoutMs -Label $Tool
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $streams.Stdout
            Stderr = $streams.Stderr
        }
    }
    finally {
        $process.Dispose()
    }
}

function Assert-CbmProcessSucceeded {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][Collections.Generic.List[string]]$Warnings
    )

    if ($Result.ExitCode -ne 0) { throw "$Label failed with exit code $($Result.ExitCode)." }
    foreach ($line in @($Result.Stderr -split '\r?\n')) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed -match '^level=info msg=mem\.init') { continue }
        $Warnings.Add("$Label reported stderr output.")
        break
    }
}

function ConvertFrom-CbmJson {
    param(
        [Parameter(Mandatory = $true)][string]$Json,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ([string]::IsNullOrWhiteSpace($Json)) { throw "$Label returned empty output." }
    try { return $Json | ConvertFrom-Json }
    catch { throw "$Label returned invalid JSON." }
}

function Get-CbmStructuredContent {
    param(
        [Parameter(Mandatory = $true)]$Parsed,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $isErrorProperty = $Parsed.PSObject.Properties['isError']
    if ($null -ne $isErrorProperty -and [bool]$isErrorProperty.Value) { throw "$Label returned isError=true." }
    $structuredProperty = $Parsed.PSObject.Properties['structuredContent']
    if ($null -ne $structuredProperty -and $null -ne $structuredProperty.Value) { return $structuredProperty.Value }
    $contentProperty = $Parsed.PSObject.Properties['content']
    if ($null -ne $contentProperty -and @($contentProperty.Value).Count -gt 0) {
        $textProperty = @($contentProperty.Value)[0].PSObject.Properties['text']
        if ($null -ne $textProperty -and -not [string]::IsNullOrWhiteSpace([string]$textProperty.Value)) {
            return ConvertFrom-CbmJson -Json ([string]$textProperty.Value) -Label "$Label content"
        }
    }
    return $Parsed
}

function ConvertTo-ProjectList {
    param([Parameter(Mandatory = $true)]$Parsed)

    $projectsProperty = $Parsed.PSObject.Properties['projects']
    if ($null -ne $projectsProperty) { return @($projectsProperty.Value) }
    return @($Parsed)
}

function Get-NormalizedRoot {
    param([AllowNull()][string]$Root)
    return ($Root -replace '\\', '/').TrimEnd('/')
}

function Assert-ProjectSet {
    param([Parameter(Mandatory = $true)][object[]]$Projects)

    $exactName = @($Projects | Where-Object { [string]$_.name -ceq $projectName })
    if ($exactName.Count -ne 1) { throw "Expected exactly one existing project named $projectName; found $($exactName.Count)." }
    $actualRoot = Get-NormalizedRoot -Root ([string]$exactName[0].root_path)
    if ($actualRoot -cne $workspaceRoot) { throw "Project $projectName has root '$actualRoot', expected '$workspaceRoot'." }

    $sameRoot = @($Projects | Where-Object { (Get-NormalizedRoot -Root ([string]$_.root_path)) -ieq $workspaceRoot })
    if ($sameRoot.Count -ne 1 -or [string]$sameRoot[0].name -cne $projectName) {
        throw "A duplicate or conflicting project uses root $workspaceRoot."
    }

    $similar = @($Projects | Where-Object {
        $name = [string]$_.name
        $canonical = ($name.ToLowerInvariant() -replace '[-_]', '')
        $name -cne $projectName -and $canonical -in @('dcoc', 'coc')
    })
    if ($similar.Count -gt 0) { throw 'A similarly named Codebase Memory project exists.' }
    return $exactName[0]
}

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = @(& git -C $gitRoot @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Git command failed: $($Arguments -join ' ')" }
    return ($output -join "`n").TrimEnd()
}

function Assert-IgnoreFile {
    if (-not (Test-Path -LiteralPath $ignorePath -PathType Leaf)) { throw "Required ignore file is missing: $ignorePath" }
    $lines = @(Get-Content -LiteralPath $ignorePath -ErrorAction Stop)
    $missing = @($requiredIgnoreRules | Where-Object { $_ -notin $lines })
    if ($missing.Count -gt 0) { throw ".cbmignore is missing $($missing.Count) required rule(s)." }
}

function Assert-ArchitectureSafe {
    param([Parameter(Mandatory = $true)]$Structured)

    $treeProperty = $Structured.PSObject.Properties['file_tree']
    if ($null -eq $treeProperty) { throw 'Architecture result did not include file_tree.' }
    $forbidden = @()
    $fileCount = 0
    foreach ($entry in @($treeProperty.Value)) {
        $path = ([string]$entry.path).Replace('\', '/').TrimStart('/')
        if ([string]$entry.type -eq 'file') { $fileCount++ }
        if ($path -match '^(data|runs|local)(/|$)' -or
            $path -match '(^|/)\.git(/|$)' -or
            $path -match '(^|/)\.codebase-memory(/|$)' -or
            $path -match '^repo/site/data(/|$)' -or
            [IO.Path]::GetFileName($path) -eq '.env' -or
            [IO.Path]::GetFileName($path) -match '(?i)^\.env\.' -or
            $path -match '(?i)\.(db|sqlite|sqlite3|log|csv|xlsx|xls|parquet|jsonl)$') {
            $forbidden += $path
        }
    }
    if ($forbidden.Count -gt 0) { throw "Architecture tree contains $($forbidden.Count) forbidden path(s)." }
    return $fileCount
}

function Invoke-CodebaseMemoryRefresh {
    $warnings = [Collections.Generic.List[string]]::new()
    $oldWorkers = [Environment]::GetEnvironmentVariable('CBM_WORKERS', 'Process')
    $oldMemoryBudget = [Environment]::GetEnvironmentVariable('CBM_MEM_BUDGET_MB', 'Process')
    $hadWorkers = $null -ne $oldWorkers
    $hadMemoryBudget = $null -ne $oldMemoryBudget

    try {
        $script:CurrentStage = 'preflight'
        if (-not (Test-Path -LiteralPath $workspacePath -PathType Container)) { throw "Workspace is missing: $workspacePath" }
        if (-not (Test-Path -LiteralPath $gitRoot -PathType Container)) { throw "Git root is missing: $gitRoot" }
        Assert-IgnoreFile

        $actualGitRoot = (Invoke-Git -Arguments @('rev-parse', '--show-toplevel')).Replace('\', '/').TrimEnd('/')
        if ($actualGitRoot -ine 'D:/coc/repo') { throw "Unexpected Git root: $actualGitRoot" }
        $statusBefore = Invoke-Git -Arguments @('status', '--porcelain=v1', '--untracked-files=all')
        if (-not [string]::IsNullOrEmpty($statusBefore)) { throw 'Git working tree is not completely clean.' }
        $stagedBefore = Invoke-Git -Arguments @('diff', '--cached', '--name-only')
        if (-not [string]::IsNullOrEmpty($stagedBefore)) { throw 'Git index contains staged files.' }
        $remoteBefore = Invoke-Git -Arguments @('remote')
        if (-not [string]::IsNullOrEmpty($remoteBefore)) { throw 'Git remote is present; this workspace expects none.' }
        $headBefore = Invoke-Git -Arguments @('rev-parse', 'HEAD')

        $repoHashBefore = Get-ContentManifestHash -Root $gitRoot
        $obsidianHashBefore = Get-ContentManifestHash -Root (Join-Path $workspacePath 'obsidian') -MarkdownOnly
        $ignoreHashBefore = Get-FileSha256 -Path $ignorePath
        $inventoryBefore = Get-WorkspaceInventoryHash

        [Environment]::SetEnvironmentVariable('CBM_WORKERS', '1', 'Process')
        [Environment]::SetEnvironmentVariable('CBM_MEM_BUDGET_MB', '3000', 'Process')

        $listBefore = Invoke-CbmCli -Tool 'list_projects' -TimeoutSeconds $ListTimeoutSeconds -StandardInput $null
        Assert-CbmProcessSucceeded -Result $listBefore -Label 'preflight list_projects' -Warnings $warnings
        $projectsBefore = ConvertTo-ProjectList -Parsed (ConvertFrom-CbmJson -Json $listBefore.Stdout -Label 'preflight list_projects')
        $null = Assert-ProjectSet -Projects $projectsBefore

        $script:CurrentStage = 'index'
        $payload = @{ repo_path = $workspaceRoot; name = $projectName } | ConvertTo-Json -Compress
        $script:IndexRuns++
        $indexResult = Invoke-CbmCli -Tool 'index_repository' -TimeoutSeconds $IndexTimeoutSeconds -StandardInput $payload
        Assert-CbmProcessSucceeded -Result $indexResult -Label 'index_repository' -Warnings $warnings
        $indexParsed = ConvertFrom-CbmJson -Json $indexResult.Stdout -Label 'index_repository'
        $indexStructured = Get-CbmStructuredContent -Parsed $indexParsed -Label 'index_repository'
        $statusProperty = $indexStructured.PSObject.Properties['status']
        if ($null -eq $statusProperty -or [string]$statusProperty.Value -ne 'indexed') {
            throw 'index_repository did not report status=indexed.'
        }
        $projectProperty = $indexStructured.PSObject.Properties['project']
        if ($null -eq $projectProperty -or [string]$projectProperty.Value -cne $projectName) {
            throw 'index_repository did not report the expected project.'
        }

        $script:CurrentStage = 'postflight'
        $listAfter = Invoke-CbmCli -Tool 'list_projects' -TimeoutSeconds $ListTimeoutSeconds -StandardInput $null
        Assert-CbmProcessSucceeded -Result $listAfter -Label 'postflight list_projects' -Warnings $warnings
        $projectsAfter = ConvertTo-ProjectList -Parsed (ConvertFrom-CbmJson -Json $listAfter.Stdout -Label 'postflight list_projects')
        $projectAfter = Assert-ProjectSet -Projects $projectsAfter

        $architecturePayload = @{ project = $projectName; aspects = @('file_tree') } | ConvertTo-Json -Compress
        $architectureResult = Invoke-CbmCli -Tool 'get_architecture' -TimeoutSeconds $ArchitectureTimeoutSeconds -StandardInput $architecturePayload
        Assert-CbmProcessSucceeded -Result $architectureResult -Label 'get_architecture' -Warnings $warnings
        $architectureParsed = ConvertFrom-CbmJson -Json $architectureResult.Stdout -Label 'get_architecture'
        $architectureStructured = Get-CbmStructuredContent -Parsed $architectureParsed -Label 'get_architecture'
        $fileNodes = Assert-ArchitectureSafe -Structured $architectureStructured
        $warnings.Add('Architecture file_tree reflects graph-indexed paths and may omit unsupported or empty files.')

        if ((Invoke-Git -Arguments @('rev-parse', 'HEAD')) -cne $headBefore) { throw 'HEAD changed during refresh.' }
        if (-not [string]::IsNullOrEmpty((Invoke-Git -Arguments @('status', '--porcelain=v1', '--untracked-files=all')))) { throw 'Git status changed during refresh.' }
        if (-not [string]::IsNullOrEmpty((Invoke-Git -Arguments @('diff', '--cached', '--name-only')))) { throw 'Staged files appeared during refresh.' }
        if ((Invoke-Git -Arguments @('remote')) -cne $remoteBefore) { throw 'Git remotes changed during refresh.' }
        if ((Get-ContentManifestHash -Root $gitRoot) -cne $repoHashBefore) { throw 'Repository files changed during refresh.' }
        if ((Get-ContentManifestHash -Root (Join-Path $workspacePath 'obsidian') -MarkdownOnly) -cne $obsidianHashBefore) { throw 'Obsidian Markdown files changed during refresh.' }
        if ((Get-FileSha256 -Path $ignorePath) -cne $ignoreHashBefore) { throw '.cbmignore changed during refresh.' }
        if ((Get-WorkspaceInventoryHash) -cne $inventoryBefore) { throw 'Workspace file inventory changed during refresh.' }

        return [pscustomobject]@{
            Nodes = [long]$projectAfter.nodes
            Edges = [long]$projectAfter.edges
            FileNodes = $fileNodes
            Warnings = @($warnings)
        }
    }
    finally {
        [Environment]::SetEnvironmentVariable('CBM_WORKERS', $(if ($hadWorkers) { $oldWorkers } else { $null }), 'Process')
        [Environment]::SetEnvironmentVariable('CBM_MEM_BUDGET_MB', $(if ($hadMemoryBudget) { $oldMemoryBudget } else { $null }), 'Process')
    }
}

try {
    $result = Invoke-CodebaseMemoryRefresh
    Write-Output 'Codebase Memory refresh: PASS'
    Write-Output "Project: $projectName"
    Write-Output "Workspace root: $workspaceRoot"
    Write-Output "Index runs: $script:IndexRuns"
    Write-Output "Nodes: $($result.Nodes)"
    Write-Output "Edges: $($result.Edges)"
    Write-Output "File nodes/files: $($result.FileNodes)"
    Write-Output "Warnings: $(if ($result.Warnings.Count -eq 0) { 'none' } else { $result.Warnings -join '; ' })"
    Write-Output 'Git unchanged: yes'
    Write-Output 'Workspace files unchanged: yes'
    exit 0
}
catch {
    Write-Output 'Codebase Memory refresh: FAIL'
    Write-Output "Stage: $script:CurrentStage"
    Write-Output "Reason: $($_.Exception.Message)"
    Write-Output "Index runs: $script:IndexRuns"
    exit 2
}
