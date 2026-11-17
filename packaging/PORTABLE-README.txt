TEMPLATE CUTTING - PORTABLE WINDOWS APPLICATION - 1.4.11

1. Copy TemplateCutting-Portable-1.4.11.zip onto the Windows PC.
2. Right-click the ZIP and choose Extract All. Use a NEW writable local folder.
3. Open the extracted TemplateCutting folder.
4. Double-click Start Template Cutting.cmd. TemplateCutting.exe can also be used directly.
Keep the complete _internal folder beside the EXE. Do not run inside the ZIP.
Do not merge this package into an older application folder.

REPAIR INCLUDED
Windows can mark DLL files extracted from downloaded ZIPs as downloaded files.
That blocks Python.Runtime.Loader.Initialize. Version 1.4.11 removes that metadata
from only its bundled Python.NET/CLR/WebView runtime DLLs before loading the UI.
DLL contents, calibration and production logic are unchanged. The launcher also
checks the Windows .NET Framework prerequisite. No administrator account is
required for normal application use.

WINDOWS PREREQUISITES
Windows 10/11 x64, Microsoft .NET Framework 4.8 Runtime and Edge WebView2 Runtime.
Python does NOT need to be installed separately.
.NET: https://dotnet.microsoft.com/download/dotnet-framework/net48
WebView2: https://developer.microsoft.com/microsoft-edge/webview2/
Prerequisite installation may require the PC administrator.

DATA AND CALIBRATION
On a new PC, data defaults to:
%LOCALAPPDATA%\SheathTemplateCuttingManualCalibration\Workspace
Existing workspace files are not overwritten. This portable ZIP excludes account
databases, production history and captured images. A new workspace requires initial
Admin setup. To retain existing data, preserve the entire existing workspace and run:
TemplateCutting.exe --workspace "C:\path\to\existing-workspace"
The existing default project folder is reused when present:
%USERPROFILE%\sheathTemplateCutting_manual_calibration

The included seed calibration is unchanged from the build PC. It is NOT calibrated
for a new camera/bed. In Calibration, enter the actual marker coordinates and save
from a new photo. For the reported new rectangular layout, centre spacings are
368 mm across and 725 mm down: ID11 (0,0), ID12 (368,0), ID13 (368,725), ID10 (0,725).
Verify laser origin/directions separately. This release does not change those values.

If startup still fails, send the workspace logs\desktop.log. The repair was tested
against the reproduced DLL-block failure on the build PC; the remote PC still needs
its own startup check. Do not delete calibration or production files to troubleshoot.
