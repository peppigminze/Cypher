"""
Screen brightness.

    laptops / internal displays   WMI WmiMonitorBrightnessMethods (PowerShell)
    external monitors             DDC/CI via the Windows Monitor Configuration
                                  API (dxva2.dll, ctypes) - only if the monitor
                                  supports DDC/CI (often a setting in its menu)
"""
from __future__ import annotations

import ctypes
import functools
import subprocess
import sys
from typing import List, Optional, Tuple

from app.pc.powershell import CREATE_NO_WINDOW, powershell_args


@functools.lru_cache(maxsize=1)
def _api():
    from ctypes import wintypes  # noqa: PLC0415

    class PHYSICAL_MONITOR(ctypes.Structure):
        _fields_ = [("hPhysicalMonitor", wintypes.HANDLE), ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32")
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), enum_proc, wintypes.LPARAM]
    dxva2 = ctypes.WinDLL("dxva2")
    # 64-bit handles: without argtypes ctypes would cut them to 32 bit.
    dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, ctypes.POINTER(wintypes.DWORD)]
    dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
    dxva2.GetMonitorBrightness.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.DWORD)] * 3
    dxva2.SetMonitorBrightness.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dxva2.DestroyPhysicalMonitors.argtypes = [wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
    return user32, dxva2, PHYSICAL_MONITOR, enum_proc


def _ddc_monitors():
    """(dxva2, [(array, count)]) for every physical monitor; caller destroys them."""
    from ctypes import wintypes  # noqa: PLC0415

    user32, dxva2, PHYSICAL_MONITOR, enum_proc = _api()
    monitors: List[int] = []

    def _collect(hmonitor, _hdc, _rect, _lparam):
        monitors.append(hmonitor)
        return True

    callback = enum_proc(_collect)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    handles = []
    for hmonitor in monitors:
        count = wintypes.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)) or not count.value:
            continue
        array = (PHYSICAL_MONITOR * count.value)()
        if dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, array):
            handles.append((array, count.value))
    return dxva2, handles


def _ddc(level: Optional[int]) -> List[int]:
    """Set (level given) or read brightness on all DDC/CI monitors -> levels in %."""
    from ctypes import wintypes  # noqa: PLC0415

    dxva2, handles = _ddc_monitors()
    results = []
    try:
        for array, count in handles:
            for i in range(count):
                handle = array[i].hPhysicalMonitor
                low, cur, high = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                if not dxva2.GetMonitorBrightness(handle, ctypes.byref(low), ctypes.byref(cur), ctypes.byref(high)):
                    continue
                span = max(1, high.value - low.value)
                if level is not None:
                    raw = low.value + round(span * level / 100)
                    if dxva2.SetMonitorBrightness(handle, wintypes.DWORD(raw)):
                        results.append(level)
                else:
                    results.append(round((cur.value - low.value) * 100 / span))
    finally:
        for array, count in handles:
            dxva2.DestroyPhysicalMonitors(count, array)
    return results


def _wmi(level: Optional[int]) -> Tuple[bool, str]:
    script = ("$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | Select-Object -First 1; "
              f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness=[byte]{level}}} | Out-Null; 'ok'"
              if level is not None else
              "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness")
    proc = subprocess.run(powershell_args(script), capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    return proc.returncode == 0, proc.stdout.decode("utf-8", "replace").strip()


def get_brightness() -> Optional[int]:
    if sys.platform != "win32":
        return None
    ok, out = _wmi(None)
    if ok and out.isdigit():
        return int(out)
    levels = _ddc(None)
    return levels[0] if levels else None


def set_brightness(level: int) -> str:
    """Returns which method worked ("intern" | "DDC/CI"); raises OSError if none."""
    ok, _out = _wmi(level)
    if ok:
        return "intern"
    if _ddc(level):
        return "DDC/CI"
    raise OSError("Die Helligkeit lässt sich hier nicht per Windows einstellen: kein Laptop-Display und kein Monitor mit DDC/CI "
                  "(DDC/CI lässt sich oft im Monitor-Menü einschalten). Bitte am Monitor selbst einstellen.")
