import { useState } from "react";
import { href } from "../router";
import { Page, Todo } from "./Stub";

const STEPS = ["Upload your resume", "Pick job titles and locations", "Press Start"] as const;

/** Headline 3-step onboarding. Everything else (answers, work auth, EEO, email, sync) is optional and progressive. */
export function Onboarding() {
  const [step, setStep] = useState(0);
  const [titles, setTitles] = useState("");
  const [locations, setLocations] = useState("");
  const [who, setWho] = useState<"grok-bot" | "local" | "other">("grok-bot");

  return (
    <Page title="Get started in 3 steps" lead="Upload a resume, say what you want, press Start. You can refine everything later.">
      <ol className="steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "current" : i < step ? "done" : ""}>
            {s}
          </li>
        ))}
      </ol>

      <div className="card">
        {step === 0 && (
          <>
            <h2>1. Upload your resume</h2>
            <p>Drop a PDF. Your AI backend reads it to pre-fill your profile; you confirm each field.</p>
            <div className="dropzone" aria-disabled="true">Drop resume PDF here</div>
            <Todo milestone="M6">Resume import and field confirmation.</Todo>
          </>
        )}
        {step === 1 && (
          <>
            <h2>2. Pick job titles and locations</h2>
            <label>
              Job titles (comma separated)
              <input value={titles} onChange={(e) => setTitles(e.target.value)} placeholder="e.g. data analyst, registered nurse" />
            </label>
            <label>
              Locations
              <input value={locations} onChange={(e) => setLocations(e.target.value)} placeholder="e.g. Remote, Chicago, US" />
            </label>
            <p className="muted">Filters, excluded companies, and boards are optional; set them later on the queue.</p>
          </>
        )}
        {step === 2 && (
          <>
            <h2>3. Press Start</h2>
            <p>
              This creates your first <strong>queue</strong> in <em>review-first</em> mode: you approve each match before the
              agent applies. Switch to fully automatic per queue when you trust it.
            </p>
            <fieldset className="choices">
              <legend>Who does the work?</legend>
              <label>
                <input type="radio" name="who" checked={who === "grok-bot"} onChange={() => setWho("grok-bot")} />
                <strong>Grok Bot</strong> <span className="badge">Recommended</span>
                <span className="muted"> Runs on its own cloud computer; nothing to keep on at home.</span>
              </label>
              <label>
                <input type="radio" name="who" checked={who === "local"} onChange={() => setWho("local")} />
                <strong>Run 100% locally</strong> <span className="badge">Free, no subscription</span>
                <span className="muted"> A local AI model on this computer. Slower, needs a decent GPU, best overnight.</span>
              </label>
              <label>
                <input type="radio" name="who" checked={who === "other"} onChange={() => setWho("other")} />
                <strong>Another AI</strong>
                <span className="muted"> ChatGPT, Claude, Gemini, or Cursor (<a href={href("backends")}>compare</a>).</span>
              </label>
            </fieldset>
            {who === "local" && <Todo milestone="M8">Detects Ollama / LM Studio / llama.cpp and recommends a model that fits your GPU.</Todo>}
            <button className="primary" disabled title="Arrives with M3 and M7">
              Start
            </button>
            <Todo milestone="M3/M7">Queue creation and the apply loop.</Todo>
          </>
        )}
        <div className="row">
          <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
            Back
          </button>
          <button onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))} disabled={step === STEPS.length - 1}>
            Next
          </button>
        </div>
      </div>

      <details className="card">
        <summary>Optional: improve answer quality (progressive setup)</summary>
        <ul>
          <li>Answer bank (top common questions, pre-filled from your resume)</li>
          <li>Work authorization and sponsorship (plain-language questions per country)</li>
          <li>Voluntary EEO choices (explicit answer or "decline to self-identify")</li>
          <li>Email connection for confirmation tracking (Gmail / Outlook / IMAP)</li>
          <li>Portal sign-ins and private sync repo for multiple agents</li>
        </ul>
        <Todo milestone="M6">Each item is offered when it first matters, never as a wall of forms.</Todo>
      </details>
    </Page>
  );
}
