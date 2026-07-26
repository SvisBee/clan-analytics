[CmdletBinding()]
param(
    [string] $IgnorePath = 'D:\coc\.cbmignore'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $IgnorePath -PathType Leaf)) { throw "Ignore file is missing: $IgnorePath" }
$patterns = @(Get-Content -LiteralPath $IgnorePath | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
$requiredExcluded = @('/obsidian/**', '/data/**', '/runs/**', '/local/**', '/repo/site/data/**')
$fictionalExcluded = @('obsidian/example-note.md', 'data/example.json', 'runs/example/result.json', 'local/health/example.json', 'repo/site/data/example.json')
$fictionalIncluded = @('repo/scripts/example.ps1', 'repo/tests/test_example.py', 'repo/docs/example.md')

foreach ($pattern in $requiredExcluded) {
    if ($patterns -notcontains $pattern) { throw "Required exclusion is missing: $pattern" }
}

foreach ($path in $fictionalExcluded) {
    $root = '/' + (($path -split '/')[0]) + '/**'
    $matched = $patterns -contains $root -or ($path -like 'repo/site/data/*' -and $patterns -contains '/repo/site/data/**')
    if (-not $matched) { throw "Fictional excluded path is not covered: $path" }
}

foreach ($path in $fictionalIncluded) {
    if ($patterns | Where-Object { $_ -eq '/repo/**' -or $_ -eq '/repo/scripts/**' -or $_ -eq '/repo/tests/**' -or $_ -eq '/repo/docs/**' }) {
        throw "Required repository path is excluded: $path"
    }
}

Write-Output 'PASS: required exclusions present; fictional private paths covered; repo scripts/tests/docs remain included.'
