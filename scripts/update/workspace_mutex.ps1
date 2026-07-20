Set-StrictMode -Version Latest

function Get-CanonicalWorkspaceRoot {
    param([Parameter(Mandatory = $true)][string] $WorkspaceRoot)

    $full = [System.IO.Path]::GetFullPath($WorkspaceRoot).Replace('/', '\')
    $root = [System.IO.Path]::GetPathRoot($full)
    if (-not $full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        $full = $full.TrimEnd('\')
    }
    return $full.ToUpperInvariant()
}

function Get-WorkspaceMutexName {
    param([Parameter(Mandatory = $true)][string] $WorkspaceRoot)

    $canonical = Get-CanonicalWorkspaceRoot -WorkspaceRoot $WorkspaceRoot
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hex = ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
    return "Local\ClashClanAnalyticsSiteUpdate-$($hex.Substring(0, 24))"
}
