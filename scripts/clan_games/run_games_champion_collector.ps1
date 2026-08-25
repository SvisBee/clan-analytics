param(
    [Parameter(Mandatory = $true)]
    [string] $EventId,

    [Parameter(Mandatory = $true)]
    [string] $ScanId,

    [Parameter(Mandatory = $true)]
    [ValidateSet('baseline', 'periodic', 'final')]
    [string] $ScanKind,

    [ValidateRange(1, 8)]
    [int] $MaxWorkers = 4,

    [string] $SecretPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-PathInsideRestrictedRoot {
    param([string] $Candidate, [string] $Root)
    $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    return $Candidate.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $Candidate.StartsWith("$normalizedRoot\", [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-SafeSecretPath {
    param([string] $Path)
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw 'Secret path must be absolute.'
    }
    $normalized = [System.IO.Path]::GetFullPath($Path)
    foreach ($root in @('D:\coc', 'D:\work', 'D:\study')) {
        if (Test-PathInsideRestrictedRoot -Candidate $normalized -Root $root) {
            throw 'Secret path must be outside protected workspaces.'
        }
    }
    return $normalized
}

function Assert-NoReparsePath {
    param([string] $Path)
    $current = [System.IO.DirectoryInfo]::new($Path)
    while ($null -ne $current) {
        if ($current.Exists -and
            ($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'Secret parent path must not contain a reparse point.'
        }
        $current = $current.Parent
    }
}

function Get-InnermostExceptionType {
    param([System.Exception] $Exception)
    while ($null -ne $Exception.InnerException) {
        $Exception = $Exception.InnerException
    }
    return $Exception.GetType().Name
}

function Import-HostPowerShellSecurityModule {
    try {
        if ([string]::IsNullOrWhiteSpace($PSHOME) -or
            -not [System.IO.Path]::IsPathRooted($PSHOME)) {
            throw [System.InvalidOperationException]::new('PowerShell home is invalid.')
        }
        $normalizedPsHome = [System.IO.Path]::GetFullPath($PSHOME).TrimEnd('\', '/')
        $moduleRoot = [System.IO.Path]::GetFullPath((Join-Path `
            $normalizedPsHome 'Modules\Microsoft.PowerShell.Security')).TrimEnd('\', '/')
        $manifest = [System.IO.Path]::GetFullPath((Join-Path `
            $moduleRoot 'Microsoft.PowerShell.Security.psd1'))
        if (-not $manifest.StartsWith("$moduleRoot\", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw [System.InvalidOperationException]::new('Security module path is invalid.')
        }
        $item = Get-Item -LiteralPath $manifest -Force -ErrorAction Stop
        if ($item.PSIsContainer -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw [System.InvalidOperationException]::new('Security module manifest is invalid.')
        }
        $imported = @(Import-Module -Name $manifest -PassThru -ErrorAction Stop)
        $command = Get-Command `
            'Microsoft.PowerShell.Security\ConvertTo-SecureString' `
            -CommandType Cmdlet `
            -ErrorAction Stop
        if ($imported.Count -ne 1 -or $command.ModuleName -cne 'Microsoft.PowerShell.Security' -or
            [System.IO.Path]::GetFullPath($command.Module.Path) -ine $manifest) {
            throw [System.InvalidOperationException]::new('Security module identity mismatch.')
        }
    }
    catch {
        $cause = Get-InnermostExceptionType -Exception $_.Exception
        throw [System.InvalidOperationException]::new(
            "Failed to load host-local security module. Stage: import_security_module. Cause: $cause."
        )
    }
}

$pythonCommand = Get-Command python -ErrorAction Stop
$collectorPath = 'D:\coc\repo\scripts\clan_games\collect_games_champion.py'
if (-not [System.IO.File]::Exists($collectorPath)) {
    throw 'Clan Games collector entrypoint is missing.'
}
$collectorArguments = @(
    $collectorPath,
    '--event-id', $EventId,
    '--scan-id', $ScanId,
    '--scan-kind', $ScanKind,
    '--max-workers', $MaxWorkers.ToString(),
    '--json'
)

# Establish all local gates, including the existing-scan retry gate, before DPAPI access.
$preflightOutput = & $pythonCommand.Source @collectorArguments '--local-preflight'
$preflightExit = $LASTEXITCODE
if ($preflightExit -ne 0) {
    $preflightOutput | Write-Output
    exit $preflightExit
}
$preflight = $preflightOutput | ConvertFrom-Json
if ($preflight.result_code -eq 'already_recorded') {
    $preflightOutput | Write-Output
    exit 0
}
if ($preflight.result_code -ne 'local_preflight_ready') {
    throw 'Collector local preflight returned an invalid state.'
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'DPAPI token access is supported only on Windows.'
}
if ([string]::IsNullOrWhiteSpace($SecretPath)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is unavailable.'
    }
    $SecretPath = Join-Path `
        $env:LOCALAPPDATA `
        'ClashClanAnalytics\secrets\coc_api_token.dpapi'
}
$resolvedSecretPath = Assert-SafeSecretPath -Path $SecretPath
$secretItem = Get-Item -LiteralPath $resolvedSecretPath -Force -ErrorAction Stop
if ($secretItem.PSIsContainer -or
    ($secretItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw 'Secret target must be a regular file and not a reparse point.'
}
Assert-NoReparsePath -Path $secretItem.DirectoryName
Import-HostPowerShellSecurityModule

$encryptedToken = $null
$secureToken = $null
$plainToken = $null
$tokenBstr = [System.IntPtr]::Zero
$childExitCode = 1
try {
    $encryptedToken = [System.IO.File]::ReadAllText($resolvedSecretPath).Trim()
    try {
        $secureToken = Microsoft.PowerShell.Security\ConvertTo-SecureString `
            -String $encryptedToken `
            -ErrorAction Stop
    }
    catch {
        $cause = Get-InnermostExceptionType -Exception $_.Exception
        throw [System.InvalidOperationException]::new(
            "Failed to decrypt the DPAPI secret. Stage: decrypt_dpapi_secret. Cause: $cause."
        )
    }
    $encryptedToken = $null
    $tokenBstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $plainToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenBstr)
    $env:COC_API_TOKEN = $plainToken
    $plainToken = $null
    & $pythonCommand.Source @collectorArguments
    $childExitCode = $LASTEXITCODE
}
finally {
    if ($tokenBstr -ne [System.IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenBstr)
    }
    Remove-Item Env:COC_API_TOKEN -ErrorAction SilentlyContinue
    if ($null -ne $secureToken) {
        $secureToken.Dispose()
    }
    $plainToken = $null
    $encryptedToken = $null
    $secureToken = $null
    Remove-Variable plainToken, encryptedToken, secureToken -ErrorAction SilentlyContinue
}
exit $childExitCode
