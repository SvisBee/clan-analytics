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

function Test-PrivateAccessAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier] $UserSid,
        [Parameter(Mandatory = $true)]
        [System.Security.AccessControl.InheritanceFlags] $InheritanceFlags
    )

    try {
        $acl = Get-Acl -LiteralPath $Path
        $rules = @($acl.Access)
        if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 2) {
            return $false
        }

        $allow = [System.Security.AccessControl.AccessControlType]::Allow
        $fullControl = [System.Security.AccessControl.FileSystemRights]::FullControl
        $none = [System.Security.AccessControl.PropagationFlags]::None
        $systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
        $expectedSids = @($UserSid.Value, $systemSid.Value) | Sort-Object
        $actualSids = @(
            foreach ($rule in $rules) {
                if ($rule.IsInherited -or
                    $rule.AccessControlType -ne $allow -or
                    $rule.FileSystemRights -ne $fullControl -or
                    $rule.InheritanceFlags -ne $InheritanceFlags -or
                    $rule.PropagationFlags -ne $none) {
                    return $false
                }
                $rule.IdentityReference.Translate(
                    [System.Security.Principal.SecurityIdentifier]
                ).Value
            }
        ) | Sort-Object
        return ($actualSids -join ',') -eq ($expectedSids -join ',')
    }
    catch {
        return $false
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

    $stage = 'precheck_private_dacl'
    try {
        if (Test-PrivateAccessAcl `
            -Path $Path `
            -UserSid $UserSid `
            -InheritanceFlags $InheritanceFlags) {
            return
        }

        $stage = 'read_existing_acl'
        $acl = Get-Acl -LiteralPath $Path
        $stage = 'capture_identity_metadata'
        $ownerBefore = $acl.Owner
        $groupBefore = $acl.Group
        $stage = 'protect_dacl'
        $acl.SetAccessRuleProtection($true, $false)
        $stage = 'remove_access_rules'
        foreach ($rule in @($acl.Access)) {
            $null = $acl.RemoveAccessRuleSpecific($rule)
        }
        $stage = 'add_private_rules'
        $systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
        $none = [System.Security.AccessControl.PropagationFlags]::None
        $allow = [System.Security.AccessControl.AccessControlType]::Allow
        foreach ($sid in @($UserSid, $systemSid)) {
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $sid, 'FullControl', $InheritanceFlags, $none, $allow
            ))
        }
        $stage = 'apply_dacl'
        Set-Acl -LiteralPath $Path -AclObject $acl

        $stage = 'read_applied_acl'
        $verifiedAcl = Get-Acl -LiteralPath $Path
        $stage = 'verify_owner_group'
        if ($verifiedAcl.Owner -ne $ownerBefore -or $verifiedAcl.Group -ne $groupBefore) {
            throw [System.InvalidOperationException]::new(
                'Owner or primary group changed while applying the private DACL.'
            )
        }
        $stage = 'verify_private_dacl'
        if (-not (Test-PrivateAccessAcl `
            -Path $Path `
            -UserSid $UserSid `
            -InheritanceFlags $InheritanceFlags)) {
            throw [System.InvalidOperationException]::new(
                'Private DACL verification failed.'
            )
        }
    }
    catch {
        $cause = Get-InnermostExceptionType -Exception $_.Exception
        throw [System.InvalidOperationException]::new(
            "$FailureMessage Stage: $stage. Cause: $cause."
        )
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

function Publish-PrivateSecretFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $TemporaryPath,
        [Parameter(Mandatory = $true)]
        [string] $TargetPath,
        [Parameter(Mandatory = $true)]
        [string] $ParentPath,
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier] $UserSid,
        [switch] $OverwriteExisting
    )

    $stage = 'verify_target_file'
    $backupPath = $null
    $replaceCompleted = $false
    $saveCompleted = $false
    try {
        if ($OverwriteExisting) {
            $targetItem = Get-Item -LiteralPath $TargetPath -Force -ErrorAction Stop
            if ($targetItem.PSIsContainer -or
                ($targetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                throw 'Secret target must be a regular file and not a reparse point.'
            }
            if (-not (Test-PrivateAccessAcl `
                -Path $TargetPath `
                -UserSid $UserSid `
                -InheritanceFlags ([System.Security.AccessControl.InheritanceFlags]::None))) {
                throw 'Existing DPAPI secret target must already have a private DACL.'
            }

            $stage = 'prepare_backup_path'
            $backupName = '.coc-api-token-backup-{0}.tmp' -f (
                [System.Guid]::NewGuid().ToString('N')
            )
            $backupPath = [System.IO.Path]::GetFullPath((Join-Path $ParentPath $backupName))
            $expectedParent = [System.IO.Path]::GetFullPath($ParentPath).TrimEnd('\', '/')
            $backupParent = [System.IO.Path]::GetDirectoryName($backupPath).TrimEnd('\', '/')
            $targetParent = [System.IO.Path]::GetDirectoryName(
                [System.IO.Path]::GetFullPath($TargetPath)
            ).TrimEnd('\', '/')
            $temporaryParent = [System.IO.Path]::GetDirectoryName(
                [System.IO.Path]::GetFullPath($TemporaryPath)
            ).TrimEnd('\', '/')
            if (-not [System.IO.Path]::IsPathRooted($backupPath) -or
                -not $backupParent.Equals(
                    $expectedParent,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -or
                -not $targetParent.Equals(
                    $expectedParent,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -or
                -not $temporaryParent.Equals(
                    $expectedParent,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -or
                (Test-Path -LiteralPath $backupPath)) {
                throw 'Recovery backup path validation failed.'
            }

            $stage = 'replace_target'
            [System.IO.File]::Replace(
                [string] $TemporaryPath,
                [string] $TargetPath,
                [string] $backupPath
            )
            $replaceCompleted = $true

            $stage = 'verify_replace_paths'
            if ([System.IO.File]::Exists($TemporaryPath)) {
                throw 'Temporary secret file remained after replacement.'
            }
            $targetItem = Get-Item -LiteralPath $TargetPath -Force -ErrorAction Stop
            $backupItem = Get-Item -LiteralPath $backupPath -Force -ErrorAction Stop
            foreach ($item in @($targetItem, $backupItem)) {
                if ($item.PSIsContainer -or
                    ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                    throw 'Published secret files must be regular files without reparse points.'
                }
            }

            $stage = 'verify_target_acl'
            Set-PrivateFileAcl -Path $TargetPath -UserSid $UserSid
            $stage = 'verify_backup_acl'
            Set-PrivateFileAcl -Path $backupPath -UserSid $UserSid
            $stage = 'verify_target_file'
            $targetItem = Get-Item -LiteralPath $TargetPath -Force -ErrorAction Stop
            if ($targetItem.Length -le 0) {
                throw 'Published DPAPI secret file must not be empty.'
            }

            $stage = 'delete_backup'
            [System.IO.File]::Delete($backupPath)
            $stage = 'verify_backup_cleanup'
            if ([System.IO.File]::Exists($backupPath)) {
                throw 'Recovery backup cleanup failed.'
            }
            $saveCompleted = $true
            return
        }

        $stage = 'publish_initial_target'
        [System.IO.File]::Move($TemporaryPath, $TargetPath)
        $stage = 'verify_replace_paths'
        if ([System.IO.File]::Exists($TemporaryPath)) {
            throw 'Temporary secret file remained after publication.'
        }
        $stage = 'verify_target_acl'
        Set-PrivateFileAcl -Path $TargetPath -UserSid $UserSid
        $stage = 'verify_target_file'
        $targetItem = Get-Item -LiteralPath $TargetPath -Force -ErrorAction Stop
        if ($targetItem.PSIsContainer -or
            ($targetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
            $targetItem.Length -le 0) {
            throw 'Published DPAPI secret target validation failed.'
        }
        $saveCompleted = $true
    }
    catch {
        $cause = Get-InnermostExceptionType -Exception $_.Exception
        $message = "Failed to publish DPAPI secret. Stage: $stage. Cause: $cause."
        $recoveryBackupExists = -not [string]::IsNullOrWhiteSpace($backupPath) -and
            [System.IO.File]::Exists($backupPath)
        if (-not $saveCompleted -and
            ($replaceCompleted -or $recoveryBackupExists) -and
            $recoveryBackupExists) {
            $message += ' DPAPI secret replacement did not complete validation.'
            $message += ' A protected recovery backup was retained in the secret directory.'
        }
        throw [System.InvalidOperationException]::new($message)
    }
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
$recoveryBackups = @(
    Get-ChildItem `
        -LiteralPath $parentPath `
        -Filter '.coc-api-token-backup-*.tmp' `
        -Force `
        -ErrorAction Stop
)
if ($recoveryBackups.Count -gt 0) {
    throw 'A protected DPAPI recovery backup already exists. Resolve it before saving another token.'
}
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

    Publish-PrivateSecretFile `
        -TemporaryPath $temporaryPath `
        -TargetPath $targetPath `
        -ParentPath $parentPath `
        -UserSid $userSid `
        -OverwriteExisting:($null -ne $existingItem)
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
