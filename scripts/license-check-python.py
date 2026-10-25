#!/usr/bin/env python3
"""Fails if any installed Python dependency of the sidecar has a license outside the allowlist.

Run inside the project environment so it sees exactly what PyInstaller bundles:
    uv run python scripts/license-check-python.py

The sidecar binary bundles every package, so copyleft that reaches the binary (GPL/AGPL)
would bind the whole distributed app. Known exceptions must be listed in EXCEPTIONS with a
reason and a tracking milestone; they print as warnings instead of failing.
"""
from __future__ import annotations

import re
import sys
from importlib import metadata

ALLOWED = {
    "MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "CC0-1.0", "Python-2.0",
    "PSF-2.0", "Unlicense", "MPL-2.0", "Zlib", "BSD", "MIT-0", "HPND", "LGPL-2.1-or-later",
    "LGPL-3.0-or-later", "LGPL-2.1-only", "LGPL-3.0-only", "LGPL", "MIT-CMU", "CNRI-Python",
}
# name -> reason. Keep this short; every entry needs a plan.
EXCEPTIONS = {
    "pymupdf": "AGPL-3.0 (or Artifex commercial). Used by LangHire for resume parsing; "
               "must be replaced (e.g. pypdf, BSD) before any public binary release (PLAN.md M1).",
    "pymupdf4llm": "No license metadata; Artifex package pulled in by pymupdf 2.x (AGPL/commercial family). "
                   "Goes away with the pymupdf replacement (PLAN.md M1).",
    "pymupdf-layout": "No license metadata; Artifex package pulled in by pymupdf 2.x. Goes away with the "
                      "pymupdf replacement (PLAN.md M1).",
    "pymupdf_layout": "Same as pymupdf-layout.",
    "pyinstaller": "GPL-2.0 with bootloader exception: build tool only; the exception allows "
                   "distributing bundled apps under any license.",
    "pyinstaller-hooks-contrib": "GPL-2.0 / Apache-2.0, build-time hooks only.",
}
CLASSIFIER_MAP = {
    "MIT License": "MIT", "BSD License": "BSD", "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "The Unlicense (Unlicense)": "Unlicense",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Library or Lesser General Public License (LGPL)": "LGPL",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND",
    "zlib/libpng License": "Zlib",
    "GNU Affero General Public License v3": "AGPL-3.0", "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0",
}
ALIASES = {
    "mit license": "MIT", "mit": "MIT", "bsd": "BSD", "bsd license": "BSD", "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0", "apache software license": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0", "bsd-3-clause": "BSD-3-Clause", "bsd 3-clause": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause", "new bsd license": "BSD-3-Clause", "bsd-2-clause": "BSD-2-Clause",
    "isc": "ISC", "isc license": "ISC", "psf": "PSF-2.0", "psf-2.0": "PSF-2.0", "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0", "unlicense": "Unlicense", "0bsd": "0BSD", "python-2.0": "Python-2.0",
}


def spdx_ok(expr: str) -> bool:
    e = expr.strip().strip("()")
    if re.search(r"\s+OR\s+", e):
        return any(spdx_ok(p) for p in re.split(r"\s+OR\s+", e))
    if re.search(r"\s+AND\s+", e):
        return all(spdx_ok(p) for p in re.split(r"\s+AND\s+", e))
    e = e.strip("() ")
    return e in ALLOWED or ALIASES.get(e.lower()) in ALLOWED


def licenses_of(dist: metadata.Distribution) -> list[str]:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return [expr]
    out = []
    for c in md.get_all("Classifier") or []:
        if c.startswith("License ::"):
            leaf = c.split("::")[-1].strip()
            if leaf != "OSI Approved":
                out.append(CLASSIFIER_MAP.get(leaf, leaf))
    lic = (md.get("License") or "").strip()
    if not out and lic and len(lic) < 80 and "\n" not in lic:
        out.append(ALIASES.get(lic.lower(), lic))
    elif not out and lic:
        # Full license text pasted into the License field: recognise the common headers.
        head = lic.splitlines()[0].strip().lower()
        if head in ALIASES:
            out.append(ALIASES[head])
        elif "permission is hereby granted, free of charge" in lic.lower():
            out.append("MIT")
    return out or ["UNKNOWN"]


def main() -> int:
    bad, warn, seen = [], [], {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").lower()
        if not name or name in seen:
            continue
        lics = licenses_of(dist)
        seen[name] = lics
        if name in EXCEPTIONS:
            warn.append(f"{name}=={dist.version}: {' | '.join(lics)} -> {EXCEPTIONS[name]}")
        elif not any(spdx_ok(l) for l in lics):
            bad.append(f"{name}=={dist.version}: {' | '.join(lics)}")
    for w in warn:
        print(f"WARNING (allowed exception) {w}")
    if bad:
        print(f"Python license check FAILED ({len(bad)} of {len(seen)} packages):\n  " + "\n  ".join(sorted(bad)))
        print("If a license is acceptable, add it to ALLOWED/ALIASES or EXCEPTIONS in a reviewed change.")
        return 1
    print(f"Python license check passed: {len(seen)} packages ({len(warn)} documented exceptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
