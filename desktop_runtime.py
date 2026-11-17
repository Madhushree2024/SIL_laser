"""Desktop runtime helpers. Processing remains in the existing FastAPI app."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sys
from threading import Thread
import time
from http.client import HTTPConnection
from urllib.error import HTTPError
from urllib.parse import urlsplit
from uuid import uuid4

TITLE = 'Template Cutting Automation'
SOURCE_ROOT = Path(__file__).resolve().parent


def bundle_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', SOURCE_ROOT)).resolve()



def unblock_bundled_runtime(bundle: Path | None = None) -> list[str]:
    """Remove download-zone metadata only from this app's bundled runtime DLLs.

    Windows ZIP extraction can propagate Zone.Identifier to managed assemblies,
    causing CLR to reject Python.Runtime.Loader.Initialize before the UI opens.
    DLL contents, settings and calibration files are never modified.
    """
    if sys.platform != 'win32':
        return []
    root = (bundle or bundle_root()).resolve()
    repaired = []
    for folder in ('pythonnet/runtime', 'clr_loader', 'webview/lib'):
        directory = root / folder
        if not directory.is_dir():
            continue
        for dll in directory.rglob('*.dll'):
            actual = dll.resolve()
            if not actual.is_relative_to(root):
                raise RuntimeError('Desktop runtime file points outside the application folder.')
            zone = Path(str(actual) + ':Zone.Identifier')
            try:
                zone.unlink()
                repaired.append(str(actual.relative_to(root)))
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise RuntimeError(
                    'Windows blocked a desktop runtime DLL. Extract the entire ZIP to a writable '
                    'local folder and run Start Template Cutting.cmd. '
                    'Your calibration and production files are unchanged.'
                ) from exc
    return repaired


def initialize_desktop_runtime() -> None:
    """Use the Windows desktop CLR, independent of other apps' runtime variables."""
    repaired = unblock_bundled_runtime()
    if repaired:
        logging.info('Removed download-zone metadata from bundled runtime DLLs: %s', repaired)
    from pythonnet import load
    load('netfx')


def desktop_url(base: str) -> str:
    # A fresh document URL prevents WebView2 from restoring pre-upgrade HTML.
    return base.rstrip('/') + '/?desktop_boot=' + uuid4().hex


def user_directory() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'SheathTemplateCuttingAutomation'


def workspace_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not getattr(sys, 'frozen', False):
        return SOURCE_ROOT
    # This edition must never fall back to the original installation's workspace.
    return user_directory() / 'Workspace'


def prepare_workspace(workspace: Path, bundle: Path | None = None) -> None:
    """Copy only missing initial settings; never overwrite user data on update."""
    bundle = bundle or bundle_root()
    seed = bundle / 'seed'
    for relative in ['data/projects', 'data/production', 'data/calibration', 'data/templates', 'app/config']:
        (workspace / relative).mkdir(parents=True, exist_ok=True)
    if seed.is_dir():
        for source in seed.rglob('*'):
            if source.is_file():
                target = workspace / source.relative_to(seed)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation also protects concurrent first starts.
                    try:
                        with target.open('xb') as stream, source.open('rb') as original:
                            shutil.copyfileobj(original, stream)
                    except FileExistsError:
                        pass
    if not (workspace / 'app/config/config.yaml').is_file():
        raise RuntimeError('Application settings are missing. Reinstall the application or select the existing project folder.')
    # This runs only after the old desktop process has released its instance lock.
    if getattr(sys, 'frozen', False):
        upgrade_embedded_download_settings(workspace, bundle)
    from app.calibration.common_origin import migrate_workspace
    report = migrate_workspace(workspace)
    if report.get('migrated'): logging.info('Shared ID 12 origin activated: %s',report)
    if getattr(sys, 'frozen', False):
        from app.calibration.usable_travel_upgrade import upgrade_usable_travel
        upgrade_usable_travel(workspace,bundle)
        from app.laser_control.cut_recipe import upgrade_cut_recipe
        upgrade_cut_recipe(workspace)




def upgrade_embedded_download_settings(workspace: Path, bundle: Path) -> dict:
    """Apply the requested deployment preferences once, preserving survey data."""
    import yaml
    state = workspace / 'laser_control_state' / 'embedded-download-upgrade-v1.json'
    if state.exists():
        return {'status': 'already_applied'}
    config_path = workspace / 'app/config/config.yaml'
    original = config_path.read_bytes()
    config = yaml.safe_load(original.decode('utf-8-sig'))
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise RuntimeError('Application settings must be a YAML mapping.')
    for name in ('laser_control', 'lasercad', 'machine_workflow'):
        if name in config and not isinstance(config[name], dict):
            raise RuntimeError('Invalid settings section: ' + name)
        config.setdefault(name, {})
    cad = config['lasercad']
    options = cad.setdefault('prepare_and_download', {})
    if not isinstance(options, dict):
        raise RuntimeError('Invalid LaserCAD download settings.')
    config['laser_control'].update(dashboard_enabled=False, host_lasercad=True, split_after_dxf=False)
    cad.update(auto_open_dxf=True, auto_frame_after_dxf=False)
    options.update(enabled=True, communication_mode='USB', usb_port='MachName:_AWC_D2713611',
                   speed_mm_s=40, power_percent=50, work_mode='Cut')
    # Do not enable framing, motion or Start as a side effect of this upgrade.
    config['machine_workflow']['enabled'] = False
    bundled_cad = bundle.parent / 'LaserCAD/AWCPrograms/LaserCAD.exe'
    if bundled_cad.is_file():
        cad['executable'] = str(bundled_cad.resolve())
    state.parent.mkdir(parents=True, exist_ok=True)
    backup = state.parent / ('config-before-embedded-download-' + uuid4().hex + '.yaml')
    with backup.open('xb') as stream:
        stream.write(original)
    temporary = config_path.with_name('config.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
    report = {'status': 'applied', 'backup': str(backup),
              'embedded_lasercad': True, 'split_screen': False,
              'automatic_download': True, 'start_manual': True}
    # A crash before this receipt is safe: no machine commands are dispatched here.
    receipt = state.with_name(state.name + '.' + uuid4().hex + '.tmp')
    receipt.write_text(json.dumps(report, indent=2), encoding='utf-8')
    os.replace(receipt, state)
    logging.info('Embedded LaserCAD/download settings upgraded: %s', report)
    return report


def get_local_bytes(url: str, timeout: float = 2) -> bytes:
    """Read the local backend without initializing proxy or HTTPS certificate state.

    A frozen Windows app may inherit certificate paths belonging to its launcher.
    Its plain HTTP loopback traffic does not need that external configuration.
    """
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password:
        raise ValueError('Desktop backend requests must use http://127.0.0.1.')
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''))
        response = connection.getresponse()
        if response.status >= 400:
            raise HTTPError(url, response.status, response.reason, response.headers, None)
        return response.read()
    finally:
        connection.close()


def get_json(url: str, timeout: float = 2) -> dict:
    return json.loads(get_local_bytes(url, timeout))


def existing_backend(url: str, workspace: Path) -> bool:
    try:
        health = get_json(url + '/api/health', 1)
    except (OSError, ValueError):
        return False
    if health.get('application') != 'areca-cutter':
        return False
    data_root = health.get('data_root')
    if not data_root:
        # Compatibility with the already-running browser version before packaging.
        project = get_json(url + '/api/state').get('project_dir')
        if project and Path(project).resolve().is_relative_to((workspace / 'data').resolve()):
            return True
    elif Path(data_root).resolve() == workspace.resolve():
        return True
    raise RuntimeError('Another Sheath Cutting backend is using a different data folder. Close it before opening this application.')


def activate_existing_window(timeout: float = 5) -> bool:
    """Restore the actual app window when its shortcut is opened again."""
    if sys.platform != 'win32':
        return False
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    user.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user.ShowWindowAsync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user.AllowSetForegroundWindow.argtypes = [ctypes.c_ulong]
    deadline = time.monotonic() + timeout
    while True:
        matches = []
        @callback_type
        def visit(hwnd, _):
            title = ctypes.create_unicode_buffer(512)
            kind = ctypes.create_unicode_buffer(128)
            user.GetWindowTextW(hwnd, title, len(title))
            user.GetClassNameW(hwnd, kind, len(kind))
            # Ignore error dialogs; only restore the native WebView window.
            if title.value == TITLE and kind.value != '#32770':
                matches.append(hwnd)
            return 1
        user.EnumWindows(visit, None)
        if matches:
            hwnd = matches[0]
            pid = ctypes.c_ulong()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            user.AllowSetForegroundWindow(pid.value)
            user.ShowWindowAsync(hwnd, 9)  # SW_RESTORE, including minimized windows.
            return bool(user.SetForegroundWindow(hwnd))
        if time.monotonic() >= deadline:
            return False
        time.sleep(.1)


def set_black_title_bar(hwnd: int) -> bool:
    """Keep this application's native caption black without changing Windows settings."""
    if sys.platform != 'win32':
        return False
    try:
        dwm = ctypes.WinDLL('dwmapi', use_last_error=True)
        setter = dwm.DwmSetWindowAttribute
        setter.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        setter.restype = ctypes.c_long
        results = {}
        # Dark caption buttons, no backdrop, black border and caption, white text.
        for attribute, value in [(20, 1), (38, 1), (34, 0), (35, 0), (36, 0xFFFFFF)]:
            colour = ctypes.c_uint(value)
            results[attribute] = setter(hwnd, attribute, ctypes.byref(colour), ctypes.sizeof(colour))
        return results[35] == 0 and results[36] == 0
    except (OSError, AttributeError):
        logging.exception('Could not apply the black application title bar')
        return False


class SingleInstance:
    def __init__(self):
        self.handle = None

    def acquire(self) -> bool:
        if sys.platform != 'win32':
            return True
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        self.handle = kernel.CreateMutexW(None, False, 'Local\\SheathTemplateCuttingAutomationDesktop')
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(self.handle)
            self.handle = None


class Backend:
    def __init__(self, workspace: Path, preferred_port: int = 8001):
        self.workspace = workspace.resolve()
        self.url = f'http://127.0.0.1:{preferred_port}'
        self.server = None
        self.thread = None
        self.socket = None
        self.app_module = None

    def start(self):
        if existing_backend(self.url, self.workspace):
            logging.info('Using existing application backend at %s', self.url)
            return
        # Set the persistent path before importing the existing application.
        os.environ['SHEATH_WORKSPACE'] = str(self.workspace)
        import app.main as main
        import uvicorn
        self.app_module = main
        if main.ROOT.resolve() != self.workspace:
            raise RuntimeError('The backend has already loaded another workspace; restart the desktop application.')
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                self.socket.bind(('127.0.0.1', int(self.url.rsplit(':', 1)[1])))
            except OSError:
                self.socket.bind(('127.0.0.1', 0))
            self.socket.listen(128)
            self.url = f'http://127.0.0.1:{self.socket.getsockname()[1]}'
            self.server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=self.socket.getsockname()[1], loop='asyncio', http='h11', ws='none', log_config=None, access_log=False, timeout_graceful_shutdown=5))
            self.thread = Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, name='desktop-backend')
            self.thread.start()
            deadline = time.monotonic() + 60
            last_error = 'No health response'
            while time.monotonic() < deadline:
                if not self.thread.is_alive():
                    raise RuntimeError('The application backend stopped during startup. Check desktop.log.')
                try:
                    if get_json(self.url + '/api/health', 1).get('application') == 'areca-cutter':
                        logging.info('Started application backend at %s', self.url)
                        return
                except (OSError, ValueError) as exc:
                    last_error = str(exc)
                time.sleep(.1)
            raise RuntimeError(f'Application startup timed out: {last_error}. Check desktop.log.')
        except BaseException:
            self.stop()
            raise

    def busy(self) -> bool:
        if self.app_module is not None:
            return self.app_module.operation_lock.locked() or bool(self.app_module.WORKFLOW.get('busy'))
        try:
            return bool(get_json(self.url + '/api/workflow').get('busy'))
        except HTTPError as exc:
            # An attached backend keeps working when this window closes. Before
            # sign-in it deliberately does not disclose workflow state.
            if exc.code in {401,403}:return False
            raise

    def stop(self):
        # Never stop a browser/backend process that this desktop instance did not start.
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                logging.warning('Backend is still finishing its existing operation; waiting for cleanup.')
                self.thread.join()
        if self.socket is not None:
            self.socket.close()


def error_dialog(message: str):
    if sys.platform == 'win32':
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    else:
        print(message, file=sys.stderr)
