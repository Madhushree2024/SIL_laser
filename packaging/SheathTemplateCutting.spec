from pathlib import Path
root = Path(SPECPATH).parent
seed = root / 'build' / 'desktop-seed'
datas = [(str(root / 'frontend'), 'frontend'), (str(root / 'app/export/templates/daily_production.xlsx'), 'app/export/templates'), (str(seed), 'seed')]
hidden = ['app.main','uvicorn.logging','uvicorn.loops.auto','uvicorn.loops.asyncio','uvicorn.protocols.http.auto','uvicorn.protocols.http.h11_impl','uvicorn.protocols.websockets.auto','uvicorn.lifespan.on','webview.platforms.winforms','webview.platforms.edgechromium']
a = Analysis([str(root/'desktop.py')], pathex=[str(root)], binaries=[], datas=datas, hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest','tkinter','PyQt5','PyQt6','PySide2','PySide6','IPython','matplotlib','pandas'], noarchive=False, optimize=0)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='TemplateCutting',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,icon=str(root/'frontend/assets/template-logo-transparent-v2.ico'),version=str(root/'packaging/version_info.txt'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='TemplateCutting')
