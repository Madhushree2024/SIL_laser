function Set-TemplateCuttingShortcuts {
 param(
  [Parameter(Mandatory=$true)][string]$DesktopDirectory,
  [Parameter(Mandatory=$true)][string]$MenuDirectory,
  [Parameter(Mandatory=$true)][string]$Executable,
  [Parameter(Mandatory=$true)][string]$Destination
 )
 $taskShell = New-Object -ComObject WScript.Shell
 $taskDesktopLink = Join-Path $DesktopDirectory 'Template Cutting Automation.lnk'
 $taskDuplicateLink = Join-Path $DesktopDirectory 'TemplateCutting.lnk'
 $taskPreviousTarget = ''
 if (Test-Path -LiteralPath $taskDesktopLink -PathType Leaf) {
  $taskPreviousTarget = $taskShell.CreateShortcut($taskDesktopLink).TargetPath
 }
 New-Item -ItemType Directory -Path $DesktopDirectory -Force | Out-Null
 New-Item -ItemType Directory -Path $MenuDirectory -Force | Out-Null
 foreach ($taskLink in @($taskDesktopLink, (Join-Path $MenuDirectory 'Template Cutting Automation.lnk'))) {
  $taskShortcut = $taskShell.CreateShortcut($taskLink)
  $taskShortcut.TargetPath = $Executable
  $taskShortcut.WorkingDirectory = $Destination
  $taskShortcut.IconLocation = (Join-Path $Destination '_internal\frontend\assets\template-logo-transparent-v2.ico') + ',0'
  $taskShortcut.Save()
 }
 # Remove only the old alias belonging to this app. A different program using
 # that filename is not ours to delete. No executable or workspace is removed.
 if (Test-Path -LiteralPath $taskDuplicateLink -PathType Leaf) {
  try {
   $taskDuplicateTarget = $taskShell.CreateShortcut($taskDuplicateLink).TargetPath
   if ($taskDuplicateTarget) {
    $taskFullTarget = [IO.Path]::GetFullPath($taskDuplicateTarget)
    $taskFullExe = [IO.Path]::GetFullPath($Executable)
    $taskFamily = [IO.Path]::GetFullPath((Split-Path -Parent $Destination)).TrimEnd('\') + '\'
    $taskMatchesNew = [string]::Equals($taskFullTarget,$taskFullExe,[StringComparison]::OrdinalIgnoreCase)
    $taskMatchesPrevious = $taskPreviousTarget -and [string]::Equals($taskFullTarget,[IO.Path]::GetFullPath($taskPreviousTarget),[StringComparison]::OrdinalIgnoreCase)
    $taskInFamily = ([IO.Path]::GetFileName($taskFullTarget) -ieq 'TemplateCutting.exe') -and $taskFullTarget.StartsWith($taskFamily,[StringComparison]::OrdinalIgnoreCase)
    if ($taskMatchesNew -or $taskMatchesPrevious -or $taskInFamily) {
     Remove-Item -LiteralPath $taskDuplicateLink
    } else {
     Write-Warning 'An unrelated TemplateCutting shortcut was left unchanged.'
    }
   } else { Write-Warning 'The old shortcut target could not be identified; it was left unchanged.' }
  } catch { Write-Warning ('Could not remove the legacy duplicate shortcut: ' + $_.Exception.Message) }
 }
}
