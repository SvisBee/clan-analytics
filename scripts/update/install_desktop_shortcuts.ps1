param(
    [switch] $ConfirmShortcuts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ConfirmShortcuts) {
    throw 'Pass -ConfirmShortcuts to create the desktop shortcuts.'
}

$desktop = [Environment]::GetFolderPath('Desktop')
$powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$scriptPath = 'D:\coc\repo\scripts\update\update_clan_site.ps1'
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Updater script is missing: $scriptPath"
}

$shell = New-Object -ComObject WScript.Shell
$items = @(
    @{
        Name = 'Обновить сайт клана.lnk'
        Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\coc\repo\scripts\update\update_clan_site.ps1"'
        Description = 'Получить актуальные данные и опубликовать сайт клана.'
    },
    @{
        Name = 'Проверить сайт клана без публикации.lnk'
        Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\coc\repo\scripts\update\update_clan_site.ps1" -PreviewOnly'
        Description = 'Получить и проверить данные без изменения Git и сайта.'
    }
)

foreach ($item in $items) {
    $path = Join-Path $desktop $item.Name
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $powerShellPath
    $shortcut.Arguments = $item.Arguments
    $shortcut.WorkingDirectory = 'D:\coc\repo'
    $shortcut.Description = $item.Description
    $shortcut.Save()
    Write-Host "Created: $path"
}

Write-Host 'Desktop shortcuts: PASS'
