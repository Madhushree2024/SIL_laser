$ErrorActionPreference = 'Stop'
try {
    $packageRoot = $PSScriptRoot
    $exe = Join-Path $packageRoot 'TemplateCutting.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Extract the entire ZIP first. TemplateCutting.exe must be beside this launcher.' }
    $dll = Join-Path $packageRoot '_internal\pythonnet\runtime\Python.Runtime.dll'
    if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) { throw 'The _internal folder is missing or incomplete. Extract the entire ZIP into a new folder.' }
    Unblock-File -LiteralPath $exe
    foreach ($relative in @('_internal\pythonnet\runtime', '_internal\clr_loader', '_internal\webview\lib')) {
        $directory = Join-Path $packageRoot $relative
        if (Test-Path -LiteralPath $directory -PathType Container) {
            Get-ChildItem -LiteralPath $directory -Recurse -File -Filter '*.dll' | Unblock-File
        }
    }
    $framework = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue
    if (-not $framework -or $framework.Release -lt 461808) {
        throw 'Install Microsoft .NET Framework 4.8 Runtime, restart Windows and try again: https://dotnet.microsoft.com/download/dotnet-framework/net48'
    }
    Start-Process -FilePath $exe -WorkingDirectory $packageRoot
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Template Cutting - startup help') | Out-Null
    exit 1
}
