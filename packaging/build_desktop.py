from pathlib import Path
from datetime import datetime
import hashlib,json,shutil,subprocess,sys
root=Path(__file__).resolve().parents[1]
seed=root/'build/desktop-seed';seed.mkdir(parents=True,exist_ok=True)
files=['app/config/config.yaml','camera_calibration.npz','data/templates/templates.json']
files += [str(p.relative_to(root)) for p in (root/'data/calibration').glob('*') if p.is_file()]
manifest={}
for relative in files:
 source=root/relative
 if source.is_file():
  target=seed/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
  manifest[relative]=hashlib.sha256(source.read_bytes()).hexdigest()
(seed/'seed-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
release = root/'dist/releases'/datetime.now().strftime('%Y%m%d-%H%M%S')
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(release),str(root/'packaging/SheathTemplateCutting.spec')],cwd=root,check=True)
print(release/'TemplateCutting/TemplateCutting.exe')
relative = str((release/'TemplateCutting/TemplateCutting.exe').relative_to(root))
launcher = root/'packaged-launch.cmd'
temporary = launcher.with_suffix('.cmd.tmp')
temporary.write_text('@echo off\n"%~dp0'+relative+'" --workspace "%LOCALAPPDATA%\\SheathTemplateCuttingAutomation-Source\\Workspace" --port 8012\n', encoding='utf-8')
temporary.replace(launcher)
