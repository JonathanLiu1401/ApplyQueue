import { useEffect, useState } from "react";
import { Copy, RefreshCw, Terminal } from "lucide-react";
import { Section } from "../ui";
import {
  getAgentBackends,
  getAgentSetupPrompt,
  selectAgentBackend,
  updateAgentBackend,
  type AgentBackend,
} from "../../lib/api";

const AUTH_LABEL: Record<string, string> = {
  logged_in: "Signed in",
  logged_out: "Not signed in",
  unknown: "Sign-in unknown",
  not_applicable: "API key only",
};

function authBadge(b: AgentBackend) {
  if (!b.runnable) return "bg-secondary text-muted-foreground";
  if (!b.installed) return "bg-secondary text-muted-foreground";
  return b.auth.state === "logged_in" ? "bg-green-500/10 text-green-600" : "bg-amber-500/10 text-amber-600";
}

/** M3: pick the agent that fills applications — the built-in browser agent or a subscription CLI. */
export default function AgentBackendsSection() {
  const [backends, setBackends] = useState<AgentBackend[]>([]);
  const [selected, setSelected] = useState("browser_use");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [termsFor, setTermsFor] = useState<AgentBackend | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [paths, setPaths] = useState<Record<string, string>>({});

  const load = () =>
    getAgentBackends()
      .then((r) => {
        setBackends(r.backends || []);
        setSelected(r.selected || "browser_use");
        setPaths(Object.fromEntries((r.backends || []).map((b) => [b.id, b.custom_path || ""])));
      })
      .catch(() => setError("Could not detect agent CLIs."))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^API Error \d+: /, "") : "Request failed");
    }
  };

  const choose = (b: AgentBackend) => {
    if (b.requires_terms && !b.terms_accepted) {
      setTermsFor(b);
      return;
    }
    act(() => updateAgentBackend(b.id, { select: true }));
  };

  return (
    <Section title="Agent backend">
      <p className="text-[13px] text-muted-foreground mb-4">
        Choose what fills in applications. Subscription CLIs run on this computer under your own sign-in and
        connect to ApplyQueue&apos;s tools (browser, form filler, secrets, email codes, job queue). ApplyQueue never
        reads or stores their login tokens.
      </p>
      {error && <div className="error-banner mb-3">{error}</div>}

      <label className="flex items-center gap-2 p-3 border border-border rounded-lg mb-2 cursor-pointer">
        <input
          type="radio"
          name="agent-backend"
          checked={selected === "browser_use"}
          onChange={() => act(() => selectAgentBackend("browser_use"))}
        />
        <span className="text-sm font-semibold text-foreground">Built-in browser agent</span>
        <span className="text-[12px] text-muted-foreground">uses the model from LLM Settings (API key)</span>
      </label>

      <div className="space-y-2" data-testid="agent-backends">
        {backends.map((b) => (
          <div key={b.id} className="p-3 border border-border rounded-lg" data-testid={`backend-${b.id}`}>
            <div className="flex items-center justify-between gap-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="agent-backend"
                  disabled={!b.runnable || !b.installed}
                  checked={selected === `cli:${b.id}`}
                  onChange={() => choose(b)}
                  aria-label={b.name}
                />
                <Terminal className="w-4 h-4 text-muted-foreground" />
                <span className="text-sm font-semibold text-foreground">{b.name}</span>
                {b.version && <span className="text-[11px] text-muted-foreground">{b.version}</span>}
              </label>
              <span className={`text-[11px] px-1.5 py-0.5 rounded ${authBadge(b)}`}>
                {!b.runnable ? "API key only" : !b.installed ? "Not installed" : AUTH_LABEL[b.auth.state] || b.auth.state}
              </span>
            </div>
            {b.notes && <p className="text-[12px] text-muted-foreground mt-1">{b.notes}</p>}
            {b.runnable && !b.installed && (
              <p className="text-[12px] text-muted-foreground mt-1">
                Install: <code>{b.install_hint}</code>
              </p>
            )}
            {b.runnable && b.installed && b.auth.state !== "logged_in" && (
              <p className="text-[12px] text-muted-foreground mt-1">
                Sign in from a terminal: <code>{b.login_command}</code>
              </p>
            )}
            {b.runnable && (
              <div className="flex gap-2 mt-2">
                <input
                  value={paths[b.id] ?? ""}
                  onChange={(e) => setPaths({ ...paths, [b.id]: e.target.value })}
                  placeholder={b.path || "Custom path to the CLI (optional)"}
                  className="input-base flex-1 !text-[12px]"
                  aria-label={`${b.name} path`}
                />
                <button className="btn-secondary !text-[12px]"
                  onClick={() => act(() => updateAgentBackend(b.id, { path: paths[b.id] ?? "" }))}>
                  Save path
                </button>
                {b.requires_terms && b.terms_accepted && (
                  <button className="btn-secondary !text-[12px]"
                    onClick={() => act(() => updateAgentBackend(b.id, { accept_terms: false }))}>
                    Turn off
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {termsFor && (
        <div className="mt-3 p-3 border border-amber-500/40 bg-amber-500/5 rounded-lg" role="dialog"
          aria-label={`${termsFor.name} terms`}>
          <p className="text-sm font-semibold text-foreground mb-1">Before you turn on {termsFor.name}</p>
          <p className="text-[13px] text-muted-foreground whitespace-pre-line">{termsFor.terms_text}</p>
          <div className="flex gap-2 mt-3">
            <button className="btn-primary"
              onClick={() => {
                const id = termsFor.id;
                setTermsFor(null);
                act(() => updateAgentBackend(id, { accept_terms: true, select: true }));
              }}>
              I understand, use {termsFor.name}
            </button>
            <button className="btn-secondary" onClick={() => setTermsFor(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="flex gap-2 mt-4">
        <button className="btn-secondary" onClick={() => { setLoading(true); load(); }} disabled={loading}>
          <RefreshCw className="w-4 h-4" /> Re-detect
        </button>
        <button className="btn-secondary"
          onClick={() => getAgentSetupPrompt().then((r) => setPrompt(r.prompt)).catch(() => setError("Could not build the prompt."))}>
          Grok Bot setup prompt
        </button>
      </div>
      {prompt !== null && (
        <div className="mt-3">
          <p className="text-[12px] text-muted-foreground mb-1">
            Paste this into Grok Bot to have it run applications on this computer. It contains no keys or passwords.
          </p>
          <textarea readOnly value={prompt} className="input-base w-full h-40 !text-[12px] font-mono" aria-label="Setup prompt" />
          <button className="btn-secondary mt-2" onClick={() => navigator.clipboard?.writeText(prompt).catch(() => {})}>
            <Copy className="w-4 h-4" /> Copy
          </button>
        </div>
      )}
    </Section>
  );
}
