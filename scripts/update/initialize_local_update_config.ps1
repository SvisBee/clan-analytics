param(
    [Parameter(Mandatory = $true)]
    [string] $ClanTag,

    [switch] $ConfirmConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ConfirmConfig) {
    throw 'Pass -ConfirmConfig to create or replace the local updater config.'
}

$normalized = $ClanTag.Trim().ToUpperInvariant()
if ($normalized -notmatch '^#[A-Z0-9]{3,20}$') {
    throw 'ClanTag must use the official #TAG format.'
}

$configPath = 'D:\coc\data\config\clan_site_update.json'
$configDir = Split-Path -Parent $configPath
New-Item -ItemType Directory -Path $configDir -Force | Out-Null

$payload = [ordered]@{
    clan_tag = $normalized
    schema_version = 1
}
$json = $payload | ConvertTo-Json -Depth 3
$temporary = Join-Path $configDir ('.clan-site-update-' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    [IO.File]::WriteAllText(
        $temporary,
        $json.TrimEnd() + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $configPath -Force
}
finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}

Write-Host 'Local updater config: PASS'
Write-Host "Path: $configPath"
Write-Host 'Clan tag: [REDACTED]'
Write-Host 'Git changed: no'
