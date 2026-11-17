param([switch]$Quiet, [string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$applicationName = 'Sheath Template Cutting'
$defaultRoot = Join-Path $env:LOCALAPPDATA 'Programs\Sheath Template Cutting'
$targetRoot = if ($InstallRoot) { [IO.Path]::GetFullPath($InstallRoot) } else { $defaultRoot }
$stageRoot = Join-Path $env:TEMP ('SheathCuttingSetup-' + [guid]::NewGuid().ToString('N'))
try {
    if ((Split-Path -Leaf $targetRoot) -ne $applicationName) { throw 'The destination folder must be named Sheath Template Cutting.' }
    if (Test-Path -LiteralPath $targetRoot) {
        $marker = Join-Path $targetRoot 'installation.json'
        if ((Get-ChildItem -LiteralPath $targetRoot -Force | Measure-Object).Count -gt 0) {
            if (-not (Test-Path -LiteralPath $marker)) { throw 'Destination contains unrelated files. Choose a dedicated application folder.' }
            if ((Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).ApplicationId -ne 'SheathTemplateCutting') { throw 'Destination is not a Sheath Cutting installation.' }
        }
    }
    $running = @(Get-Process -Name 'SheathTemplateCutting' -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq (Join-Path $targetRoot 'SheathTemplateCutting.exe')
    })
    # Ask the application to close normally; its processing guard can refuse.
    foreach ($process in $running) { $null = $process.CloseMainWindow() }
    foreach ($process in $running) {
        if (-not $process.WaitForExit(30000)) {
            throw 'The application is still processing or waiting for a dialog. Finish that operation and run setup again. No files have been replaced.'
        }
    }
    Expand-Archive -LiteralPath (Join-Path $PSScriptRoot 'application.zip') -DestinationPath $stageRoot
    if (-not (Test-Path -LiteralPath (Join-Path $stageRoot 'SheathTemplateCutting.exe'))) { throw 'Installer payload is incomplete.' }
    New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
    # External applications may still hold an unchanged shared runtime DLL.
    # Keep identical bytes in place; replace changed files only.
    foreach ($payloadFile in (Get-ChildItem -LiteralPath $stageRoot -Recurse -File -Force)) {
        $relativeFile = $payloadFile.FullName.Substring($stageRoot.Length + 1)
        $destinationFile = Join-Path $targetRoot $relativeFile
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destinationFile)) | Out-Null
        if (Test-Path -LiteralPath $destinationFile -PathType Leaf) {
            $existingHash = (Get-FileHash -LiteralPath $destinationFile -Algorithm SHA256).Hash
            $payloadHash = (Get-FileHash -LiteralPath $payloadFile.FullName -Algorithm SHA256).Hash
            if ($existingHash -eq $payloadHash) { continue }
        }
        try { Copy-Item -LiteralPath $payloadFile.FullName -Destination $destinationFile -Force }
        catch { throw "Close applications using '$relativeFile' and run setup again. $($_.Exception.Message)" }
    }
    @{ApplicationId='SheathTemplateCutting';Version='1.4.10';InstalledAt=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $targetRoot 'installation.json') -Encoding UTF8
    $shell = New-Object -ComObject WScript.Shell
    $desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Sheath Template Cutting.lnk'
    $startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Sheath Template Cutting'
    New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
    foreach ($linkPath in @($desktopShortcut, (Join-Path $startMenu 'Sheath Template Cutting.lnk'))) {
        $shortcut = $shell.CreateShortcut($linkPath)
        $shortcut.TargetPath = Join-Path $targetRoot 'SheathTemplateCutting.exe'
        $shortcut.WorkingDirectory = $targetRoot
        $shortcut.IconLocation = (Join-Path $targetRoot '_internal\frontend\assets\app-icon-v142.ico') + ',0'
        $shortcut.Description = 'Sheath camera capture, nesting and export'
        $shortcut.Save()
    }
    $registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SheathTemplateCutting'
    New-Item -Path $registry -Force | Out-Null
    New-ItemProperty -Path $registry -Name DisplayName -Value $applicationName -Force | Out-Null
    New-ItemProperty -Path $registry -Name DisplayVersion -Value '1.4.10' -Force | Out-Null
    New-ItemProperty -Path $registry -Name InstallLocation -Value $targetRoot -Force | Out-Null
    New-ItemProperty -Path $registry -Name DisplayIcon -Value (Join-Path $targetRoot '_internal\frontend\assets\app-icon-v142.ico') -Force | Out-Null
    New-ItemProperty -Path $registry -Name UninstallString -Value ('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $targetRoot 'Uninstall.ps1') + '"') -Force | Out-Null
    New-ItemProperty -Path $registry -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $registry -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
    if (-not $Quiet) {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show("Installation complete. Open Sheath Template Cutting from your desktop shortcut.`n`nExisting calibration, templates and production records are preserved.",$applicationName) | Out-Null
    }
} catch {
    if ($Quiet) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($_.Exception.Message,$applicationName) | Out-Null
    exit 1
} finally {
    # Delete only the exact temporary extraction directory created by this run.
    $resolvedStage = [IO.Path]::GetFullPath($stageRoot)
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if ($resolvedStage.StartsWith($resolvedTemp,[StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedStage) -match '^SheathCuttingSetup-[a-f0-9]{32}$') {
        if (Test-Path -LiteralPath $resolvedStage) { Remove-Item -LiteralPath $resolvedStage -Recurse -Force }
    }
}
