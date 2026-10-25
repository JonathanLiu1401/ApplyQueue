#!/usr/bin/env python3
"""Fail if any locked Python dependency lacks an installable macOS x86_64 artifact.

Intel Macs are a hard requirement for ApplyQueue. For every package in uv.lock
that is installed on macOS, there must be a wheel that runs on x86_64 macOS
(macosx_*_x86_64, *_intel, *_universal2, or py3-none-any) for CPython 3.13,
or the package must be pure Python (sdist-only packages are flagged so they are
reviewed, since building them in CI may need a compiler).

Usage: python scripts/check-macos-x64-wheels.py [uv.lock] [--python 3.13] [--macos 13.0]
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

WHEEL_RE = re.compile(r"^(?P<name>.+?)-(?P<ver>[^-]+)(-\d[^-]*)?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")


def _py_ok(py: str, abi: str, pyver: tuple[int, int]) -> bool:
    major, minor = pyver
    for tag in py.split("."):
        if tag in ("py3", f"py{major}{minor}", "py2.py3"):
            return True
        m = re.fullmatch(r"cp(\d)(\d+)", tag)
        if m:
            v = (int(m.group(1)), int(m.group(2)))
            abis = abi.split(".")
            if v == pyver and (f"cp{major}{minor}" in abis or "abi3" in abis or "none" in abis):
                return True
            if "abi3" in abis and v <= pyver:
                return True
    return False


def _plat_ok(plat: str, macos: tuple[int, int]) -> bool:
    for p in plat.split("."):
        if p == "any":
            return True
        m = re.fullmatch(r"macosx_(\d+)_(\d+)_(\w+)", p)
        if m:
            need = (int(m.group(1)), int(m.group(2)))
            arch = m.group(3)
            if arch in ("x86_64", "intel", "universal2", "universal", "fat64", "fat3") and need <= macos:
                return True
    return False


def wheel_runs_on_mac_x64(filename: str, pyver, macos) -> bool:
    m = WHEEL_RE.match(filename)
    if not m:
        return False
    if m.group("py").startswith("pp"):
        return False
    return _py_ok(m.group("py"), m.group("abi"), pyver) and _plat_ok(m.group("plat"), macos)


_NON_DARWIN = ("sys_platform == 'win32'", "platform_system == 'Windows'", "os_name == 'nt'",
               "sys_platform == 'linux'", "platform_system == 'Linux'", "sys_platform != 'darwin'")


def _non_darwin_only(lock: dict) -> set[str]:
    """Names whose every incoming dependency edge is gated to a non-macOS platform."""
    edges: dict[str, list[str]] = {}
    for pkg in lock.get("package", []):
        for group in [pkg.get("dependencies", [])] + list((pkg.get("optional-dependencies") or {}).values()) \
                + list((pkg.get("dev-dependencies") or {}).values()):
            for dep in group:
                edges.setdefault(dep["name"], []).append(dep.get("marker", ""))
    return {name for name, markers in edges.items()
            if markers and all(any(t in mk for t in _NON_DARWIN) for mk in markers)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lock", nargs="?", default="uv.lock")
    ap.add_argument("--python", default="3.13")
    ap.add_argument("--macos", default="13.0", help="oldest macOS version to support")
    args = ap.parse_args()
    pyver = tuple(int(x) for x in args.python.split("."))[:2]
    macos = tuple(int(x) for x in args.macos.split("."))[:2]

    lock = tomllib.loads(Path(args.lock).read_text())
    bad, sdist_only = [], []
    skip = _non_darwin_only(lock)
    for pkg in lock.get("package", []):
        src = pkg.get("source", {})
        if "registry" not in src or pkg["name"] in skip:
            continue
        wheels = [w.get("url", "").rsplit("/", 1)[-1] for w in pkg.get("wheels", [])]
        if not wheels:
            if pkg.get("sdist"):
                sdist_only.append(f"{pkg['name']}=={pkg['version']}")
            continue
        if not any(wheel_runs_on_mac_x64(w, pyver, macos) for w in wheels):
            bad.append(f"{pkg['name']}=={pkg['version']}")
    for s in sdist_only:
        print(f"note: sdist only (builds from source): {s}")
    if bad:
        print(f"FAIL: no macOS x86_64 wheel (CPython {args.python}, macOS >= {args.macos}) for:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"OK: every locked registry package has a macOS x86_64 artifact (CPython {args.python}, macOS {args.macos}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
