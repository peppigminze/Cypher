"""
Windows known folders (Desktop, Dokumente, Downloads ...) by their real
location - the Desktop is often redirected to OneDrive, and the model says
"Dokumente" while the folder on disk is called "Documents".
"""
from __future__ import annotations

import ctypes
import functools
import sys
import uuid
from typing import Optional

# FOLDERID_* from KnownFolders.h
_IDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
NAMES = {
    "desktop": "desktop", "schreibtisch": "desktop",
    "documents": "documents", "dokumente": "documents", "eigene dateien": "documents",
    "downloads": "downloads",
    "pictures": "pictures", "bilder": "pictures",
    "music": "music", "musik": "music",
    "videos": "videos",
}


@functools.lru_cache(maxsize=None)
def known_folder(key: str) -> Optional[str]:
    if sys.platform != "win32" or key not in _IDS:
        return None
    from ctypes import wintypes  # noqa: PLC0415

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID(_IDS[key]).bytes_le)
    out = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return out.value
    finally:
        ctypes.WinDLL("ole32").CoTaskMemFree(out)


def map_first_part(relative: str) -> Optional[str]:
    """'Dokumente/Bericht.docx' -> '<real Documents folder>/Bericht.docx'; None if not a known folder."""
    parts = relative.replace("/", "\\").split("\\", 1)
    key = NAMES.get(parts[0].strip().lower())
    base = known_folder(key) if key else None
    if not base:
        return None
    return base if len(parts) == 1 or not parts[1] else base + "\\" + parts[1]
