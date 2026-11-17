param([switch]$Quiet)
$ErrorActionPreference = 'Stop'
$programRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\Sheath Template Cutting'))
if ($programRoot -ne $expectedRoot) { throw 'Uninstaller is outside the expected application folder. No files were removed.' }
$marker = Join-Path $programRoot 'installation.json'
if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).ApplicationId -ne 'SheathTemplateCutting') { throw 'Installation identity is invalid. No files were removed.' }
if (Get-Process -Name 'SheathTemplateCutting' -ErrorAction SilentlyContinue) { throw 'Close Sheath Template Cutting before uninstalling.' }
if (-not $Quiet) {
    Add-Type -AssemblyName System.Windows.Forms
    if ([Windows.Forms.MessageBox]::Show('Remove the application? Calibration, templates and production data will be kept.','Sheath Template Cutting','YesNo') -ne 'Yes') { exit 0 }
}
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Sheath Template Cutting.lnk'
if (Test-Path -LiteralPath $desktopLink) { Remove-Item -LiteralPath $desktopLink -Force }
$menuRoot = Join-Path ([Environment]::GetFolderPath('Programs')) 'Sheath Template Cutting'
if (Test-Path -LiteralPath $menuRoot) { Remove-Item -LiteralPath $menuRoot -Recurse -Force }
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SheathTemplateCutting' -Recurse -Force -ErrorAction SilentlyContinue
# All targets are verified absolute program/shortcut locations. User data is elsewhere.
Remove-Item -LiteralPath $programRoot -Recurse -Force
