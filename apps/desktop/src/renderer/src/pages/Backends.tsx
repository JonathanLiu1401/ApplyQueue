import { Page, Todo } from "./Stub";

interface Backend {
  id: string;
  name: string;
  tag?: string;
  blurb: string;
}

const BACKENDS: Backend[] = [
  { id: "grok-bot", name: "Grok Bot", tag: "Recommended", blurb: "Runs on its own cloud computer with a browser, routines, and a vault. Nothing to keep running at home." },
  { id: "local", name: "Local (100% on your machine)", tag: "No subscription", blurb: "Ollama, LM Studio, llama.cpp, or Hugging Face models. Private and free; slower, needs a decent GPU, best run overnight." },
  { id: "chatgpt", name: "ChatGPT", blurb: "Use a ChatGPT agent with a generated setup prompt, or the OpenAI API with the in-app runner." },
  { id: "claude", name: "Claude", blurb: "Claude in Chrome / Claude Code, or the Anthropic API with the in-app runner." },
  { id: "gemini", name: "Gemini", blurb: "Gemini agent features, or the Gemini API with the in-app runner." },
  { id: "cursor", name: "Cursor", blurb: "Cursor agents connected to the ApplyQueue MCP server." },
];

export function Backends() {
  return (
    <Page title="AI backends" lead="Every backend drives the same tools (browser, forms, email, queue) through the ApplyQueue MCP server.">
      <div className="grid">
        {BACKENDS.map((b) => (
          <div className={`card ${b.id === "local" ? "local" : ""}`} key={b.id}>
            <h2>
              {b.name} {b.tag && <span className="badge">{b.tag}</span>}
            </h2>
            <p>{b.blurb}</p>
            {b.id === "local" && (
              <>
                <ul className="muted">
                  <li>Detect installed runtimes (Ollama, LM Studio, llama-server)</li>
                  <li>Recommend a vision-capable model that fits your RAM/VRAM</li>
                  <li>One-click model download</li>
                </ul>
                <button disabled>Detect local runtimes</button>
              </>
            )}
            <button disabled>Connect</button>
          </div>
        ))}
      </div>
      <Todo milestone="M5/M6">Backend adapters, local runtime detection, model recommender, and the in-app agent runner.</Todo>
    </Page>
  );
}
