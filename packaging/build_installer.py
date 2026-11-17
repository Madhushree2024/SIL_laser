from pathlib import Path
import json,shutil,subprocess,zipfile
root=Path(__file__).resolve().parents[1]
app=root/'dist/SheathTemplateCutting'
stage=root/'build/installer';stage.mkdir(parents=True,exist_ok=True)
shutil.copy2(root/'packaging/Uninstall.ps1',app/'Uninstall.ps1')
(app/'installation.json').write_text(json.dumps({'ApplicationId':'SheathTemplateCutting','Version':'1.4.10'}),encoding='utf-8')
for filename in ['Install.ps1','Setup.cmd']:shutil.copy2(root/'packaging'/filename,stage/filename)
with zipfile.ZipFile(stage/'application.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
 for path in app.rglob('*'):
  if path.is_file():archive.write(path,path.relative_to(app).as_posix())
output=root/'dist/SheathTemplateCutting-Setup.exe'
sed=f'''[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=Install Sheath Template Cutting for your Windows account? Your calibration and production data will be preserved.
DisplayLicense=
FinishMessage=
TargetName={output}
FriendlyName=Sheath Template Cutting Setup
AppLaunched=cmd.exe /c Setup.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=cmd.exe /c Setup.cmd -Quiet
UserQuietInstCmd=cmd.exe /c Setup.cmd -Quiet
SourceFiles=SourceFiles
[SourceFiles]
SourceFiles0={stage}\\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
[Strings]
FILE0="Setup.cmd"
FILE1="Install.ps1"
FILE2="application.zip"
'''
spec=stage/'installer.sed';spec.write_text(sed,encoding='utf-8')
subprocess.run(['iexpress.exe','/N','/Q',str(spec)],cwd=stage,check=True)
if not output.is_file():raise RuntimeError('Windows installer was not generated')
# Apply the same branding to the installer without changing its payload.
from PyInstaller.utils.win32.icon import CopyIcons_FromIco
import pefile, hashlib
def non_icon_resources(path):
 pe=pefile.PE(str(path))
 entries={}
 for kind in getattr(pe,'DIRECTORY_ENTRY_RESOURCE',()).entries:
  if kind.id in (3,14):
   continue
  for item in kind.directory.entries:
   for language in item.directory.entries:
    data=language.data.struct
    key=(kind.id,str(kind.name),item.id,str(item.name),language.id)
    entries[key]=hashlib.sha256(pe.get_data(data.OffsetToData,data.Size)).hexdigest()
 overlay=pe.get_overlay()
 pe.close()
 return entries,hashlib.sha256(overlay).hexdigest() if overlay else None
before=non_icon_resources(output)
CopyIcons_FromIco(str(output),[str(root/'frontend/assets/app-icon-v142.ico')])
if non_icon_resources(output)!=before:
 raise RuntimeError('Installer payload changed during icon update')
print(output)
