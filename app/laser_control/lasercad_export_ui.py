"""Checked Windows controls for LaserCAD V8.28.3 (AWC7813).

Control IDs were inspected in the installed application's dialog resources.
Only the export-preparation controls below are dispatched, using native messages.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import hashlib
import ipaddress
import re
import struct
from time import monotonic, sleep
from app.export.lasercad_preview import is_hosted_window
from app.laser.lasercad_control import NativeLaserCAD, ControlError, normalize


def named_usb_entry(target):
    return bool(re.fullmatch(r'MachName:[A-Za-z0-9_.-]{1,80}',target))


def usb_entry_matches(entry,target):
    if named_usb_entry(target):return entry.strip()==target
    return bool(re.search(r'(?<![A-Za-z0-9])'+re.escape(target)+r'(?![A-Za-z0-9])',entry,re.I))


class _VariantValue(ctypes.Union):
    _fields_=[('integer',ctypes.c_long),('wide',ctypes.c_longlong),('record',ctypes.c_void_p*2)]
class _Variant(ctypes.Structure):
    _anonymous_=('value',)
    _fields_=[('vt',ctypes.c_ushort),('reserved',ctypes.c_ushort*3),('value',_VariantValue)]
class _GUID(ctypes.Structure):
    _fields_=[('data1',ctypes.c_ulong),('data2',ctypes.c_ushort),('data3',ctypes.c_ushort),('data4',ctypes.c_ubyte*8)]


class NativeExportUI(NativeLaserCAD):
    def __init__(self,controller,window,path,digest):
        super().__init__()
        self.controller,self.window,self.path,self.digest=controller,window,path,digest
        self.user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];self.user.GetAncestor.restype=wintypes.HWND
        self.user.GetWindow.argtypes=[wintypes.HWND,wintypes.UINT];self.user.GetWindow.restype=wintypes.HWND
        self.user.GetDlgItem.argtypes=[wintypes.HWND,ctypes.c_int];self.user.GetDlgItem.restype=wintypes.HWND
        self.user.IsWindow.argtypes=[wintypes.HWND]
        self.user.IsWindowUnicode.argtypes=[wintypes.HWND]
        self.user.IsChild.argtypes=[wintypes.HWND,wintypes.HWND]
        self.user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.kernel.OpenProcess.restype=wintypes.HANDLE
        self.kernel.VirtualAllocEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_size_t,wintypes.DWORD,wintypes.DWORD]
        self.kernel.VirtualAllocEx.restype=ctypes.c_void_p
        for name in ['ReadProcessMemory','WriteProcessMemory']:
            getattr(self.kernel,name).argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        self.kernel.VirtualFreeEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_size_t,wintypes.DWORD]
        self.remote=None;self.process=None;self.uncertain=False;self.com_initialized=False

    def __enter__(self):
        try:return self._open()
        except Exception:
            self.__exit__()
            raise

    def _open(self):
        # The remote structures below are pointer-free RECT/DWORD structures and
        # therefore work with both 32-bit LaserCAD and this 64-bit Python host.
        self.ole=ctypes.WinDLL('ole32')
        self.ole.CoInitializeEx.argtypes=[ctypes.c_void_p,wintypes.DWORD]
        self.ole.CoInitializeEx.restype=ctypes.c_long
        self.ole.CoUninitialize.restype=None
        hr=self.ole.CoInitializeEx(None,2)
        if hr not in (0,1,-2147417850):raise ControlError('Could not initialize Windows accessibility.')
        self.com_initialized=hr in (0,1)
        self.process=self.kernel.OpenProcess(0x1038,False,self.window['pid'])
        if not self.process: raise ControlError('Cannot inspect LaserCAD controls at the current Windows privilege level.')
        self.remote=self.kernel.VirtualAllocEx(self.process,None,4096,0x3000,0x04)
        if not self.remote:
            self.kernel.CloseHandle(self.process);self.process=None
            raise ControlError('Cannot allocate the native control inspection buffer.')
        self._wait(lambda:bool(self.user.IsWindowVisible(self.window['hwnd'])) and self.ready(self.window), 'LaserCAD is not visible in its tab. No setup or download was requested.', timeout=12)
        return self

    def __exit__(self,*args):
        # A timed-out message can still be using its remote buffer. Leave that
        # small allocation to process teardown instead of freeing it underneath it.
        if self.remote and not self.uncertain:self.kernel.VirtualFreeEx(self.process,self.remote,0,0x8000)
        if self.process:self.kernel.CloseHandle(self.process)
        if self.com_initialized:self.ole.CoUninitialize()

    def _identity(self):
        live=self.window_metadata(self.window['hwnd'])
        same_title = live and live['title'].strip().strip('* ') == self.window['title'].strip().strip('* ')
        if not live or not same_title or any(live[k]!=self.window[k] for k in ['pid','executable']):
            raise ControlError('LaserCAD changed windows or documents during automatic preparation.')
        if not self.controller.job or self.controller.job.resolve()!=self.path or hashlib.sha256(self.path.read_bytes()).hexdigest()!=self.digest:
            raise ControlError('The exported DXF changed during automatic preparation.')

    def _belongs(self,hwnd):
        pid=wintypes.DWORD();self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value!=self.window['pid']:return False
        if hwnd==self.window['hwnd'] or self.user.IsChild(self.window['hwnd'],hwnd):return True
        # An embedded LaserCAD popup can be owned by our outer host window.
        # Require the exact LaserCAD PID and the same host root, not title alone.
        host_root=self.user.GetAncestor(self.window['hwnd'],2) if is_hosted_window(self.window['hwnd']) else None
        seen=set();pending=[hwnd]
        while pending and len(seen)<64:
            current=pending.pop()
            if current in seen or not current:continue
            if current==self.window['hwnd'] or (host_root and current==host_root):return True
            seen.add(current)
            pending.extend([self.user.GetParent(current),self.user.GetWindow(current,4)])  # parent + GW_OWNER
        return False

    def _send(self,hwnd,msg,wparam=0,lparam=0):
        self._identity()
        if not self._belongs(hwnd):raise ControlError('A preparation control belongs to another LaserCAD window.')
        result=ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(hwnd,msg,wparam,lparam,0x22,700,ctypes.byref(result)):
            self.uncertain=True
            raise ControlError('LaserCAD did not acknowledge a control request. Its outcome is unknown.')
        return ctypes.c_ssize_t(result.value).value

    def _post(self,hwnd,msg,wparam=0,lparam=0):
        self._identity()
        if not self._belongs(hwnd) or not self.user.IsWindowEnabled(hwnd):
            raise ControlError('A preparation control is disabled or changed windows.')
        if not self.user.PostMessageW(hwnd,msg,wparam,lparam):
            raise ControlError('Could not deliver a preparation request to LaserCAD.')

    def _one(self,window,cid,kind,labels=None):
        items=[c for c in self.controls(window) if c['control_id']==cid and kind.casefold() in c['kind'].casefold() and c['enabled']]
        if labels:items=[c for c in items if normalize(c['text']) in {normalize(v) for v in labels}]
        if len(items)!=1:raise ControlError(f'LaserCAD control {cid} is missing, ambiguous or incompatible. No further automatic clicks were sent.')
        return items[0]

    def _click(self,window,cid,labels):
        control=self._one(window,cid,'button',labels)
        # Modal commands must be posted: synchronous BM_CLICK would remain inside
        # DialogBox until an operator closed the newly opened dialog.
        self._post(control['hwnd'],0x00F5)

    def _dialogs(self):
        result=[];seen=set();inventory=[]
        @self.callback
        def visit(hwnd,_):
            hwnd=int(hwnd)
            if hwnd in seen:return True
            seen.add(hwnd)
            if hwnd==self.window['hwnd'] or not self._belongs(hwnd) or not self.user.IsWindowVisible(hwnd):return True
            kind=ctypes.create_unicode_buffer(256);self.user.GetClassNameW(hwnd,kind,len(kind))
            title=self.text(hwnd)
            known=normalize(title).rstrip('.\u2026') in {
                'systemsettings','systemoptions','layerparameters','downloaddocument','dealing'}
            # Permanent docked panels also use the Windows dialog class. A
            # child #32770 is not automatically a modal dialog. Known parameter,
            # Download and progress dialogs remain discoverable when embedded.
            child=bool(self.user.GetWindowLongW(hwnd,-16)&0x40000000) if kind.value=='#32770' else False
            panel=False
            if normalize(title)=='controlpanel':
                ids={c['control_id'] for c in self.controls({'hwnd':hwnd})}
                panel={1241,1674,1063,1522,1523}.issubset(ids)
            if not panel and (known or (kind.value=='#32770' and not child)):
                result.append({'hwnd':hwnd,'pid':self.window['pid'],'title':title})
            elif title and len(inventory)<20 and not any(k in kind.value.casefold() for k in ('button','static','edit','combobox','listview')):
                inventory.append({'title':title[:160],'class':kind.value})
            return True
        self.user.EnumWindows(visit,0)
        # Reparented dialogs can be child windows and are absent from EnumWindows.
        roots={self.window['hwnd']}
        if is_hosted_window(self.window['hwnd']):
            root=self.user.GetAncestor(self.window['hwnd'],2)
            if root:roots.add(root)
        for root in roots:self.user.EnumChildWindows(root,visit,0)
        self.dialog_inventory=inventory
        return result

    def _wait(self,predicate,message,timeout=8):
        until=monotonic()+timeout
        while True:
            self._identity()
            value=predicate()
            if value:return value
            if monotonic()>=until:raise ControlError(message)
            sleep(.1)

    def _dialog(self,required):
        def find():
            matches=[]
            for window in self._dialogs():
                ids={c['control_id'] for c in self.controls(window)}
                if required.issubset(ids):matches.append(window)
            if len(matches)>1:raise ControlError('More than one matching LaserCAD dialog is open.')
            return matches[0] if matches else None
        try:
            return self._wait(find,'The expected LaserCAD dialog did not open. Review the current dialog manually.')
        except ControlError as exc:
            observed=[]
            for window in self._dialogs():
                ids={c['control_id'] for c in self.controls(window)}
                observed.append(str(window.get('title','')) + ' (missing controls: ' +
                                ', '.join(map(str,sorted(required-ids))) + ')')
            raise ControlError(str(exc) + ' Required controls: ' + ', '.join(map(str,sorted(required))) +
                               '. Observed: ' + ('; '.join(observed) or 'no owned visible dialogs') +
                               '. Other owned windows: ' + str(getattr(self,'dialog_inventory',[]))) from exc

    def _main(self):
        self._identity()
        dialogs=self._dialogs()
        ready=self.ready(self.window)
        if dialogs or not ready:
            names='; '.join(str(d.get('title') or '(untitled)') for d in dialogs) or 'none recognized'
            raise ControlError('LaserCAD is not ready for automatic preparation. Open dialogs: '+names+
                               '. Main window ready: '+str(ready)+'. Other owned windows: '+
                               str(getattr(self,'dialog_inventory',[]))+'. Review LaserCAD before exporting a new job.')
        return self.window

    def _closed(self,window):
        self._wait(lambda:not self.user.IsWindow(window['hwnd']),'LaserCAD did not close the confirmed dialog.')

    def _combo_values(self,control):
        count=self._send(control['hwnd'],0x0146)
        if not 0<=count<=128:raise ControlError('LaserCAD returned an unsupported dropdown list.')
        values=[]
        for index in range(count):
            length=self._send(control['hwnd'],0x0149,index)
            if not 0<=length<2048:raise ControlError('LaserCAD returned an unsupported dropdown entry.')
            value=ctypes.create_unicode_buffer(length+1)
            self._send(control['hwnd'],0x0148,index,ctypes.addressof(value));values.append(value.value)
        return values

    def _select(self,window,control,value):
        values=self._combo_values(control)
        matches=[i for i,text in enumerate(values) if text.strip()==value]
        if len(matches)!=1:raise ControlError('LaserCAD dropdown option is missing or ambiguous: '+value)
        index=matches[0]
        if self._send(control['hwnd'],0x0147)==index:return
        if self._send(control['hwnd'],0x014E,index)!=index:raise ControlError('LaserCAD did not retain the dropdown selection.')
        self._post(control['parent'],0x0111,control['control_id']|(1<<16),control['hwnd'])
        self._wait(lambda:self.text(control['hwnd']).strip()==value,'LaserCAD did not apply the dropdown change.')

    def _read_accessible_row(self,control,index):
        # Virtual list views do not support LVM_GETITEMTEXT. Their MSAA row
        # description exposes the displayed subitems without private data offsets.
        oleacc=ctypes.OleDLL('oleacc');aut=ctypes.OleDLL('oleaut32')
        iid=_GUID(0x618736E0,0x3C3D,0x11CF,(ctypes.c_ubyte*8)(0x81,0x0C,0x00,0xAA,0x00,0x38,0x9B,0x71))
        pointer=ctypes.c_void_p()
        oleacc.AccessibleObjectFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.POINTER(_GUID),ctypes.POINTER(ctypes.c_void_p)]
        if oleacc.AccessibleObjectFromWindow(control['hwnd'],0xFFFFFFFC,ctypes.byref(iid),ctypes.byref(pointer))!=0 or not pointer.value:
            raise ControlError('LaserCAD did not expose readable layer/device rows.')
        table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        child=_Variant();child.vt=3;child.integer=index+1
        aut.SysStringLen.argtypes=[ctypes.c_void_p];aut.SysStringLen.restype=wintypes.UINT
        aut.SysFreeString.argtypes=[ctypes.c_void_p]
        aut.SysFreeString.restype=None
        values=[]
        try:
            for slot in [10,12]:
                text=ctypes.c_void_p()
                fn=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,_Variant,ctypes.POINTER(ctypes.c_void_p))(table[slot])
                hr=fn(pointer,child,ctypes.byref(text))
                if hr<0:raise ControlError('LaserCAD row text could not be verified.')
                try:values.append(ctypes.wstring_at(text,aut.SysStringLen(text)) if text.value else '')
                finally:
                    if text.value:aut.SysFreeString(text)
        finally:ctypes.WINFUNCTYPE(ctypes.c_ulong,ctypes.c_void_p)(table[2])(pointer)
        return ' '.join(values)

    def _write_remote(self,data):
        count=ctypes.c_size_t();value=ctypes.create_string_buffer(data)
        if not self.kernel.WriteProcessMemory(self.process,self.remote,value,len(data),ctypes.byref(count)) or count.value!=len(data):
            raise ControlError('Could not prepare the native row-inspection buffer.')

    def _read_remote(self,length):
        value=ctypes.create_string_buffer(length);count=ctypes.c_size_t()
        if not self.kernel.ReadProcessMemory(self.process,self.remote,value,length,ctypes.byref(count)) or count.value!=length:
            raise ControlError('Could not read the native row-inspection buffer.')
        return value.raw

    def _row_click(self,control,index,double=False,subitem=1):
        self._write_remote(struct.pack('<4i',0,subitem,0,0))  # LVIR_BOUNDS, requested subitem
        if not self._send(control['hwnd'],0x1038,index,self.remote):raise ControlError('Could not locate the selected LaserCAD row.')
        left,top,right,bottom=struct.unpack('<4i',self._read_remote(16))
        if subitem==0:
            # Subitem zero's bounds may span the full row. Restrict the click
            # to column zero, the red colour cell demonstrated by the operator.
            width=self._send(control['hwnd'],0x101D,0)  # LVM_GETCOLUMNWIDTH
            if not 0<width<32768:raise ControlError('The red layer colour column is not readable.')
            right=min(right,left+width)
        x,y=(left+right)//2,(top+bottom)//2
        if not 0<=x<32768 or not 0<=y<32768:raise ControlError('The LaserCAD row is outside its visible control.')
        point=x|(y<<16)
        for msg,flags in [(0x0201,1),(0x0202,0)]+([(0x0203,1),(0x0202,0)] if double else []):
            self._post(control['hwnd'],msg,flags,point)

    def _ip_combo(self):return self._one(self.window,1674,'combobox')

    def _ip_selected(self,target):
        text=self.text(self._ip_combo()['hwnd'])
        return bool(re.search(r'(?<![\d.])'+re.escape(target)+r'(?![\d.])',text))

    def select_communication(self,target):
        if named_usb_entry(target) or re.fullmatch(r'COM[1-9][0-9]*',target,re.I):return self.select_usb(target)
        self._main()
        combo=self._ip_combo();values=self._combo_values(combo)
        matches=[v for v in values if re.search(r'(?<![\d.])'+re.escape(target)+r'(?![\d.])',v)]
        if len(matches)>1:raise ControlError('More than one LaserCAD communication entry uses '+target)
        if matches:
            self._select(self.window,combo,matches[0])
        else:
            self._click(self.window,1241,['Select Mode'])
            dialog=self._dialog({1911,1514,1828})
            network=self._one(dialog,1911,'button',['Network Mode'])
            if self._send(network['hwnd'],0x00F0)!=1:
                self._click(dialog,1911,['Network Mode'])
                self._wait(lambda:self._send(network['hwnd'],0x00F0)==1,'Network Mode was not selected.')
            listing=self._one(dialog,1514,'syslistview32')
            def rows():
                count=self._send(listing['hwnd'],0x1004)
                if not 0<=count<=64:raise ControlError('Unsupported number of LaserCAD network devices.')
                return [i for i in range(count) if re.search(r'(?<![\d.])'+re.escape(target)+r'(?![\d.])',self._read_accessible_row(listing,i))]
            found=rows()
            if not found:
                self._click(dialog,1828,['Add'])
                device=self._dialog({1908,1516,1,2})
                name=self._one(device,1908,'edit');self.set_text(device,name,target)
                ip=self._one(device,1516,'sysipaddress32')
                self._send(ip['hwnd'],0x0465,0,int(ipaddress.IPv4Address(target)))
                self._write_remote(bytes(4))
                count=self._send(ip['hwnd'],0x0466,0,self.remote)
                if count!=4 or struct.unpack('<I',self._read_remote(4))[0]!=int(ipaddress.IPv4Address(target)):
                    raise ControlError('The new device IP address could not be verified.')
                self._click(device,1,['OK']);self._closed(device);found=rows()
            if len(found)!=1:raise ControlError('The target LaserCAD network device is missing or ambiguous.')
            self._row_click(listing,found[0])
            self._wait(lambda:bool(self._send(listing['hwnd'],0x102C,found[0],2)&2),'The target network row was not selected.')
            self._post(dialog['hwnd'],0x0010);self._closed(dialog)
            combo=self._ip_combo();values=self._combo_values(combo)
            matches=[v for v in values if re.search(r'(?<![\d.])'+re.escape(target)+r'(?![\d.])',v)]
            if len(matches)!=1:raise ControlError('The configured IP did not appear in LaserCAD communication choices.')
            self._select(self.window,combo,matches[0])
        self._wait(lambda:self._ip_selected(target),'LaserCAD communication selection did not retain '+target)
        self._main()

    def _speed_units(self,check_only=False):
        # Check the actual loaded preference; never open/change System settings.
        self._main()
        self._identity()
        from .lasercad_speed_units import read_speed_unit
        unit=read_speed_unit(self.kernel,self.process,self.window['pid'],self.window['executable'])
        self._identity()
        if unit!=1:
            raise ControlError('LaserCAD Speed Unit is m/min. Set it to mm/s once manually before automatic download. No settings dialog was opened and no download was requested.')

    def verify_speed_units(self):self._speed_units()

    def set_speed_units(self):
        # Compatibility name: verifies only; no settings changes.
        self.verify_speed_units()

    def _require_single_cut_layer(self):
        # Output is a custom subitem checkbox, not control 1848 (Z enable).
        # Preserve its existing selection; never infer it from a row state image.
        self._main()
        listing=self._one(self.window,1063,'syslistview32')
        if self._send(listing['hwnd'],0x1004)!=1:
            raise ControlError('Automatic setup requires exactly one cutting layer.')
        return listing

    def _layer_dialog(self):
        self._require_single_cut_layer()
        listing=self._one(self.window,1063,'syslistview32')
        if self._send(listing['hwnd'],0x1004)!=1:
            raise ControlError('Automatic setup requires exactly one imported cutting layer. Multiple/reference layers need manual review.')
        self._row_click(listing,0,double=True,subitem=0)
        return self._dialog({1195,1063,1,2})

    def _fields(self,dialog):
        return (self._one(dialog,1195,'combobox'),self._one(dialog,1790,'edit'),
                self._one(dialog,1832,'edit'),self._one(dialog,1836,'edit'))

    def configure_cut(self,speed,power):
        from .cut_recipe import cut_recipe
        speed,power=cut_recipe({'speed_mm_s':speed,'power_percent':power})
        dialog=self._layer_dialog()
        mode=self._one(dialog,1195,'combobox');self._select(dialog,mode,'Cut')
        self._wait(lambda:any(c['control_id']==1790 and 'edit' in c['kind'].casefold() for c in self.controls(dialog)),'The Cut parameter page did not appear.')
        mode,speed_field,max_field,min_field=self._fields(dialog)
        # The main table shows the primary channel maximum. Preserve a lower
        # minimum, but lower it first when it exceeds the new maximum.
        try:minimum=float(self.text(min_field['hwnd']))
        except ValueError:raise ControlError('LaserCAD minimum power is unreadable.')
        if not 0<=minimum<=100:raise ControlError('LaserCAD minimum power is invalid.')
        if minimum>power:self.set_text(dialog,min_field,format(power,'.12g'))
        self.set_text(dialog,speed_field,format(speed,'.12g'));self.set_text(dialog,max_field,format(power,'.12g'))
        self._verify_fields(dialog,speed,power)
        self._click(dialog,1,['OK']);self._closed(dialog);self._main()

    def _verify_fields(self,dialog,speed,power):
        mode,speed_field,max_field,min_field=self._fields(dialog)
        try:valid=self.text(mode['hwnd']).strip()=='Cut' and float(self.text(speed_field['hwnd']))==speed and float(self.text(max_field['hwnd']))==power and 0<=float(self.text(min_field['hwnd']))<=power
        except ValueError:valid=False
        if not valid:raise ControlError(f'LaserCAD did not retain Cut / {speed:g} mm/s / {power:g}%. Download was not requested.')

    def verify(self,target,speed,power):
        if not self._communication_selected(target):raise ControlError('LaserCAD communication changed before download.')
        self._speed_units(check_only=True)
        dialog=self._layer_dialog();self._verify_fields(dialog,speed,power)
        self._click(dialog,1,['OK']);self._closed(dialog);self._require_single_cut_layer()
        if not self._communication_selected(target):raise ControlError('LaserCAD communication changed during parameter confirmation.')

    def open_download(self):
        self._main();self._click(self.window,1522,['Download'])
        self.download=self._dialog({1384,1015,1504,1779})
        # Prevent a partial selection or a broadcast to multiple devices.
        for cid in [1779,1899]:
            control=self._download_option(cid)
            if self._send(control['hwnd'],0x00F0)!=0:
                raise ControlError('Download Selected Objects or Download To Selected Devices is enabled. Review the download dialog manually.')
        if not self.text(self._one(self.download,1015,'edit')['hwnd']).strip():
            raise ControlError('LaserCAD download document name is empty.')

    def _download_option(self,cid):
        # The multi-device checkbox may be hidden in USB mode. Inspect its real
        # control state rather than requiring it to be visible or assuming OFF.
        hwnd=self.user.GetDlgItem(self.download['hwnd'],cid)
        if not hwnd or not self.user.IsChild(self.download['hwnd'],hwnd):
            raise ControlError('Download option '+str(cid)+' is missing. No document was submitted.')
        kind=ctypes.create_unicode_buffer(256);self.user.GetClassNameW(hwnd,kind,len(kind))
        if self.user.GetDlgCtrlID(hwnd)!=cid or 'button' not in kind.value.casefold():
            raise ControlError('Download option '+str(cid)+' changed type. No document was submitted.')
        return {'hwnd':hwnd}

    def download_document(self):
        dialogs=self._dialogs()
        if len(dialogs)!=1 or dialogs[0]['hwnd']!=self.download['hwnd']:
            raise ControlError('The Download Document dialog changed before transfer.')
        self.transfer_baseline={dialog['hwnd'] for dialog in dialogs}
        self._click(self.download,1384,['Download Document'])

    def _usb_selected(self,port):
        text=self.text(self._ip_combo()['hwnd']).strip()
        expected=getattr(self,'selected_usb_entry',None)
        return usb_entry_matches(text,port) and (expected is None or text==expected)

    def _communication_selected(self,target):
        return self._usb_selected(target) if named_usb_entry(target) or re.fullmatch(r'COM[1-9][0-9]*',target,re.I) else self._ip_selected(target)

    def select_usb(self,port,*,allow_mode_dialog=True):
        self._main()
        combo=self._ip_combo()
        choices=[entry for entry in self._combo_values(combo) if usb_entry_matches(entry,port)]
        if len(choices)>1:raise ControlError('More than one USB communication entry uses '+port)
        if choices:
            self._select(self.window,combo,choices[0]);self.selected_usb_entry=choices[0]
            self._wait(lambda:self._usb_selected(port),'LaserCAD did not retain the USB dropdown selection.')
            self._main();return
        if not allow_mode_dialog:
            raise ControlError("The configured USB device is not available in LaserCAD's communication dropdown. Configure it in Select Mode first; no Datum was sent.")
        self._click(self.window,1241,['Select Mode'])
        dialog=self._dialog({1610,1513,1034})
        usb=self._one(dialog,1610,'button',['USB Mode'])
        if self._send(usb['hwnd'],0x00F0)!=1:
            self._click(dialog,1610,['USB Mode'])
            self._wait(lambda:self._send(usb['hwnd'],0x00F0)==1,'LaserCAD did not select USB Mode.')
        listing=self._one(dialog,1513,'syslistview32')
        count=self._send(listing['hwnd'],0x1004)
        if not 0<=count<=64:raise ControlError('Unsupported number of configured LaserCAD USB ports.')
        found=[i for i in range(count) if usb_entry_matches(self._read_accessible_row(listing,i),port)]
        if len(found)!=1:
            raise ControlError('Configure exactly one '+port+' USB entry in LaserCAD Select Mode, or change the USB port in Settings. No other port was selected.')
        self._row_click(listing,found[0])
        self._wait(lambda:bool(self._send(listing['hwnd'],0x102C,found[0],2)&2),'The requested USB device row was not selected.')
        self._post(dialog['hwnd'],0x0010);self._closed(dialog)
        combo=self._ip_combo()
        matches=[entry for entry in self._combo_values(combo) if usb_entry_matches(entry,port)]
        if len(matches)!=1:raise ControlError('The requested USB entry is missing or ambiguous in LaserCAD\'s communication dropdown.')
        self._select(self.window,combo,matches[0]);self.selected_usb_entry=matches[0]
        self._wait(lambda:self._usb_selected(port),'LaserCAD did not retain USB '+port+' in its communication dropdown.')
        self._main()

    def _owned_by_download(self,hwnd):
        seen=set();pending=[hwnd]
        while pending and len(seen)<64:
            current=pending.pop()
            if not current or current in seen:continue
            if current==self.download['hwnd']:return True
            seen.add(current)
            pending.extend([self.user.GetParent(current),self.user.GetWindow(current,4)])
        return False

    def wait_transfer_result(self,timeout):
        from .lasercad_transfer_result import wait_for_transfer
        def observe():
            self._identity()
            result=[]
            for dialog in self._dialogs():
                if dialog['hwnd'] in self.transfer_baseline:continue
                if not self._owned_by_download(dialog['hwnd']):continue
                texts=[c['text'] for c in self.controls(dialog) if c['text'].strip() and 'button' not in c['kind'].casefold()]
                if texts:result.append({'hwnd':dialog['hwnd'],'title':dialog.get('title',''),'owned_by_download':True,'texts':texts})
            return result
        # Success/error prompts remain visible. No OK, overwrite, retry or Start
        # command is sent while observing their result.
        return wait_for_transfer(observe,self.transfer_baseline,timeout)


    def acknowledge_success(self,transfer,target,guard):
        """Dismiss only the verified fresh success and its matching Download dialog."""
        from .lasercad_transfer_result import classify_message
        self._identity()
        if (transfer.get('status')!='completed' or transfer.get('confirmation_source')!='lasercad_download_dialog'
            or transfer.get('dialog_id') in self.transfer_baseline):
            raise ControlError('Run Box requires a fresh successful download confirmation.')
        dialogs=self._dialogs()
        matches=[d for d in dialogs if d['hwnd']==transfer.get('dialog_id') and self._owned_by_download(d['hwnd'])]
        if len(matches)!=1:
            raise ControlError('The successful transfer dialog changed. Run Box was not requested.')
        success=matches[0]
        texts=[c['text'] for c in self.controls(success) if c['text'].strip() and 'button' not in c['kind'].casefold()]
        if classify_message(texts)['status']!='completed':
            raise ControlError('The transfer message no longer confirms success. Run Box was not requested.')
        # Only the success prompt's exact OK button can be acknowledged.
        self._click(success,1,['OK']);self._closed(success)
        if self.user.IsWindow(self.download['hwnd']):
            dialogs=self._dialogs()
            if len(dialogs)!=1 or dialogs[0]['hwnd']!=self.download['hwnd']:
                raise ControlError('Unexpected dialog after successful transfer. Run Box was not requested.')
            ids={c['control_id'] for c in self.controls(self.download)}
            if not {1384,1015,1504,1779}.issubset(ids):
                raise ControlError('The Download window changed. Run Box was not requested.')
            self._post(self.download['hwnd'],0x0010);self._closed(self.download)
        self._wait(lambda:self.ready(self.window),'LaserCAD did not return to the drawing after download.')
        self._main()
        if not self._communication_selected(target):
            raise ControlError('LaserCAD communication changed after download. Run Box was not requested.')
        guard()
        return {'acknowledged':True,'motion_requested':False}

    def run_box_after_success(self,transfer,target,guard):
        self.acknowledge_success(transfer,target,guard)
        button=self._one(self.window,1100,'button',['Run Box'])
        self._main();self.click(self.window,button)
        return {'status':'requested','requested':True,'action':'frame',
                'physical_motion_verified':False,
                'message':'Run Box click delivered once after confirmed transfer. Boundary motion/completion is unverified. Start remains manual.'}
