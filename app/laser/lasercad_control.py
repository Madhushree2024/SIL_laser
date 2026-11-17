"""Explicit, one-shot requests to the installed LaserCAD machine panel.

This adapter reports UI request delivery, never controller connectivity or physical
completion. It never calls Start, jog, Origin, or Datum as part of export.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import re
import sys
import subprocess
from threading import Lock

from app.export.lasercad import find_lasercad
from app.export.lasercad_preview import WindowsPreviewAPI, is_hosted_window


BUTTONS = {
    "frame": "Run Box", "download": "Download", "start": "Start",
    "pause": "Pause/Continue", "stop": "Stop", "origin": "Origin",
    "datum": "Datum", "x_minus": "X-", "x_plus": "X+",
    "y_minus": "Y-", "y_plus": "Y+", "u_minus": "U-", "u_plus": "U+",
    "read_position": "Read Current Pos:", "move_to": "Move to Pos:",
}
JOB_ACTIONS = {"frame", "download", "start"}


def normalize(text):
    return re.sub(r"\s+", "", text.replace("&", "")).casefold()


class ControlError(RuntimeError):
    pass


class NativeLaserCAD(WindowsPreviewAPI):
    def __init__(self):
        super().__init__()
        u = self.user
        u.EnumChildWindows.argtypes = [wintypes.HWND, self.callback, wintypes.LPARAM]
        u.GetDlgCtrlID.argtypes = [wintypes.HWND]
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        u.IsWindowEnabled.argtypes = [wintypes.HWND]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]

    def ready(self, window):
        return bool(self.user.IsWindowEnabled(window["hwnd"]) and super().ready(window))

    def text(self, hwnd):
        buffer = ctypes.create_unicode_buffer(2048)
        result = ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(hwnd, 0x000D, len(buffer), ctypes.addressof(buffer),
                                             0x22, 300, ctypes.byref(result)):
            return ""
        return buffer.value

    def controls(self, window):
        controls = []
        @self.callback
        def visit(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                if not is_hosted_window(window["hwnd"]) or not (self.user.GetWindowLongW(hwnd, -16) & 0x10000000):
                    return True
            kind = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, kind, len(kind))
            rect = wintypes.RECT()
            self.user.GetWindowRect(hwnd, ctypes.byref(rect))
            controls.append(dict(hwnd=int(hwnd), parent=int(self.user.GetParent(hwnd) or 0),
                                 control_id=self.user.GetDlgCtrlID(hwnd), text=self.text(hwnd),
                                 kind=kind.value, enabled=bool(self.user.IsWindowEnabled(hwnd)),
                                 rect=[rect.left, rect.top, rect.right, rect.bottom]))
            return True
        self.user.EnumChildWindows(window["hwnd"], visit, 0)
        return controls

    def click(self, window, control):
        # Revalidate the HWND, label, ID and process immediately before dispatch.
        live = self.controls(window)
        matches = [c for c in live if c["hwnd"] == control["hwnd"]
                   and c["control_id"] == control["control_id"]
                   and normalize(c["text"]) == normalize(control["text"])
                   and c["enabled"]]
        if len(matches) != 1 or not self.user.IsChild(window["hwnd"], control["hwnd"]):
            raise ControlError("LaserCAD control changed or is disabled. Refresh its status.")
        result = ctypes.c_size_t()
        # BM_CLICK simulates one complete native click; never hold a jog key.
        if not self.user.SendMessageTimeoutW(control["hwnd"], 0x00F5, 0, 0,
                                             0x22, 1000, ctypes.byref(result)):
            raise ControlError("LaserCAD did not acknowledge the click. Its outcome is unknown; do not retry automatically.")

    def set_text(self, window, control, value):
        if not self.user.IsChild(window["hwnd"], control["hwnd"]):
            raise ControlError("LaserCAD coordinate field changed.")
        buffer = ctypes.create_unicode_buffer(value)
        result = ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(control["hwnd"], 0x000C, 0, ctypes.addressof(buffer),
                                             0x22, 500, ctypes.byref(result)) or not result.value:
            raise ControlError("Could not set the LaserCAD coordinate field. No move was requested.")
        if self.text(control["hwnd"]).strip() != value:
            raise ControlError("LaserCAD did not retain the coordinate value. No move was requested.")


class LaserCADController:
    def __init__(self, config_getter, api=None):
        self.config_getter = config_getter
        self.api = api
        self.hosted_mode = False
        self.job = None
        self.lock = Lock()

    def open(self):
        if sys.platform != "win32":
            raise ControlError("LaserCAD requires Windows.")
        exe = find_lasercad(self.config_getter().get("lasercad", {}).get("executable", ""))
        if not exe:
            raise ControlError("Set the LaserCAD application path in Settings.")
        # Only open the application; no machine or document commands are sent.
        startup = {}
        if self.hosted_mode and self.config_getter().get("laser_control", {}).get("host_lasercad", False):
            info = subprocess.STARTUPINFO(); info.dwFlags |= subprocess.STARTF_USESHOWWINDOW; info.wShowWindow = 0
            startup["startupinfo"] = info
        process = subprocess.Popen([str(exe)], cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, **startup)
        return {"requested": True, "pid": process.pid, "message": "LaserCAD launch requested. Refresh status once its Control Panel is visible."}

    def set_job(self, path):
        self.job = Path(path).resolve() if path else None

    def _api(self):
        if self.api is not None:
            return self.api
        if sys.platform != "win32":
            raise ControlError("LaserCAD machine controls require Windows.")
        self.api = NativeLaserCAD()
        return self.api

    def _window(self, job_scoped=False, require_ready=True):
        config = self.config_getter()
        exe = find_lasercad(config.get("lasercad", {}).get("executable", ""))
        if not exe:
            raise ControlError("LaserCAD was not found. Set its application path in Settings.")
        expected = os.path.normcase(str(Path(exe).resolve()))
        matches = [w for w in self._api().windows()
                   if os.path.normcase(str(Path(w["executable"]).resolve())) == expected
                   and re.match(r"^LaserCAD\b", w["title"], re.I)]
        if job_scoped:
            matches = [window for window in matches if self._job_matches(window)]
        if len(matches) != 1:
            raise ControlError("Open exactly one LaserCAD drawing window, with its Control Panel visible.")
        window = matches[0]
        if require_ready and not self._api().ready(window):
            raise ControlError("LaserCAD is not responding. No command was sent.")
        return window

    def _buttons(self, controls):
        found = {}
        for action, label in BUTTONS.items():
            matches = [c for c in controls if "button" in c["kind"].casefold()
                       and normalize(c["text"]) == normalize(label)]
            if len(matches) == 1:
                found[action] = matches[0]
        return found

    def _job_matches(self, window):
        # Require the full filename, including .dxf, as a delimited title token.
        return bool(self.job and self.job.is_file() and self.job.suffix.lower() == ".dxf"
                    and re.search(r"(?<![\w.])" + re.escape(self.job.name) + r"(?![\w.])", window["title"], re.I))

    def _coordinates(self, controls):
        result = {}
        for axis in ("X", "Y"):
            labels = [c for c in controls if normalize(c["text"]) == axis.lower() + "="]
            if len(labels) != 1:
                continue
            label = labels[0]
            l, t, r, b = label["rect"]
            edits = [c for c in controls if "edit" in c["kind"].casefold()
                     and c["parent"] == label["parent"] and c["enabled"]
                     and 0 <= c["rect"][0] - r <= 40
                     and abs((c["rect"][1] + c["rect"][3]) / 2 - (t + b) / 2) <= 12]
            if len(edits) == 1:
                result[axis.lower()] = edits[0]
        return result

    def observation_snapshot(self):
        """Read existing visible controls only; do not query or operate the controller."""
        try:
            window=self._window(job_scoped=False,require_ready=False)
            api=self._api()
            controls=api.controls(window)
            coords=self._coordinates(controls)
            text=[{'control_id':c['control_id'],'kind':c['kind'],'text':c['text'][:512],
                   'enabled':c['enabled']} for c in controls
                  if c['text'] and any(k in c['kind'].casefold()
                                      for k in ('static','statusbar','button','combobox'))]
            return {'available':True,'window_title':window['title'],'process_id':window['pid'],
                    'executable':window['executable'],'main_window_enabled':bool(api.user.IsWindowEnabled(window['hwnd'])),
                    'displayed_position_unverified':{k:v['text'] for k,v in coords.items()},
                    'communication_selection':[c['text'] for c in controls if 'combobox' in c['kind'].casefold()],
                    'button_states':{k:v['enabled'] for k,v in self._buttons(controls).items()},
                    'visible_text':text[:100],'visible_text_truncated':len(text)>100,
                    'controller_telemetry_verified':False,'machine_commands_sent':False}
        except (ControlError,OSError,ValueError) as exc:
            return {'available':False,'error':str(exc),'controller_telemetry_verified':False,
                    'machine_commands_sent':False}

    def status(self, job_scoped=False):
        try:
            window = self._window(job_scoped=job_scoped)
            controls = self._api().controls(window)
            buttons = self._buttons(controls)
            coords = self._coordinates(controls)
            job_matches = self._job_matches(window)
            modes = [c["text"] for c in controls if "combobox" in c["kind"].casefold() and c["text"]]
            return {"available": True, "window_title": window["title"], "window_id": window['hwnd'], "process_id": window['pid'],
                    "machine_connected": None, "communication_selection": modes,
                    "job": str(self.job) if self.job else None, "job_matches": job_matches,
                    "displayed_position": {k: v["text"] for k, v in coords.items()},
                    "actions": {action: bool(buttons.get(action, {}).get("enabled")
                                             and (action not in JOB_ACTIONS.union({'pause'} if job_scoped else set()) or job_matches)
                                             and (action != "move_to" or len(coords) == 2))
                                for action in BUTTONS},
                    "message": "LaserCAD panel detected. Machine connection and physical completion are not verified by this bridge."}
        except (ControlError, OSError, ValueError) as exc:
            return {"available": False, "machine_connected": None, "actions": {},
                    "job": str(self.job) if self.job else None, "message": str(exc)}

    def request(self, action, *, x=None, y=None, confirm_start=False, job_scoped=False, expected_panel=None):
        if action not in BUTTONS:
            raise ControlError("Unsupported LaserCAD control.")
        if action == "start" and not confirm_start:
            raise ControlError("Confirm Start for the current exported DXF.")
        # Stop must not wait behind another UI request.
        acquired = action != "stop" and self.lock.acquire(blocking=False)
        if action != "stop" and not acquired:
            raise ControlError("Another LaserCAD request is pending. Refresh status before continuing.")
        try:
            window = self._window(job_scoped=job_scoped)
            if action in JOB_ACTIONS.union({'pause'} if job_scoped else set()) and not self._job_matches(window):
                raise ControlError("LaserCAD must show this application's latest exported DXF before Download, Run Box or Start.")
            controls = self._api().controls(window)
            button = self._buttons(controls).get(action)
            if not button or not button["enabled"]:
                raise ControlError("The LaserCAD " + BUTTONS[action] + " button is missing, ambiguous or disabled. Open its Control Panel and close any dialogs.")
            if action == "move_to":
                if x is None or y is None or any(not math.isfinite(v) or abs(v) > 100000 for v in (x, y)):
                    raise ControlError("Enter finite X/Y coordinates within ±100000 mm.")
                coords = self._coordinates(controls)
                if len(coords) != 2:
                    raise ControlError("Could not uniquely identify LaserCAD's X/Y fields. No move was requested.")
                for key, value in (("x", x), ("y", y)):
                    self._api().set_text(window, coords[key], format(value, ".6f"))
            # Revalidate the window/process before each explicit click.
            live = self._window(job_scoped=job_scoped)
            if live["hwnd"] != window["hwnd"] or live["pid"] != window["pid"] or live["title"] != window["title"]:
                raise ControlError("LaserCAD changed documents. No click was sent.")
            if expected_panel is not None:
                modes = [c['text'] for c in self._api().controls(live) if 'combobox' in c['kind'].casefold() and c['text']]
                if (live['hwnd'] != expected_panel.get('window_id') or live['pid'] != expected_panel.get('process_id')
                    or live['title'] != expected_panel.get('window_title') or modes != expected_panel.get('communication_selection')):
                    raise ControlError('The confirmed LaserCAD window or machine selection changed. No click was sent.')
            self._api().click(live, button)
            return {"requested": True, "action": action, "window_title": live["title"],
                    "message": BUTTONS[action] + " click delivered to LaserCAD. This is not confirmation of machine execution."}
        finally:
            if acquired:
                self.lock.release()
