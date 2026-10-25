#!/usr/bin/env node
// Fails if any installed dependency (prod or dev) has a license outside the allowlist.
// ApplyQueue is MIT; we only accept permissive or file-level copyleft licenses.
import { execFileSync } from "node:child_process";

const ALLOWED = new Set([
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

const json = execFileSync("pnpm", ["licenses", "list", "--json"], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
const byLicense = JSON.parse(json);
const bad = [];
let total = 0;
for (const [license, pkgs] of Object.entries(byLicense)) {
  total += pkgs.length;
  if (!allowed(license)) for (const p of pkgs) bad.push(`${p.name}@${(p.versions ?? [p.version]).join(",")}: ${license}`);
}
if (bad.length) {
  console.error(`License check FAILED (${bad.length} of ${total} packages):\n  ${bad.join("\n  ")}`);
  console.error("If a license is acceptable, add it to ALLOWED in scripts/license-check.mjs in a reviewed PR.");
  process.exit(1);
}
console.log(`License check passed: ${total} packages, licenses: ${Object.keys(byLicense).sort().join(", ")}`);
