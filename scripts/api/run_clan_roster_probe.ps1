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

function Get-InnermostExceptionType {
    param(
        [Parameter(Mandatory = $true)]
        [System.Exception] $Exception
    )

    while ($null -ne $Exception.InnerException) {
        $Exception = $Exception.InnerException
    }
    return $Exception.GetType().Name
}

function Import-HostPowerShellSecurityModule {
    try {
        if ([string]::IsNullOrWhiteSpace($PSHOME) -or
            -not [System.IO.Path]::IsPathRooted($PSHOME)) {
            throw [System.InvalidOperationException]::new(
                'The current PowerShell home must be an absolute path.'
            )
        }

        $normalizedPsHome = [System.IO.Path]::GetFullPath($PSHOME).TrimEnd('\', '/')
        $expectedModuleRoot = [System.IO.Path]::GetFullPath((Join-Path `
            $normalizedPsHome `
            'Modules\Microsoft.PowerShell.Security'
        )).TrimEnd('\', '/')
        $moduleManifestPath = [System.IO.Path]::GetFullPath((Join-Path `
            $expectedModuleRoot `
            'Microsoft.PowerShell.Security.psd1'
        ))

        if (-not [System.IO.Path]::IsPathRooted($expectedModuleRoot) -or
            -not [System.IO.Path]::IsPathRooted($moduleManifestPath) -or
            -not $expectedModuleRoot.StartsWith(
                "$normalizedPsHome\",
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not $moduleManifestPath.StartsWith(
                "$expectedModuleRoot\",
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            throw [System.InvalidOperationException]::new(
                'The host-local security module path is invalid.'
            )
        }

        $manifestItem = Get-Item `
            -LiteralPath $moduleManifestPath `
            -Force `
            -ErrorAction Stop
        if ($manifestItem.PSIsContainer -or
            ($manifestItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw [System.InvalidOperationException]::new(
                'The host-local security module manifest must be a regular file.'
            )
        }

        $importedModules = @(Import-Module `
            -Name $moduleManifestPath `
            -PassThru `
            -ErrorAction Stop
        )
        if ($importedModules.Count -ne 1 -or
            [System.IO.Path]::GetFullPath($importedModules[0].Path) -ine
                $moduleManifestPath) {
            throw [System.InvalidOperationException]::new(
                'The imported security module does not match the host-local manifest.'
            )
        }
        $command = Get-Command `
            'Microsoft.PowerShell.Security\ConvertTo-SecureString' `
            -CommandType Cmdlet `
            -ErrorAction Stop
        $loadedModuleBase = [System.IO.Path]::GetFullPath(
            $command.Module.ModuleBase
        ).TrimEnd('\', '/')
        $loadedModulePath = [System.IO.Path]::GetFullPath($command.Module.Path)
        $loadedManifestRoot = [System.IO.Path]::GetDirectoryName(
            $loadedModulePath
        ).TrimEnd('\', '/')
        $moduleBaseIsHostLocal = $loadedModuleBase.Equals(
            $normalizedPsHome,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or $loadedModuleBase.StartsWith(
            "$normalizedPsHome\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
        if ($command.CommandType -ne [System.Management.Automation.CommandTypes]::Cmdlet -or
            $command.ModuleName -cne 'Microsoft.PowerShell.Security' -or
            $loadedModulePath -ine $moduleManifestPath -or
            $loadedManifestRoot -ine $expectedModuleRoot -or
            -not $moduleBaseIsHostLocal) {
            throw [System.InvalidOperationException]::new(
                'The loaded security module does not match the current PowerShell host.'
            )
        }

        return [pscustomobject]@{
            ModuleName = $command.ModuleName
            ModuleVersion = $command.Module.Version.ToString()
            PSEdition = $PSVersionTable.PSEdition
            PSVersion = $PSVersionTable.PSVersion.ToString()
        }
    }
    catch {
        $cause = Get-InnermostExceptionType -Exception $_.Exception
        throw [System.InvalidOperationException]::new(
            'Failed to load the host-local Microsoft.PowerShell.Security module. ' +
            "Stage: import_security_module. Cause: $cause."
        )
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
$null = Import-HostPowerShellSecurityModule

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
        $hasCryptographicException = $false
        $exception = $_.Exception
        while ($null -ne $exception) {
            if ($exception -is [System.Security.Cryptography.CryptographicException]) {
                $hasCryptographicException = $true
                break
            }
            $exception = $exception.InnerException
        }
        $message = 'Failed to decrypt the DPAPI secret. ' +
            "Stage: decrypt_dpapi_secret. Cause: $cause."
        if ($hasCryptographicException) {
            $message += ' The Windows user or computer context may have changed.'
        }
        throw [System.InvalidOperationException]::new($message)
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
