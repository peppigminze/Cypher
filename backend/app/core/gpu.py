"""
GPU memory monitoring on Windows - without extra dependencies.

Why this exists: Ollama's "100% GPU" only means all layers were *assigned*
to the GPU. Under Windows (WDDM), when dedicated VRAM runs out the driver
silently backs the overflow with shared system RAM; inference then
becomes 10-20x slower while Ollama still reports 100% GPU (measured in
M0, see docs/overhaul/AUDIT.md K1). The only reliable signal is the
"Shared Usage" performance counter of the inference process.

Implementation: the Windows PDH API (pdh.dll) via ctypes for the
"GPU Adapter Memory" / "GPU Process Memory" counters, and the registry
(HardwareInformation.qwMemorySize) for the real VRAM size - WMI's
AdapterRAM is a 32-bit field and caps at 4 GB.

Everything returns None on non-Windows systems or on any API failure;
callers treat that as "unknown", never as an error.
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field

import psutil

logger = logging.getLogger("cypher.gpu")

_GB = 1024**3
_INFERENCE_PROCESS_RE = re.compile(r"(llama|ollama)", re.IGNORECASE)
_PROC_INSTANCE_RE = re.compile(r"^pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+$", re.IGNORECASE)
_ADAPTER_INSTANCE_RE = re.compile(r"^(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+$", re.IGNORECASE)


@dataclass
class GpuAdapterInfo:
    name: str
    vram_total_gb: float | None


@dataclass
class GpuMemorySnapshot:
    adapters: list[GpuAdapterInfo]
    # Values for the busiest adapter (by dedicated usage) = the discrete GPU.
    dedicated_used_gb: float
    shared_used_gb: float
    inference_dedicated_gb: float = 0.0
    inference_shared_gb: float = 0.0
    inference_pids: list[int] = field(default_factory=list)

    @property
    def vram_total_gb(self) -> float | None:
        totals = [a.vram_total_gb for a in self.adapters if a.vram_total_gb]
        return max(totals) if totals else None

    @property
    def primary_adapter_name(self) -> str | None:
        with_vram = [a for a in self.adapters if a.vram_total_gb]
        if with_vram:
            return max(with_vram, key=lambda a: a.vram_total_gb or 0).name
        return self.adapters[0].name if self.adapters else None


def read_adapters() -> list[GpuAdapterInfo]:
    if sys.platform != "win32":
        return []
    import winreg  # noqa: PLC0415

    adapters: list[GpuAdapterInfo] = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = winreg.QueryValueEx(key, "DriverDesc")[0]
                        try:
                            raw = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                            vram = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
                            vram_gb: float | None = round(vram / _GB, 2) if vram else None
                        except OSError:
                            vram_gb = None
                        adapters.append(GpuAdapterInfo(name=str(name), vram_total_gb=vram_gb))
                except OSError:
                    continue
    except OSError as exc:
        logger.debug("Could not read display adapters from registry: %s", exc)
    return adapters


class _Pdh:
    """Minimal PDH wrapper: collect formatted values of wildcard counters."""

    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2

    def __init__(self) -> None:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.pdh = ctypes.WinDLL("pdh.dll")

        class _Value(ctypes.Union):
            _fields_ = [
                ("longValue", ctypes.c_long),
                ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong),
                ("AnsiStringValue", ctypes.c_char_p),
                ("WideStringValue", ctypes.c_wchar_p),
            ]

        class _FmtValue(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("value", _Value)]

        class _Item(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]

        self.Item = _Item

    def collect(self, paths: list[str]) -> dict[str, dict[str, float]]:
        ctypes, wintypes = self.ctypes, self.wintypes
        query = wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        try:
            counters = {}
            for path in paths:
                handle = wintypes.HANDLE()
                status = self.pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(handle))
                if status != 0:
                    raise OSError(f"PdhAddEnglishCounterW({path}) failed: {status:#x}")
                counters[path] = handle
            if self.pdh.PdhCollectQueryData(query) != 0:
                raise OSError("PdhCollectQueryData failed")
            result: dict[str, dict[str, float]] = {}
            for path, handle in counters.items():
                size = wintypes.DWORD(0)
                count = wintypes.DWORD(0)
                status = self.pdh.PdhGetFormattedCounterArrayW(handle, self.PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
                if status & 0xFFFFFFFF != self.PDH_MORE_DATA:
                    result[path] = {}
                    continue
                buf = ctypes.create_string_buffer(size.value)
                items = ctypes.cast(buf, ctypes.POINTER(self.Item))
                status = self.pdh.PdhGetFormattedCounterArrayW(handle, self.PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), items)
                if status != 0:
                    raise OSError(f"PdhGetFormattedCounterArrayW failed: {status:#x}")
                result[path] = {items[i].szName: items[i].FmtValue.value.doubleValue for i in range(count.value)}
            return result
        finally:
            self.pdh.PdhCloseQuery(query)


def _inference_pids() -> list[int]:
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info.get("name") or ""
        if _INFERENCE_PROCESS_RE.search(name) and "app" not in name.lower():
            pids.append(proc.info["pid"])
    return pids


def read_gpu_memory() -> GpuMemorySnapshot | None:
    """Blocking (a few ms) - call via asyncio.to_thread from async code."""
    if sys.platform != "win32":
        return None
    try:
        pdh = _Pdh()
        data = pdh.collect(
            [
                r"\GPU Adapter Memory(*)\Dedicated Usage",
                r"\GPU Adapter Memory(*)\Shared Usage",
                r"\GPU Process Memory(*)\Dedicated Usage",
                r"\GPU Process Memory(*)\Shared Usage",
            ]
        )
    except (OSError, AttributeError, ValueError) as exc:
        logger.debug("GPU counters unavailable: %s", exc)
        return None

    adapter_dedicated = data.get(r"\GPU Adapter Memory(*)\Dedicated Usage", {})
    adapter_shared = data.get(r"\GPU Adapter Memory(*)\Shared Usage", {})
    if not adapter_dedicated:
        return None

    # The discrete GPU is the adapter with by far the most dedicated memory in use
    # (the iGPU only has a small carve-out and lives in shared memory by design).
    primary_instance = max(adapter_dedicated, key=lambda k: adapter_dedicated[k])
    m = _ADAPTER_INSTANCE_RE.match(primary_instance)
    primary_luid = m.group(1).lower() if m else ""

    pids = set(_inference_pids())
    inf_ded = inf_shared = 0.0
    found: set[int] = set()
    for path, bucket in (
        (r"\GPU Process Memory(*)\Dedicated Usage", "ded"),
        (r"\GPU Process Memory(*)\Shared Usage", "shared"),
    ):
        for instance, value in data.get(path, {}).items():
            pm = _PROC_INSTANCE_RE.match(instance)
            if not pm:
                continue
            pid, luid = int(pm.group(1)), pm.group(2).lower()
            if pid in pids and luid == primary_luid:
                found.add(pid)
                if bucket == "ded":
                    inf_ded += value
                else:
                    inf_shared += value

    return GpuMemorySnapshot(
        adapters=read_adapters(),
        dedicated_used_gb=round(adapter_dedicated[primary_instance] / _GB, 2),
        shared_used_gb=round(adapter_shared.get(primary_instance, 0.0) / _GB, 2),
        inference_dedicated_gb=round(inf_ded / _GB, 2),
        inference_shared_gb=round(inf_shared / _GB, 2),
        inference_pids=sorted(found),
    )
