import { useEffect, useRef, useState } from "react";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { Section } from "../ui";
import {
  exportSyncKey,
  getSyncStatus,
  importSyncKey,
  pollSyncDeviceFlow,
  saveSyncConfig,
  saveSyncToken,
  setupSyncRepo,
  startSyncDeviceFlow,
  syncNow,
  unlinkSync,
  type SyncStatus,
} from "../../lib/api";
import { syncNote } from "./syncNote";

export const SYNC_WARNING =
  "This repository holds your job portal passwords and signed-in sessions. It must stay private. Anyone with access to it can sign in as you. Never make it public, never add collaborators you would not give your passwords to.";

async function openExternal(url: string) {
  try {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

function errText(e: unknown) {
  return e instanceof Error ? e.message.replace(/^API Error \d+: /, "") : "Request failed";
}

const input = "w-full px-2 py-1.5 text-sm rounded border border-border bg-background";
const btn = "px-3 py-1.5 text-sm rounded border border-border hover:bg-secondary disabled:opacity-50";
const primary = "px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground disabled:opacity-50";

export default function SyncSection() {
  const [st, setSt] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [clientId, setClientId] = useState("");
  const [pat, setPat] = useState("");
  const [repoName, setRepoName] = useState("applyqueue-state");
  const [code, setCode] = useState<{ user_code: string; verification_uri: string } | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [keyText, setKeyText] = useState("");
  const timer = useRef<number | null>(null);

  const load = () =>
    getSyncStatus()
      .then((r) => {
        setSt(r);
        setClientId((c) => c || r.config.client_id || "");
      })
      .catch(() => setError("Could not load sync status."));

  useEffect(() => {
    load();
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    setNote(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
      load();
    }
  };

  const signIn = () =>
    run(async () => {
      await saveSyncConfig({ client_id: clientId.trim() });
      const flow = await startSyncDeviceFlow();
      setCode(flow);
      await openExternal(flow.verification_uri);
      if (timer.current) window.clearInterval(timer.current);
      timer.current = window.setInterval(async () => {
        try {
          const r = await pollSyncDeviceFlow();
          if (r.status === "ok" || r.status === "denied" || r.status === "expired" || r.status === "error") {
            if (timer.current) window.clearInterval(timer.current);
            setCode(null);
            if (r.status !== "ok") setError(r.error || `GitHub sign-in ${r.status}.`);
            load();
          }
        } catch (e) {
          if (timer.current) window.clearInterval(timer.current);
          setError(errText(e));
        }
      }, Math.max(5, flow.interval) * 1000);
    });

  return (
    <Section title="Sync across devices (private GitHub repo)">
      <div className="space-y-3" data-testid="sync-section">
        <p className="text-[13px] text-muted-foreground">
          Keep your queue, application ledger, claim locks, portal logins and signed-in sessions in one private
          GitHub repository so every device and agent works from the same list without applying twice.
        </p>
        <div className="p-2 rounded bg-amber-500/10 flex gap-2 text-[13px]" role="note">
          <ShieldAlert className="w-4 h-4 shrink-0 text-amber-600" />
          <span>{SYNC_WARNING}</span>
        </div>

        {st && !st.git && (
          <p className="text-[13px] text-red-600">git is not installed. On macOS run "xcode-select --install".</p>
        )}
        {st?.visibility_error && (
          <p className="text-[13px] text-red-600" role="alert">
            Sync paused: {st.visibility_error} Your logins stay on this device until the repo is private again.
          </p>
        )}
        {st?.auth_error && (
          <p className="text-[13px] text-red-600" role="alert">
            Sync paused: {st.auth_error} Your changes stay on this device until sync works again.
          </p>
        )}

        {st && !st.github_linked && (
          <div className="space-y-2">
            <label className="block text-[13px]">
              GitHub OAuth App client ID (device flow enabled)
              <input className={input} value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="Ov23li…" />
            </label>
            <button className={primary} disabled={busy || !clientId.trim()} onClick={signIn}>
              Sign in with GitHub
            </button>
            {code && (
              <p className="text-sm" data-testid="sync-user-code">
                Enter code <span className="font-mono font-semibold">{code.user_code}</span> at {code.verification_uri}
              </p>
            )}
            <details className="text-[13px]">
              <summary className="cursor-pointer text-muted-foreground">Use a personal access token instead</summary>
              <div className="flex gap-2 mt-2">
                <input className={input} type="password" value={pat} onChange={(e) => setPat(e.target.value)}
                  placeholder="Fine-grained token with Contents read/write on the state repo" />
                <button className={btn} disabled={busy || !pat} onClick={() => run(async () => { await saveSyncToken(pat); setPat(""); })}>
                  Save
                </button>
              </div>
            </details>
          </div>
        )}

        {st?.github_linked && !st.configured && (
          <div className="space-y-2">
            <label className="block text-[13px]">
              Private repository name (created if it does not exist)
              <input className={input} value={repoName} onChange={(e) => setRepoName(e.target.value)} />
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              I understand this repo must stay private.
            </label>
            <button className={primary} disabled={busy || !ack || !repoName.trim()}
              onClick={() => run(() => setupSyncRepo(repoName.trim()))}>
              Create / connect private repo
            </button>
          </div>
        )}

        {st?.configured && (
          <div className="space-y-2 text-[13px]">
            <div className="flex items-center justify-between">
              <span>
                <span className="font-semibold">{st.owner}/{st.repo}</span> · this device: {st.device_label}
                {st.offline ? " · offline" : ""}
                {st.pending ? ` · ${st.pending} change(s) waiting to upload` : ""}
              </span>
              <button className={btn} disabled={busy}
                onClick={() => run(async () => { const r = await syncNow(); setNote(syncNote(r)); })}>
                <RefreshCw className="w-3.5 h-3.5 inline mr-1" />Sync now
              </button>
            </div>
            <p className="text-muted-foreground">
              Last sync: {st.last_sync ? new Date(st.last_sync).toLocaleString() : "never"}
              {st.verified_private_at ? ` · private verified ${new Date(st.verified_private_at).toLocaleString()}` : ""}
              {st.devices?.length ? ` · devices: ${st.devices.length}` : ""}
            </p>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={st.config.sync_credentials}
                onChange={(e) => run(() => saveSyncConfig({ sync_credentials: e.target.checked }))} />
              Sync portal logins and sessions
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={st.config.encrypt_credentials}
                onChange={(e) => run(() => saveSyncConfig({ encrypt_credentials: e.target.checked }))} />
              Also encrypt logins and sessions in the repo (optional; every device needs the key)
            </label>
            {st.config.encrypt_credentials && (
              <div className="flex gap-2">
                <input className={input} value={keyText} onChange={(e) => setKeyText(e.target.value)} placeholder="aqk1.… key from another device" />
                <button className={btn} disabled={busy || !keyText} onClick={() => run(async () => { await importSyncKey(keyText); setKeyText(""); })}>Import key</button>
                <button className={btn} disabled={busy}
                  onClick={() => run(async () => { const r = await exportSyncKey(); setNote(`Key for your other devices: ${r.key}`); })}>
                  Show key
                </button>
              </div>
            )}
            <button className={btn} disabled={busy} onClick={() => run(unlinkSync)}>Stop syncing on this device</button>
          </div>
        )}
        {note && <p className="text-[13px] text-green-700 break-all">{note}</p>}
        {error && <p className="text-[13px] text-red-600" role="alert">{error}</p>}
      </div>
    </Section>
  );
}
