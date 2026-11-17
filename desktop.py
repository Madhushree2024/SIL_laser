"""Native Windows window around the unchanged Sheath Cutting web application."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time
from threading import Event, Thread

from desktop_runtime import initialize_desktop_runtime, set_black_title_bar, bundle_root, activate_existing_window, Backend, SingleInstance, TITLE, desktop_url, error_dialog, prepare_workspace, user_directory, workspace_path


def main() -> int:
    parser = argparse.ArgumentParser(description='Template Cutting desktop application')
    parser.add_argument('--workspace', help='Existing project folder or persistent data folder')
    parser.add_argument('--port', type=int, default=8012)
    parser.add_argument('--verify-desktop', help=argparse.SUPPRESS)
    parser.add_argument('--verify-backend', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    instance = SingleInstance()
    backend = None
    try:
        if not instance.acquire():
            activate_existing_window()
            return 0
        workspace = workspace_path(args.workspace)
        log_dir = workspace / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s',
                            handlers=[RotatingFileHandler(log_dir / 'desktop.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')])
        # Windowed frozen applications have no stdout/stderr. Libraries can still log.
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w')
        prepare_workspace(workspace)
        backend = Backend(workspace, args.port)
        backend.start()
        if args.verify_backend:
            from desktop_runtime import get_json, get_local_bytes
            html = get_local_bytes(backend.url + '/').decode('utf-8')
            javascript_bytes = len(get_local_bytes(backend.url + '/static/app.js'))
            result = {'health': get_json(backend.url + '/api/health'), 'url': backend.url,
                      'html_title': 'Template Cutting' in html, 'javascript_bytes': javascript_bytes,
                      'authentication': get_json(backend.url + '/api/auth/me'),
                      'templates': len(backend.app_module.list_templates().get('templates', [])) if backend.app_module else None,
                      'calibration': backend.app_module.calibration_status() if backend.app_module else None, 'busy': backend.busy()}
            Path(args.verify_backend).write_text(json.dumps(result, indent=2), encoding='utf-8')
            return 0
        initialize_desktop_runtime()
        import webview
        webview.settings['ALLOW_DOWNLOADS'] = True
        window = webview.create_window(TITLE, desktop_url(backend.url), width=1440, height=900, min_size=(560, 600),
                                       maximized=not bool(args.verify_desktop), hidden=bool(args.verify_desktop),
                                       text_select=True, background_color='#111314')
        space_keys = None
        host = None
        if backend.app_module is not None:
            from app.laser_control.lasercad_host import DesktopLaserCADHost
            host = DesktopLaserCADHost(lambda: window.native, lambda: backend.app_module.lasercad_controller._window(job_scoped=bool(backend.app_module.lasercad_controller.job), require_ready=False))
            backend.app_module.laser_control_service.host_callback = host.request
            backend.app_module.lasercad_controller.hosted_mode = True
            from app.laser_control.window_layout import DesktopSplitLayout
            layout = DesktopSplitLayout(lambda: window.native.Handle.ToInt64(),
                lambda: backend.app_module.lasercad_controller._window(job_scoped=True))
            def arrange_windows(action):
                if action == 'split': host.request({'action':'detach'})
                return layout.request(action)
            backend.app_module.laser_control_service.layout_callback = arrange_windows
        def apply_title_bar(*_):
            try:
                return set_black_title_bar(window.native.Handle.ToInt64())
            except Exception:
                logging.exception('Could not style the native title bar')
                return False
        window.events.shown += apply_title_bar
        window.events.loaded += apply_title_bar
        def start_space_keys(*_):
            nonlocal space_keys
            if host is not None and space_keys is None:
                from app.laser_control.space_keys import DesktopSpaceKeys
                space_keys=DesktopSpaceKeys(window,host,completion_ready=lambda: dict(
                    backend.app_module.space_cycle.state or {}))
                space_keys.start()
        window.events.loaded += start_space_keys
        def closing():
            try:
                if backend.busy():
                    error_dialog('Processing is still active. Wait for export to finish before closing the application.')
                    return False
            except Exception:
                logging.exception('Could not check workflow status while closing')
                if backend.app_module is not None:
                    error_dialog('Could not confirm processing status. Please wait and try closing again.')
                    return False
            if host is not None:
                try: host.close()
                except Exception:
                    logging.exception('Could not detach the hosted LaserCAD window while closing')
                    return False
            if space_keys is not None: space_keys.close()
            return True
        window.events.closing += closing
        verification_done = Event()
        def verify():
            if not args.verify_desktop:
                return
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    result = window.evaluate_js('''JSON.stringify({header:document.querySelector('h1')?.textContent,
                        tabs:document.querySelectorAll('nav button').length,
                        themeCheck:(()=>{const buttons=[...document.querySelectorAll('[data-theme-toggle]')];
                          const before=document.documentElement.dataset.theme;buttons[0]?.click();
                          const changed=document.documentElement.dataset.theme;buttons[1]?.click();
                          return {buttons:buttons.length,before,changed,restored:document.documentElement.dataset.theme,
                            synced:buttons.every(b=>b.textContent===buttons[0].textContent),
                            colorScheme:getComputedStyle(document.documentElement).colorScheme};})(),
                        brandLogo:document.querySelector('.brand-logo')?.naturalWidth || 0,
                        signInLogo:document.querySelector('.auth-logo')?.naturalWidth || 0,
                        favicon:document.querySelector('link[rel="icon"]')?.getAttribute('href'),
                        access:document.body.dataset.auth,setupRequired:document.getElementById('authGate')?.dataset.setup,
                        authTitle:document.getElementById('authTitle')?.textContent,
                        processButton:!!document.getElementById('processSheath'),
                        manualCalibration:!!document.getElementById('workspaceWidth') && !!document.getElementById('workspaceHeight') && document.querySelectorAll('#workspaceMarkers .bed-marker').length===4,
                        excelDownload:!!document.getElementById('productionExcelDownload'),
                        initialized:typeof state!=='undefined' && state.templates.length>0,
                        textErrors:document.getElementById('messages')?.textContent || '',
                        camera:document.getElementById('cameraStream')?.naturalWidth || 0})''')
                    if isinstance(result, str):
                        result = json.loads(result)
                    if (result.get('access') == 'locked' and result.get('setupRequired') in {'true','false'}) or (result.get('initialized') and result.get('camera')):
                        break
                    time.sleep(.5)
                if not ((result.get('access') == 'locked' and result.get('setupRequired') in {'true','false'}) or (result.get('initialized') and result.get('camera'))):
                    raise RuntimeError('The desktop interface did not finish loading.')
                window.evaluate_js('(() => {\n const clone=document.body.cloneNode(true);\n clone.querySelectorAll(\'script,iframe,video,source\').forEach(n=>n.remove());\n clone.querySelectorAll(\'#cameraStream\').forEach(n=>n.removeAttribute(\'src\'));\n clone.dataset.auth=\'signed-in\';clone.dataset.screen=\'process\';clone.dataset.userRole=\'admin\';\n const auto=clone.querySelector(\'#automaticWorkflow\');auto.hidden=false;auto.textContent=\'Automatic workflow\';\n clone.querySelector(\'#cycleState\').textContent=\'Cycle interrupted · inspect machine before reset\';\n clone.querySelector(\'#signedInUser\').textContent=\'Operator | Admin\';\n const bar=clone.querySelector(\'#spaceCycleBar\');if(bar)bar.hidden=false;\n const f=document.createElement(\'iframe\');f.id=\'layoutVerification\';Object.assign(f.style,{position:\'fixed\',left:\'0\',top:\'0\',width:\'1366px\',height:\'900px\',border:\'0\',zIndex:\'99999\',background:\'#fff\'});\n const links=[...document.querySelectorAll(\'link[rel="stylesheet"]\')].map(n=>\'<link rel="stylesheet" href="\'+n.href+\'">\').join(\'\');\n f.onload=async()=>{\n  try{\n   const results=[];\n   for(const width of [1920,1366,1024,640]){\n    f.style.width=width+\'px\';await new Promise(r=>setTimeout(r,70));\n    const d=f.contentDocument;\n    results.push({width,client:d.documentElement.clientWidth,scroll:d.documentElement.scrollWidth,automaticInRow:!!d.querySelector(\'.cycle-top-row #automaticWorkflow\'),automaticInHeader:!!d.querySelector(\'.topbar #automaticWorkflow\'),themeWidth:d.querySelector(\'[data-theme-toggle]\').getBoundingClientRect().width});\n   }\n   f.style.width=\'1366px\';window.__layoutVerification={results};\n  }catch(e){window.__layoutVerification={error:String(e)}}\n };\n f.srcdoc=\'<!doctype html><html><head><meta charset="utf-8">\'+links+\'</head>\'+clone.outerHTML+\'</html>\';\n document.body.append(f);\n return true;\n})()')
                layout_deadline=time.monotonic()+15
                layout_result=None
                while time.monotonic()<layout_deadline:
                    layout_result=window.evaluate_js('window.__layoutVerification || null')
                    if layout_result:break
                    time.sleep(.1)
                if not layout_result or layout_result.get('error'):
                    raise RuntimeError('Responsive layout verification failed: '+str(layout_result))
                result['responsive_layout']=layout_result
                if any(row['scroll']>row['client']+1 or not row['automaticInRow'] or row['automaticInHeader'] or row['themeWidth']>38 for row in layout_result['results']):
                    raise RuntimeError('Responsive layout still overflows: '+str(layout_result))
                # Actual Account UI checks use fresh, isolated verification data only.
                # No initializeWorkspace(), camera request or machine action is called.
                if result.get('setupRequired')=='true' and 'verification' in str(workspace.parent).lower():
                    import secrets
                    password='UI-check-'+secrets.token_hex(24)
                    accounts=backend.app_module.app.state.account_store()
                    accounts.create('ui-verification-admin',password,'admin',initial=True)
                    accounts.create('ui-verification-manager',password,'editor',workflow_mode='manual')
                    accounts.create('ui-verification-reader',password,'viewer',workflow_mode='automated')
                    script="""(async()=>{
                      try {
                        const password=__PASSWORD__;
                        async function signIn(username){
                          const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
                          const data=await r.json();if(!r.ok)throw Error('Verification sign-in failed');acceptAccount(data);await refreshAccountPanel();
                        }
                        await signIn('ui-verification-admin');$('accountDialog').showModal();
                        const adminFull=hasPermission('users')&&hasPermission('configure')&&hasPermission('operate');
                        const field=document.querySelector('[aria-label="Workflow for ui-verification-reader"]');
                        field.value='manual';await field.closest('.account-user-row').querySelector('button').onclick();
                        const saved=(await api('/api/auth/users')).users.find(u=>u.username==='ui-verification-reader');
                        const d=$('accountDialog');const accountFits=d.scrollWidth<=d.clientWidth+1;
                        const accountOnly=!!$('newUserWorkflow').closest('#accountDialog')&&!document.querySelector('[data-panel="settings"] #newUserWorkflow');
                        $('accountDialog').close();
                        await signIn('ui-verification-reader');
                        const readerRestricted=!hasPermission('configure')&&!hasPermission('users')&&!hasPermission('templates')&&$('manageAccounts').hidden;
                        const readerOperates=hasPermission('operate')&&state.user.workflow_mode==='manual';
                        const settings=[...document.querySelectorAll('[data-panel="settings"] input,[data-panel="settings"] select,[data-panel="settings"] button')];
                        const settingsDisabled=settings.length>0&&settings.every(n=>n.disabled);
                        await signIn('ui-verification-manager');
                        const managerRestricted=!hasPermission('configure')&&!hasPermission('users')&&$('manageAccounts').hidden;
                        const managerOperates=hasPermission('operate')&&state.user.workflow_mode==='manual';
                        window.__accountVerification={adminFull,accountOnly,accountFits,adminSavedManual:saved.workflow_mode==='manual',readerRestricted,readerOperates,settingsDisabled,managerRestricted,managerOperates,supervisedRemoved:!document.getElementById('supervisedCutting')};
                      }catch(e){window.__accountVerification={error:String(e)}}
                    })()""".replace('__PASSWORD__',json.dumps(password))
                    window.evaluate_js(script)
                    account_deadline=time.monotonic()+25
                    account_result=None
                    while time.monotonic()<account_deadline:
                        account_result=window.evaluate_js('window.__accountVerification || null')
                        if account_result:break
                        time.sleep(.1)
                    if not account_result or account_result.get('error') or not all(account_result.values()):
                        raise RuntimeError('Account UI verification failed: '+str(account_result))
                    result['account_ui']=account_result
                result.update(url=backend.url, renderer='edgechromium', workflow_busy=backend.busy())
                Path(args.verify_desktop).write_text(json.dumps(result, indent=2), encoding='utf-8')
            except Exception as exc:
                logging.exception('Desktop renderer verification failed')
                Path(args.verify_desktop).write_text(json.dumps({'error':str(exc)}),encoding='utf-8')
            finally:
                verification_done.set()
                window.destroy()
        window.events.loaded += lambda: Thread(target=verify, daemon=True).start() if args.verify_desktop else None
        if args.verify_desktop:
            def verification_timeout():
                if not verification_done.wait(90):
                    Path(args.verify_desktop).write_text(json.dumps({'error': 'Desktop renderer did not load within 90 seconds.'}), encoding='utf-8')
                    window.destroy()
            Thread(target=verification_timeout, daemon=True).start()
        webview.start(gui='edgechromium', icon=str(bundle_root() / 'frontend/assets/template-logo-transparent-v2.ico'), private_mode=False, storage_path=str(workspace / '.desktop-state' / 'Browser'))
        return 0
    except Exception as exc:
        logging.exception('Desktop application startup failed')
        if args.verify_backend or args.verify_desktop:
            Path(args.verify_backend or args.verify_desktop).write_text(json.dumps({'error': str(exc)}), encoding='utf-8')
        else:
            error_dialog(f'Could not open Template Cutting.\n\n{exc}\n\nYour calibration and production records are preserved. Check logs/desktop.log in the data folder.')
        return 1
    finally:
        if backend is not None:
            backend.stop()
        instance.close()


if __name__ == '__main__':
    raise SystemExit(main())
