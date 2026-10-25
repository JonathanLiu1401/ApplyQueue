#!/usr/bin/env node
// Fails if any npm dependency (prod or dev) in package-lock.json has a license outside the
// allowlist. Python sidecar deps are checked by scripts/license-check-python.py.
// ApplyQueue is MIT; we only accept permissive or file-level copyleft licenses.
// Reads package-lock.json (lockfileVersion 3 records each package's license), so it needs no
// install and sees exactly what CI and releases will install.
import { readFileSync } from "node:fs";

export const ALLOWED = new Set([
  "MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "BlueOak-1.0.0",
  "CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "Python-2.0", "Unlicense", "WTFPL", "MPL-2.0", "Zlib",
  "BSD", "MIT-0",
]);

/** Minimal SPDX evaluation: "A OR B" passes if any side passes, "A AND B" needs all. */
function allowed(expr) {
  const e = String(expr).trim().replace(/^\((.*)\)$/, "$1");
  if (/ OR /i.test(e)) return e.split(/ OR /i).some(allowed);
  if (/ AND /i.test(e)) return e.split(/ AND /i).every(allowed);
  return ALLOWED.has(e.replace(/[()]/g, "").trim());
}

const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
const bad = [];
const seen = new Map();
for (const [path, meta] of Object.entries(lock.packages ?? {})) {
  if (!path || meta.link) continue; // root project / workspace links
  const name = meta.name ?? path.split("node_modules/").pop();
  const license = meta.license ?? "UNKNOWN";
  seen.set(license, (seen.get(license) ?? 0) + 1);
  if (!allowed(license)) bad.push(`${name}@${meta.version}${meta.dev ? " (dev)" : ""}: ${license}`);
}
const total = [...seen.values()].reduce((a, b) => a + b, 0);
if (bad.length) {
  console.error(`npm license check FAILED (${bad.length} of ${total} packages):\n  ${bad.join("\n  ")}`);
  console.error("If a license is acceptable, add it to ALLOWED in scripts/license-check.mjs in a reviewed PR.");
  process.exit(1);
}
console.log(`npm license check passed: ${total} packages, licenses: ${[...seen.keys()].sort().join(", ")}`);
