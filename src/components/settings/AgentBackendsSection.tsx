import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink, KeyRound, RefreshCw, ShieldAlert, Terminal } from "lucide-react";
import { Section } from "../ui";
import {
  getAgentBackends,
  getAgentSetupPrompt,
  selectAgentBackend,
  updateAgentBackend,
  type AgentBackend,
} from "../../lib/api";

type Step = { label: string; command: string };

const AUTH_LABEL: Record<string, string> = {
  logged_in: "Signed in",
  logged_out: "Not signed in",
  unknown: "Sign-in unknown",
  not_applicable: "API key only",
};

const BADGE = {
  ok: "bg-green-500/10 text-green-600",
  warn: "bg-amber-500/10 text-amber-600",
  off: "bg-secondary text-muted-foreground",
};

function authTone(b: AgentBackend) {
  if (!b.runnable || !b.installed) return BADGE.off;
  return b.auth.state === "logged_in" ? BADGE.ok : BADGE.warn;
}

function installSteps(b: AgentBackend): Step[] {
  if (b.install_steps && b.install_steps.length) return b.install_steps;
  return b.install_hint ? [{ label: "Install", command: b.install_hint }] : [];
}

function loginSteps(b: AgentBackend): Step[] {
  if (b.login_steps && b.login_steps.length) return b.login_steps;
  return b.login_command ? [{ label: "Sign in", command: b.login_command }] : [];
}

/** A shell command with a one-click copy button. */
export function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    };
    Promise.resolve(navigator.clipboard?.writeText(command)).then(done, () => {});
  };
  return (
    <div className="flex items-center gap-2 min-w-0">
      <code className="flex-1 min-w-0 truncate rounded bg-secondary px-2 py-1 text-[12px] font-mono" title={command}>
        {command}
      </code>
      <button type="button" className="btn-secondary !px-2 !py-1 !text-[12px] shrink-0" onClick={copy}
        aria-label={`Copy: ${command}`}>
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function StepList({ title, steps }: { title: string; steps: Step[] }) {
  if (!steps.length) return null;
  return (
    <div className="mt-2">
      <p className="text-[12px] font-semibold text-foreground mb-1">{title}</p>
      <div className="space-y-1.5">
        {steps.map((s) => (
          <div key={s.label + s.command}>
            <p className="text-[11px] text-muted-foreground mb-0.5">{s.label}</p>
            <CopyCommand command={s.command} />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * "Use your AI subscription": run applications through a subscription CLI (Grok Build, Claude Code,
 * Codex, Cursor) instead of the API-key provider. The CLIs run unmodified on this computer under the
 * user's own sign-in; ApplyQueue only checks `--version` and the CLI's own status command and never
 * reads, copies or syncs their tokens.
 */
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
      const msg = e instanceof Error ? e.message.replace(/^API Error \d+: /, "") : "Request failed";
      setError(msg.includes("terms_required") ? "Accept the Claude Code terms first." : msg);
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
    <Section title="Use your AI subscription">
      <div data-testid="ai-subscription-section">
        <p className="text-[13px] text-muted-foreground mb-4">
          Already pay for SuperGrok, ChatGPT, Claude or Cursor? Run applications through that subscription&apos;s
          official CLI instead of an API key. The CLI runs on this computer under your own sign-in and connects to
          ApplyQueue&apos;s tools (browser, form filler, secrets, email codes, job queue). ApplyQueue never reads,
          stores or syncs their login tokens.
        </p>
        {error && <div className="error-banner mb-3">{error}</div>}

        <label className="flex items-start gap-2 p-3 border border-border rounded-lg mb-2 cursor-pointer"
          data-testid="backend-api-key">
          <input
            type="radio"
            name="agent-backend"
            className="mt-1"
            checked={selected === "browser_use"}
            onChange={() => act(() => selectAgentBackend("browser_use"))}
            aria-label="API key provider"
          />
          <KeyRound className="w-4 h-4 mt-0.5 text-muted-foreground" />
          <span>
            <span className="text-sm font-semibold text-foreground">API key provider</span>
            <span className={`ml-2 text-[11px] px-1.5 py-0.5 rounded ${BADGE.off}`}>Default</span>
            <span className="block text-[12px] text-muted-foreground">
              The built-in browser agent with the model and key from AI model settings (xAI Grok recommended).
            </span>
          </span>
        </label>

        <div className="space-y-2" data-testid="agent-backends">
          {backends.map((b) => {
            const ready = b.installed && b.auth.state === "logged_in";
            return (
              <div key={b.id} className="p-3 border border-border rounded-lg" data-testid={`backend-${b.id}`}>
                <div className="flex items-center justify-between gap-2 flex-wrap">
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
                    {b.requires_terms && (
                      <span className={`text-[11px] px-1.5 py-0.5 rounded ${BADGE.warn}`}>Opt-in</span>
                    )}
                  </label>
                  <div className="flex items-center gap-1.5">
                    {b.runnable && (
                      <span className={`text-[11px] px-1.5 py-0.5 rounded ${b.installed ? BADGE.ok : BADGE.off}`}
                        data-testid={`installed-${b.id}`}>
                        {b.installed ? `Installed${b.version ? ` · ${b.version}` : ""}` : "Not installed"}
                      </span>
                    )}
                    {(!b.runnable || b.installed) && (
                      <span className={`text-[11px] px-1.5 py-0.5 rounded ${authTone(b)}`} data-testid={`auth-${b.id}`}>
                        {!b.runnable ? "API key only" : AUTH_LABEL[b.auth.state] || b.auth.state}
                      </span>
                    )}
                  </div>
                </div>
                {b.notes && <p className="text-[12px] text-muted-foreground mt-1">{b.notes}</p>}
                {b.requires_terms && b.terms_accepted && (
                  <p className="text-[12px] text-muted-foreground mt-1">You accepted the terms for {b.name}.</p>
                )}

                {b.runnable && (
                  <details className="mt-2" open={!ready}>
                    <summary className="text-[12px] font-semibold text-primary cursor-pointer select-none">
                      Setup steps
                    </summary>
                    <StepList title="1. Install" steps={installSteps(b)} />
                    <StepList title="2. Sign in (in a terminal)" steps={loginSteps(b)} />
                    {b.docs_url && (
                      <a href={b.docs_url} target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 mt-2 text-[12px] text-primary font-semibold hover:underline">
                        Docs <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
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
                  </details>
                )}
              </div>
            );
          })}
        </div>

        {termsFor && (
          <div className="mt-3 p-3 border border-amber-500/40 bg-amber-500/5 rounded-lg" role="dialog"
            aria-label={`${termsFor.name} terms`} data-testid="terms-dialog">
            <p className="flex items-center gap-1.5 text-sm font-semibold text-foreground mb-1">
              <ShieldAlert className="w-4 h-4 text-amber-600" /> Before you turn on {termsFor.name}
            </p>
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
      </div>
    </Section>
  );
}
