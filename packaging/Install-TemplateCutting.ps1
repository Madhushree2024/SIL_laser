param([switch]$VerifyOnly, [string]$TestRoot)
$ErrorActionPreference = 'Stop'
try {
 $taskManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'package-sha256.json') -Raw | ConvertFrom-Json
 foreach ($taskEntry in $taskManifest.PSObject.Properties) {
  $taskFile = Join-Path $PSScriptRoot $taskEntry.Name
  if (-not (Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw ('Missing package file: ' + $taskEntry.Name) }
  if ((Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash -ne $taskEntry.Value) { throw ('Package checksum failed: ' + $taskEntry.Name) }
 }
 if ($VerifyOnly) { Write-Output ('Package verified: ' + @($taskManifest.PSObject.Properties).Count + ' files. Nothing installed or started.'); exit 0 }
 if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows 10 or Windows 11 is required.' }
 if (-not $TestRoot) {
  $taskNet = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue
  if (-not $taskNet -or $taskNet.Release -lt 528040) {
   Write-Output 'Install the included Microsoft .NET Framework 4.8. Reboot if requested, then run Install.cmd again.'
   Start-Process -FilePath (Join-Path $PSScriptRoot 'Prerequisites\ndp48-x86-x64-allos-enu.exe') -Wait
   throw 'Run Install.cmd again after the .NET installation finishes.'
  }
  $taskWebView = $false
  foreach ($taskKey in @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')) {
   $taskVersion = (Get-ItemProperty -LiteralPath $taskKey -ErrorAction SilentlyContinue).pv
   if ($taskVersion -and $taskVersion -ne '0.0.0.0') { $taskWebView = $true }
  }
  if (-not $taskWebView) {
   Write-Output 'Install the included Microsoft WebView2 Runtime, then run Install.cmd again.'
   Start-Process -FilePath (Join-Path $PSScriptRoot 'Prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe') -ArgumentList '/install' -Wait
   throw 'Run Install.cmd again after WebView2 installation finishes.'
  }
 }
 if (-not $TestRoot -and @(Get-Process TemplateCutting -ErrorAction SilentlyContinue).Count) {
  throw 'Close every Template Cutting desktop window when the machine is idle, then run Install.cmd again. No app was force-closed.'
 }
 if (-not $TestRoot -and @(Get-Process TemplateCutting -ErrorAction SilentlyContinue).Count) {
  throw 'Close every Template Cutting desktop window when the machine is idle, then run Install.cmd again. No app was force-closed.'
 }
 $taskPayload = Join-Path $PSScriptRoot 'TemplateCutting'
 $taskVersionName = '2026-11-13-embedded-download-upgrade'
 if ($TestRoot) {
  $taskBase = [IO.Path]::GetFullPath($TestRoot)
  $taskDestination = Join-Path $taskBase ('Programs\Template Cutting Automation\' + $taskVersionName)
  $taskWorkspace = Join-Path $taskBase 'Workspace'
 } else {
  $taskDestination = Join-Path $env:LOCALAPPDATA ('Programs\Template Cutting Automation\' + $taskVersionName)
  $taskWorkspace = Join-Path $env:LOCALAPPDATA 'SheathTemplateCuttingAutomation\Workspace'
 }
 $taskMarker = Join-Path $taskDestination 'installation.json'
 if ((Test-Path -LiteralPath $taskDestination) -and @(Get-ChildItem -LiteralPath $taskDestination -Force).Count) {
  if (-not (Test-Path -LiteralPath $taskMarker)) { throw 'Destination contains files from an unrelated installation; nothing overwritten.' }
  if ((Get-Content -LiteralPath $taskMarker -Raw | ConvertFrom-Json).ApplicationId -ne 'TemplateCuttingAutomation') { throw 'Destination belongs to another application.' }
 }
 $taskExe = Join-Path $taskDestination 'TemplateCutting.exe'
 if (@(Get-Process TemplateCutting -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $taskExe }).Count) { throw 'Close this installed version before running setup again.' }
 New-Item -ItemType Directory -Path $taskDestination -Force | Out-Null
 foreach ($taskFile in Get-ChildItem -LiteralPath $taskPayload -Recurse -File) {
  $taskRelative = $taskFile.FullName.Substring($taskPayload.Length + 1)
  $taskTarget = Join-Path $taskDestination $taskRelative
  [IO.Directory]::CreateDirectory((Split-Path -Parent $taskTarget)) | Out-Null
  Copy-Item -LiteralPath $taskFile.FullName -Destination $taskTarget -Force
 }
 Get-ChildItem -LiteralPath $taskDestination -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
 @{ApplicationId='TemplateCuttingAutomation';Package=$taskVersionName;InstalledAt=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -LiteralPath $taskMarker -Encoding UTF8
 # Only initialize missing settings. Never replace an existing workspace or calibration.
 $taskConfig = Join-Path $taskWorkspace 'app\config\config.yaml'
 if (-not (Test-Path -LiteralPath $taskConfig)) {
  $taskSeed = Join-Path $taskDestination '_internal\seed'
  foreach ($taskFile in Get-ChildItem -LiteralPath $taskSeed -Recurse -File) {
   $taskRelative = $taskFile.FullName.Substring($taskSeed.Length + 1)
   if ($taskRelative -eq 'seed-manifest.json') { continue }
   $taskTarget = Join-Path $taskWorkspace $taskRelative
   if (-not (Test-Path -LiteralPath $taskTarget)) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $taskTarget)) | Out-Null
    Copy-Item -LiteralPath $taskFile.FullName -Destination $taskTarget
   }
  }
  $taskLaserPath = (Join-Path $taskDestination 'LaserCAD\AWCPrograms\LaserCAD.exe').Replace("'", "''")
  $taskText = [IO.File]::ReadAllText($taskConfig).Replace("`r`n", "`n")
  if ([regex]::Matches($taskText, "(?m)^  executable: ''\s*$").Count -ne 1) { throw 'Cannot initialize the LaserCAD executable setting.' }
  $taskText = [regex]::Replace($taskText, "(?m)^  executable: ''[\t ]*$", ("  executable: '" + $taskLaserPath + "'"))
  [IO.File]::WriteAllText($taskConfig, $taskText, (New-Object Text.UTF8Encoding($false)))
 }
 if (-not $TestRoot) {
  . (Join-Path $PSScriptRoot 'TemplateCutting-Shortcuts.ps1')
  Set-TemplateCuttingShortcuts -DesktopDirectory ([Environment]::GetFolderPath('Desktop')) -MenuDirectory (Join-Path ([Environment]::GetFolderPath('Programs')) 'Template Cutting Automation') -Executable $taskExe -Destination $taskDestination
 }
 Write-Output ('Installed successfully: ' + $taskDestination)
 Write-Output 'Open the desktop shortcut. Create an account, select the actual USB port and calibrate the new camera/marker positions.'
 Write-Output 'On first launch this update backs up settings, enables embedded LaserCAD and USB Download, and disables split screen. Calibration is preserved; Start stays manual.'
} catch { Write-Output ('Installation stopped: ' + $_.Exception.Message); exit 1 }
