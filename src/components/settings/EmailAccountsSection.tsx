import { useEffect, useState } from "react";
import { Mail, RefreshCw } from "lucide-react";
import { Section } from "../ui";
import {
  connectEmail,
  disconnectEmail,
  getEmailStatus,
  saveEmailClient,
  saveEmailSettings,
  sweepEmail,
  type EmailProvider,
  type EmailProviderStatus,
  type EmailSettings,
} from "../../lib/api";

const PROVIDERS: EmailProvider[] = ["gmail", "outlook"];

const SETUP_HELP: Record<EmailProvider, string> = {
  gmail:
    "Create your own OAuth client in Google Cloud Console (APIs & Services → Credentials → Desktop app) with the Gmail API enabled, then paste its ID and secret here. While the app is in Google's \"Testing\" mode, Google expires the sign-in after 7 days; ApplyQueue asks you to reconnect on day 6.",
  outlook:
    "Register an app in Microsoft Entra (App registrations → Mobile and desktop applications) with the redirect URI below and delegated Mail.ReadWrite permission, then paste its Application (client) ID. No secret is needed.",
};

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

function ProviderCard({ st, onChange }: { st: EmailProviderStatus; onChange: () => void }) {
  const [clientId, setClientId] = useState(st.client_id || "");
  const [secret, setSecret] = useState("");
  const [tenant, setTenant] = useState(st.tenant || "common");
  const [testing, setTesting] = useState(st.testing_app !== false);
  const [editing, setEditing] = useState(!st.configured);
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setError(errText(e));
    }
  };

  const connect = () =>
    run(async () => {
      const { url } = await connectEmail(st.provider);
      setWaiting(true);
      await openExternal(url);
    });

  const reauth = st.linked && (st.needs_reauth || st.reauth_due);

  return (
    <div className="p-3 border border-border rounded-lg" data-testid={`mail-${st.provider}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Mail className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm font-semibold text-foreground">{st.name}</span>
          {st.linked && <span className="text-[12px] text-muted-foreground">{st.email}</span>}
        </div>
        <span className={`text-[11px] px-1.5 py-0.5 rounded ${st.linked && !reauth ? "bg-green-500/10 text-green-600" : "bg-secondary text-muted-foreground"}`}>
          {st.linked ? (reauth ? "Reconnect needed" : "Connected") : st.configured ? "Not connected" : "Not set up"}
        </span>
      </div>

      {reauth && (
        <div className="mt-2 p-2 rounded bg-amber-500/10 flex items-center justify-between" role="alert">
          <span className="text-[12px] text-amber-700">
            {st.needs_reauth ? "The sign-in expired or was revoked." : "Google will expire this sign-in within a day."}
          </span>
          <button className="btn-primary !text-[12px]" onClick={connect}>Reconnect</button>
        </div>
      )}
      {error && <div className="error-banner mt-2">{error}</div>}
      {st.last_error && !reauth && <p className="text-[12px] text-destructive mt-1">{st.last_error}</p>}

      {editing ? (
        <div className="mt-2 space-y-2">
          <p className="text-[12px] text-muted-foreground">{SETUP_HELP[st.provider]}</p>
          <p className="text-[12px] text-muted-foreground">
            Redirect URI: <code>{st.registration_redirect_uri}</code>
          </p>
          <input className="input-base" placeholder="Client ID" value={clientId} aria-label={`${st.name} client ID`}
            onChange={(e) => setClientId(e.target.value)} />
          {st.provider === "gmail" ? (
            <>
              <input className="input-base" type="password" aria-label={`${st.name} client secret`}
                placeholder={st.client_secret_set ? "Client secret (saved — leave blank to keep)" : "Client secret"}
                value={secret} onChange={(e) => setSecret(e.target.value)} />
              <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
                <input type="checkbox" checked={testing} onChange={(e) => setTesting(e.target.checked)} />
                App is in Google&apos;s Testing mode (7-day sign-in)
              </label>
            </>
          ) : (
            <input className="input-base" placeholder="Tenant (common, consumers, organizations or a tenant ID)"
              aria-label="Tenant" value={tenant} onChange={(e) => setTenant(e.target.value)} />
          )}
          <div className="flex gap-2">
            <button className="btn-primary"
              onClick={() => run(async () => {
                await saveEmailClient(st.provider, st.provider === "gmail"
                  ? { client_id: clientId, ...(secret ? { client_secret: secret } : {}), testing_app: testing }
                  : { client_id: clientId, tenant });
                setSecret("");
                setEditing(false);
              })}>
              Save client
            </button>
            {st.configured && <button className="btn-secondary" onClick={() => setEditing(false)}>Cancel</button>}
          </div>
        </div>
      ) : (
        <div className="flex gap-2 mt-2">
          {!st.linked && (
            <button className="btn-primary" disabled={!st.configured} onClick={connect}>Connect</button>
          )}
          {waiting && !st.linked && (
            <button className="btn-secondary" onClick={() => { setWaiting(false); onChange(); }}>
              <RefreshCw className="w-4 h-4" /> I finished signing in
            </button>
          )}
          {st.linked && <button className="btn-secondary" onClick={() => run(() => disconnectEmail(st.provider))}>Disconnect</button>}
          <button className="btn-secondary" onClick={() => setEditing(true)}>Edit client</button>
        </div>
      )}
    </div>
  );
}

/** M4: link Gmail/Outlook so confirmations are detected and OTP codes are read over the API. */
export default function EmailAccountsSection() {
  const [providers, setProviders] = useState<Record<EmailProvider, EmailProviderStatus> | null>(null);
  const [settings, setSettings] = useState<EmailSettings | null>(null);
  const [lastSweep, setLastSweep] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    getEmailStatus()
      .then((r) => {
        setProviders(r.providers);
        setSettings((prev) => prev ?? r.settings);
        const s = r.last_sweep?.summary;
        if (r.last_sweep?.at && s) {
          setLastSweep(`Last check ${new Date(r.last_sweep.at * 1000).toLocaleString()}: ${s.confirmed.length} confirmed, ${s.verification_archived} code emails archived, ${s.interviews ?? 0} interviews, ${s.rejections ?? 0} rejections.`);
        }
      })
      .catch(() => setError("Could not load linked mailboxes."));

  useEffect(() => {
    load();
    // Pick up the OAuth result once the user returns from the browser.
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const anyLinked = providers ? PROVIDERS.some((p) => providers[p]?.linked) : false;

  const saveSettings = async () => {
    if (!settings) return;
    setError(null);
    try {
      const r = await saveEmailSettings(settings);
      setSettings(r.settings);
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <Section title="Linked email">
      <p className="text-[13px] text-muted-foreground mb-4">
        Optional. With a linked mailbox ApplyQueue reads verification codes through the Gmail or Microsoft Graph API
        (no webmail browsing), marks a job confirmed when its confirmation email arrives, labels rejections and interview invitations, and labels and archives
        those emails. Only emails that match a job you applied to are touched; email bodies are not stored.
        Tokens stay in a private file on this computer.
      </p>
      {error && <div className="error-banner mb-3">{error}</div>}
      <div className="space-y-2">
        {providers && PROVIDERS.map((p) => providers[p] && <ProviderCard key={p} st={providers[p]} onChange={load} />)}
      </div>

      {settings && (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-semibold text-foreground mb-1.5">Confirmation label</label>
            <input className="input-base" value={settings.confirm_label} aria-label="Confirmation label"
              onChange={(e) => setSettings({ ...settings, confirm_label: e.target.value })} />
            <label className="flex items-center gap-2 text-[12px] text-muted-foreground mt-1">
              <input type="checkbox" checked={settings.archive_confirmations}
                onChange={(e) => setSettings({ ...settings, archive_confirmations: e.target.checked })} />
              Archive after labeling
            </label>
          </div>
          <div>
            <label className="block text-sm font-semibold text-foreground mb-1.5">Verification-code label</label>
            <input className="input-base" value={settings.verification_label} aria-label="Verification label"
              onChange={(e) => setSettings({ ...settings, verification_label: e.target.value })} />
            <label className="flex items-center gap-2 text-[12px] text-muted-foreground mt-1">
              <input type="checkbox" checked={settings.archive_verification}
                onChange={(e) => setSettings({ ...settings, archive_verification: e.target.checked })} />
              Archive after use
            </label>
          </div>
          <div>
            <label className="block text-sm font-semibold text-foreground mb-1.5">Rejection label</label>
            <input className="input-base" value={settings.rejection_label ?? "ApplyQueue/Rejected"} aria-label="Rejection label"
              onChange={(e) => setSettings({ ...settings, rejection_label: e.target.value })} />
            <label className="flex items-center gap-2 text-[12px] text-muted-foreground mt-1">
              <input type="checkbox" checked={!!settings.archive_rejections}
                onChange={(e) => setSettings({ ...settings, archive_rejections: e.target.checked })} />
              Archive rejections
            </label>
          </div>
          <div>
            <label className="block text-sm font-semibold text-foreground mb-1.5">Interview label</label>
            <input className="input-base" value={settings.interview_label ?? "ApplyQueue/Interview"} aria-label="Interview label"
              onChange={(e) => setSettings({ ...settings, interview_label: e.target.value })} />
            <p className="text-[12px] text-muted-foreground mt-1">Interview invitations stay in your inbox.</p>
          </div>
          <label className="flex items-center gap-2 text-[13px] text-foreground col-span-2">
            <input type="checkbox" checked={settings.auto_sweep}
              onChange={(e) => setSettings({ ...settings, auto_sweep: e.target.checked })} />
            Check for confirmation emails every
            <input type="number" min={2} max={1440} className="input-base !w-20" value={settings.sweep_minutes}
              aria-label="Sweep minutes"
              onChange={(e) => setSettings({ ...settings, sweep_minutes: Number(e.target.value) || 10 })} />
            minutes
          </label>
          <div className="col-span-2 flex gap-2">
            <button className="btn-secondary" onClick={saveSettings}>Save email settings</button>
            <button className="btn-secondary" disabled={!anyLinked || busy}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  const r = await sweepEmail();
                  setLastSweep(`Checked now: ${r.confirmed.length} confirmed, ${r.verification_archived} code emails archived, ${r.interviews ?? 0} interviews, ${r.rejections ?? 0} rejections.`);
                } catch (e) {
                  setError(errText(e));
                } finally {
                  setBusy(false);
                }
              }}>
              <RefreshCw className="w-4 h-4" /> Check now
            </button>
          </div>
        </div>
      )}
      {lastSweep && <p className="text-[12px] text-muted-foreground mt-2" data-testid="last-sweep">{lastSweep}</p>}
    </Section>
  );
}
