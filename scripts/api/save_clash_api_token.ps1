param(
    [string] $SecretPath,
    [switch] $Overwrite
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

function Set-PrivateAccessAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier] $UserSid,
        [Parameter(Mandatory = $true)]
        [System.Security.AccessControl.InheritanceFlags] $InheritanceFlags,
        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    $systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    try {
        $acl = Get-Acl -LiteralPath $Path
        $ownerBefore = $acl.Owner
        $groupBefore = $acl.Group
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) {
            $acl.RemoveAccessRuleSpecific($rule)
        }
        foreach ($sid in @($UserSid, $systemSid)) {
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $sid, 'FullControl', $InheritanceFlags, $none, $allow
            ))
        }
        Set-Acl -LiteralPath $Path -AclObject $acl

        $verifiedAcl = Get-Acl -LiteralPath $Path
        $rules = @($verifiedAcl.Access)
        if (-not $verifiedAcl.AreAccessRulesProtected -or
            $verifiedAcl.Owner -ne $ownerBefore -or
            $verifiedAcl.Group -ne $groupBefore -or
            $rules.Count -ne 2) {
            throw 'Private access rule verification failed.'
        }

        $expectedSids = @($UserSid.Value, $systemSid.Value) | Sort-Object
        $actualSids = @(
            foreach ($rule in $rules) {
                if ($rule.IsInherited -or
                    $rule.AccessControlType -ne $allow -or
                    ($rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -ne
                        [System.Security.AccessControl.FileSystemRights]::FullControl -or
                    $rule.InheritanceFlags -ne $InheritanceFlags -or
                    $rule.PropagationFlags -ne $none) {
                    throw 'Private access rule verification failed.'
                }
                $rule.IdentityReference.Translate(
                    [System.Security.Principal.SecurityIdentifier]
                ).Value
            }
        ) | Sort-Object
        if (($actualSids -join ',') -ne ($expectedSids -join ',')) {
            throw 'Private access identity verification failed.'
        }
    }
    catch {
        throw [System.InvalidOperationException]::new($FailureMessage, $_.Exception)
    }
}

function Set-PrivateDirectoryAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier] $UserSid
    )

    $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    Set-PrivateAccessAcl `
        -Path $Path `
        -UserSid $UserSid `
        -InheritanceFlags $inheritance `
        -FailureMessage 'Failed to restrict DPAPI secret directory access.'
}

function Set-PrivateFileAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier] $UserSid
    )

    Set-PrivateAccessAcl `
        -Path $Path `
        -UserSid $UserSid `
        -InheritanceFlags ([System.Security.AccessControl.InheritanceFlags]::None) `
        -FailureMessage 'Failed to restrict DPAPI secret file access.'
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'DPAPI token storage is supported only on Windows.'
}
if ([string]::IsNullOrWhiteSpace($SecretPath)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is unavailable.'
    }
    $SecretPath = Join-Path $env:LOCALAPPDATA 'ClashClanAnalytics\secrets\coc_api_token.dpapi'
}

$targetPath = Assert-SafeSecretPath -Path $SecretPath
$parentPath = [System.IO.Path]::GetDirectoryName($targetPath)
if ([string]::IsNullOrWhiteSpace($parentPath)) {
    throw 'Secret parent directory is invalid.'
}
Assert-NoReparsePath -Path $parentPath

$existingItem = Get-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue
if ($null -ne $existingItem) {
    if ($existingItem.PSIsContainer -or
        ($existingItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'Secret target must be a regular file and not a reparse point.'
    }
    if (-not $Overwrite) {
        throw 'Secret file already exists; use -Overwrite to replace it.'
    }
}

[System.IO.Directory]::CreateDirectory($parentPath) | Out-Null
Assert-NoReparsePath -Path $parentPath
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $identity.User
Set-PrivateDirectoryAcl -Path $parentPath -UserSid $userSid

$temporaryPath = Join-Path $parentPath ('.coc-api-token-{0}.tmp' -f [System.Guid]::NewGuid().ToString('N'))
$secureToken = $null
$encryptedToken = $null
try {
    $secureToken = Read-Host 'Paste Clash API token' -AsSecureString
    if ($null -eq $secureToken -or $secureToken.Length -eq 0) {
        throw 'Clash API token must not be empty.'
    }
    $encryptedToken = ConvertFrom-SecureString -SecureString $secureToken
    [System.IO.File]::WriteAllText(
        $temporaryPath,
        $encryptedToken,
        [System.Text.UTF8Encoding]::new($false)
    )
    Set-PrivateFileAcl -Path $temporaryPath -UserSid $userSid

    if ($null -ne $existingItem) {
        [System.IO.File]::Replace($temporaryPath, $targetPath, $null)
    }
    else {
        [System.IO.File]::Move($temporaryPath, $targetPath)
    }
    Set-PrivateFileAcl -Path $targetPath -UserSid $userSid
    Write-Output "DPAPI secret saved: $targetPath"
    Write-Output 'The secret is bound to the current Windows user identity.'
}
finally {
    if ([System.IO.File]::Exists($temporaryPath)) {
        [System.IO.File]::Delete($temporaryPath)
    }
    if ($null -ne $secureToken) {
        $secureToken.Dispose()
    }
    $encryptedToken = $null
    $secureToken = $null
    Remove-Variable encryptedToken, secureToken -ErrorAction SilentlyContinue
}
