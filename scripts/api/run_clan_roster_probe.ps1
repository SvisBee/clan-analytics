param(
    [Parameter(Mandatory = $true)]
    [string] $ClanTag,

    [string] $SecretPath,

    [string] $OutputDir,

    [ValidateRange(1, 60)]
    [int] $TimeoutSeconds = 15,

    [switch] $Overwrite,

    [switch] $DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-PathInsideRestrictedRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Candidate,
        [Parameter(Mandatory = $true)]
        [string] $Root
    )

    $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    return $Candidate.Equals(
        $normalizedRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or $Candidate.StartsWith(
        "$normalizedRoot\",
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-SafeSecretPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

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
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $current = [System.IO.DirectoryInfo]::new($Path)
    while ($null -ne $current) {
        if ($current.Exists -and ($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'Secret parent path must not contain a reparse point.'
        }
        $current = $current.Parent
    }
}

$pythonCommand = Get-Command python -ErrorAction Stop
$probePath = 'D:\coc\repo\scripts\api\probe_clan_roster.py'
if (-not [System.IO.File]::Exists($probePath)) {
    throw 'Clan roster probe entrypoint is missing.'
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $runTimestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutputDir = "D:\coc\runs\api_probe\clan_roster\$runTimestamp"
}

$probeArguments = @(
    $probePath,
    '--clan-tag', $ClanTag,
    '--token-env', 'COC_API_TOKEN',
    '--output-dir', $OutputDir,
    '--timeout-seconds', $TimeoutSeconds.ToString(),
    '--base-url', 'https://api.clashofclans.com/v1',
    '--endpoint-template', '/clans/{clan_tag}'
)

if ($DryRun) {
    $probeArguments += '--dry-run'
    & $pythonCommand.Source @probeArguments
    exit $LASTEXITCODE
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'DPAPI token access is supported only on Windows.'
}
if ([string]::IsNullOrWhiteSpace($SecretPath)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is unavailable.'
    }
    $SecretPath = Join-Path $env:LOCALAPPDATA 'ClashClanAnalytics\secrets\coc_api_token.dpapi'
}

$resolvedSecretPath = Assert-SafeSecretPath -Path $SecretPath
$secretItem = Get-Item -LiteralPath $resolvedSecretPath -Force -ErrorAction Stop
if ($secretItem.PSIsContainer -or
    ($secretItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw 'Secret target must be a regular file and not a reparse point.'
}
Assert-NoReparsePath -Path $secretItem.DirectoryName

$encryptedToken = $null
$secureToken = $null
$plainToken = $null
$tokenBstr = [System.IntPtr]::Zero
$childExitCode = 1
try {
    $encryptedToken = [System.IO.File]::ReadAllText($resolvedSecretPath).Trim()
    try {
        $secureToken = ConvertTo-SecureString -String $encryptedToken
    }
    catch {
        throw 'DPAPI secret cannot be decrypted by the current Windows user identity; the user or computer may have changed.'
    }
    $encryptedToken = $null
    $tokenBstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $plainToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenBstr)
    $env:COC_API_TOKEN = $plainToken
    $plainToken = $null

    $probeArguments += '--confirm-api-contract'
    if ($Overwrite) {
        $probeArguments += '--overwrite'
    }
    & $pythonCommand.Source @probeArguments
    $childExitCode = $LASTEXITCODE
}
finally {
    if ($tokenBstr -ne [System.IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenBstr)
        $tokenBstr = [System.IntPtr]::Zero
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
