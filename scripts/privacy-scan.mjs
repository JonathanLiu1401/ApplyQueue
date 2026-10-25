#!/usr/bin/env node
/**
 * Personal-data scan for the PUBLIC ApplyQueue repo. Runs in CI on every push/PR and locally
 * via `pnpm privacy-scan`. It complements gitleaks (which looks for credentials/tokens).
 *
 * 1. Generic rules (no personal data embedded): forbidden paths (browser profiles, cookie and
 *    password stores, databases, personal inventories), email addresses outside an allowlist of
 *    example domains, and phone numbers outside the fictional 555-01xx range.
 * 2. Hash denylist: maintainers can supply SHA-256 hashes of strings that must never appear
 *    (their own names, emails, phone digits, addresses) via the PRIVACY_DENYLIST_SHA256 env var
 *    (newline/comma separated hex; set as a GitHub Actions secret) or a local file named by
 *    PRIVACY_DENYLIST_FILE. The raw values are never in this repo. Generate hashes with
 *    `node scripts/privacy-scan.mjs --hash "text"` (normalization: lowercase, single spaces).
 *
 * Scope: every path ever committed, every blob reachable from any ref (full history), and every
 * commit message/author. Output names only rule + location, never the matched value.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const norm = (s) => s.toLowerCase().replace(/\s+/g, " ").trim();
const sha = (s) => createHash("sha256").update(norm(s)).digest("hex");

if (process.argv[2] === "--hash") {
  console.log(sha(process.argv.slice(3).join(" ")));
  process.exit(0);
}

const git = (...args) => execFileSync("git", args, { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });

// ---------- rules ----------
const BAD_PATH = [
  /(^|\/)(Cookies|Cookies-journal|Login Data|Login Data For Account|Local State|Web Data|History)$/,
  /(^|\/)(credentials|sessions|browser-profiles)\//,
  /(^|\/)profiles\//,
  /password[^/]*\.txt$/i,
  /\.(db|sqlite3?|kdbx|pem|p12|pfx|key|keychain)$/i,
  /INVENTORY[^/]*\.md$/i,
  /(^|\/)\.env(\.(?!example$)[^/]+)?$/,
  /(^|\/)(profile|knowledge|answers|accounts)\.json$/,
];
const EMAIL = /[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}/g;
const EMAIL_ALLOW = /@((.+\.)?example\.(com|org|net)|users\.noreply\.github\.com|localhost|test\.invalid)$/i;
const PHONE = /(?<![\w.+-])(\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w-])/g;
const PHONE_ALLOW = /555[ .-]?01\d\d$/;

const denylist = new Set(
  [process.env.PRIVACY_DENYLIST_SHA256 ?? "", process.env.PRIVACY_DENYLIST_FILE ? readFileSync(process.env.PRIVACY_DENYLIST_FILE, "utf8") : ""]
    .join("\n")
    .split(/[\s,]+/)
    .map((h) => h.trim().toLowerCase())
    .filter((h) => /^[0-9a-f]{64}$/.test(h)),
);

const MAX_NGRAM = 6;
function denyHits(text) {
  if (!denylist.size) return 0;
  let hits = 0;
  for (const line of text.split("\n")) {
    const words = line.toLowerCase().match(/[\p{L}\p{N}@._+'-]+/gu) ?? [];
    const clean = words.map((w) => w.replace(/^[._'-]+|[._'-]+$/g, ""));
    for (let i = 0; i < clean.length; i++) {
      let gram = "";
      for (let n = 0; n < MAX_NGRAM && i + n < clean.length; n++) {
        gram = n ? `${gram} ${clean[i + n]}` : clean[i];
        if (denylist.has(sha(gram))) hits++;
      }
    }
    for (const run of line.match(/[\d][\d ().+-]{5,}\d/g) ?? []) {
      if (denylist.has(sha(run.replace(/\D/g, "")))) hits++;
    }
  }
  return hits;
}

function genericHits(text) {
  const out = [];
  for (const m of text.matchAll(EMAIL)) if (!EMAIL_ALLOW.test(m[0])) out.push("email-address");
  for (const m of text.matchAll(PHONE)) if (!PHONE_ALLOW.test(m[0])) out.push("phone-number");
  return out;
}

// ---------- scan ----------
const findings = [];
const add = (rule, where) => findings.push(`${rule}: ${where}`);

const hasHistory = (() => { try { git("rev-parse", "HEAD"); return true; } catch { return false; } })();
if (!hasHistory) { console.log("No commits yet; nothing to scan."); process.exit(0); }

// commits: messages + authors
const log = git("log", "--all", "--format=%H%x00%an%x00%ae%x00%B%x1e");
for (const rec of log.split("\x1e")) {
  const [hash, an, ae, body] = rec.trim().split("\x00");
  if (!hash) continue;
  const text = `${an}\n${ae}\n${body ?? ""}`;
  for (const r of genericHits(text)) add(r, `commit ${hash.slice(0, 8)} message/author`);
  if (denyHits(text)) add("denylist", `commit ${hash.slice(0, 8)} message/author`);
}

// every path ever committed + every blob in history
const seenBlobs = new Map(); // blob -> first path
for (const line of git("rev-list", "--all", "--objects").split("\n")) {
  const sp = line.indexOf(" ");
  if (sp < 0) continue;
  const oid = line.slice(0, sp);
  const path = line.slice(sp + 1);
  if (!path) continue;
  if (BAD_PATH.some((re) => re.test(path))) add("forbidden-path", path);
  if (!seenBlobs.has(oid)) seenBlobs.set(oid, path);
}
const types = git("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", "--batch-all-objects");
const blobSize = new Map();
for (const l of types.split("\n")) {
  const [oid, type, size] = l.split(" ");
  if (type === "blob") blobSize.set(oid, Number(size));
}
let scanned = 0;
for (const [oid, path] of seenBlobs) {
  const size = blobSize.get(oid);
  if (size === undefined || size > 5 * 1024 * 1024) continue;
  const buf = execFileSync("git", ["cat-file", "blob", oid], { maxBuffer: 16 * 1024 * 1024 });
  if (buf.includes(0)) continue; // binary
  const text = buf.toString("utf8");
  scanned++;
  for (const r of new Set(genericHits(text))) add(r, `${path} (blob ${oid.slice(0, 8)})`);
  const d = denyHits(text);
  if (d) add("denylist", `${path} (blob ${oid.slice(0, 8)}, ${d} hit${d > 1 ? "s" : ""})`);
}

console.log(`privacy-scan: ${scanned} text blobs across all refs, denylist entries: ${denylist.size}`);
if (findings.length) {
  console.error(`privacy-scan FAILED with ${findings.length} finding(s):\n  ${[...new Set(findings)].join("\n  ")}`);
  process.exit(1);
}
console.log("privacy-scan: clean");
