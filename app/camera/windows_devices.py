"""Read DirectShow camera identities in the same order as OpenCV's Windows indexes."""
from __future__ import annotations

import base64
import json
import logging
import subprocess

# Framework COM interfaces require no third-party package. This enumerates identities;
# it never opens a camera or changes device settings.
_SCRIPT = r'''$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

[ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ICreateDevEnum {
    [PreserveSig] int CreateClassEnumerator(ref Guid category, out IEnumMoniker enumerator, int flags);
}
[ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyBag {
    [PreserveSig] int Read([MarshalAs(UnmanagedType.LPWStr)] string name, [MarshalAs(UnmanagedType.Struct)] out object value, IntPtr errorLog);
    [PreserveSig] int Write([MarshalAs(UnmanagedType.LPWStr)] string name, [MarshalAs(UnmanagedType.Struct)] ref object value);
}
public class CameraIdentity {
    public int index;
    public string name;
    public string device_path;
}
public static class DirectShowIdentity {
    public static List<CameraIdentity> Read() {
        var result = new List<CameraIdentity>();
        object deviceEnum = null;
        IEnumMoniker enumerator = null;
        try {
            deviceEnum = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")));
            Guid category = new Guid("860BB310-5D01-11D0-BD3B-00A0C911CE86");
            if (((ICreateDevEnum)deviceEnum).CreateClassEnumerator(ref category, out enumerator, 0) != 0) return result;
            IMoniker[] monikers = new IMoniker[1];
            int index = 0;
            while (enumerator.Next(1, monikers, IntPtr.Zero) == 0) {
                object bagObject = null;
                try {
                    Guid bagId = new Guid("55272A00-42CB-11CE-8135-00AA004BB851");
                    monikers[0].BindToStorage(null, null, ref bagId, out bagObject);
                    var bag = (IPropertyBag)bagObject;
                    object name, devicePath;
                    bag.Read("FriendlyName", out name, IntPtr.Zero);
                    bag.Read("DevicePath", out devicePath, IntPtr.Zero);
                    result.Add(new CameraIdentity { index = index, name = Convert.ToString(name), device_path = Convert.ToString(devicePath) });
                } finally {
                    if (bagObject != null) Marshal.ReleaseComObject(bagObject);
                    Marshal.ReleaseComObject(monikers[0]);
                }
                index++;
            }
            return result;
        } finally {
            if (enumerator != null) Marshal.ReleaseComObject(enumerator);
            if (deviceEnum != null) Marshal.ReleaseComObject(deviceEnum);
        }
    }
}
"@
ConvertTo-Json -InputObject @([DirectShowIdentity]::Read()) -Compress
'''


def windows_camera_profiles() -> list[dict]:
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")],
            capture_output=True, check=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        profiles = json.loads(result.stdout.lstrip("\ufeff"))
        return profiles if isinstance(profiles, list) else []
    except (OSError, subprocess.SubprocessError, ValueError):
        logging.getLogger("uvicorn.error").exception("Unable to identify USB cameras using DirectShow")
        return []


def is_usb_camera(camera: dict) -> bool:
    name = (camera.get("name") or "").lower()
    if any(word in name for word in ("macbook", "iphone", "facetime", "continuity", "integrated", "built-in", "builtin", "hp hd", "virtual", "obs")):
        return False
    return any(word in name for word in ("usb", "uvc", "imx", "waveshare")) or "usb#" in (camera.get("device_path") or "").lower()
