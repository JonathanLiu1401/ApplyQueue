"""Hardware probe: RAM, GPU VRAM, Apple silicon. Stdlib only; every probe is best-effort."""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess

GB = 1024 ** 3


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def total_ram_bytes() -> int:
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return int(st.ullTotalPhys)
        if platform.system() == "Darwin":
            v = _run(["sysctl", "-n", "hw.memsize"]).strip()
            return int(v) if v.isdigit() else 0
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        return 0


def nvidia_gpus() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].replace(".", "").isdigit():
            gpus.append({"vendor": "nvidia", "name": parts[0], "vram_bytes": int(float(parts[1]) * 1024 * 1024)})
    return gpus


def amd_gpus() -> list[dict]:
    if not shutil.which("rocm-smi"):
        return []
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--csv"])
    gpus = []
    for line in out.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            gpus.append({"vendor": "amd", "name": parts[0], "vram_bytes": int(parts[1])})
    return gpus


def probe() -> dict:
    system = platform.system()
    machine = platform.machine().lower()
    apple_silicon = system == "Darwin" and machine in ("arm64", "aarch64")
    gpus = [] if system == "Darwin" else nvidia_gpus() + amd_gpus()
    return {
        "os": system, "arch": machine, "apple_silicon": apple_silicon,
        "intel_mac": system == "Darwin" and machine in ("x86_64", "amd64"),
        "ram_bytes": total_ram_bytes(), "gpus": gpus,
    }


def memory_budget(hw: dict) -> dict:
    """How much memory a model may use, and how fast it will run.

    fast = dedicated GPU, good = Apple silicon unified memory, slow = CPU only (incl. Intel Macs:
    Ollama and llama.cpp use the CPU there)."""
    ram = int(hw.get("ram_bytes") or 0)
    vram = max([int(g.get("vram_bytes") or 0) for g in hw.get("gpus") or []] or [0])
    if vram >= 4 * GB:
        return {"bytes": vram, "speed": "fast", "where": "GPU"}
    if hw.get("apple_silicon"):
        return {"bytes": int(ram * 0.7), "speed": "good", "where": "unified memory"}
    return {"bytes": int(ram * 0.5), "speed": "slow", "where": "CPU"}
