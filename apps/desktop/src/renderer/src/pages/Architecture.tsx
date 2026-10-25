import { Page } from "./Stub";

export function Architecture() {
  return (
    <Page title="How it works" lead="A plain-language map of what runs where. Live status arrives with the components.">
      <pre className="diagram">{`
 You ──► ApplyQueue app (this window)
           │  config: profile, queues, answers  (stays on this computer)
           ▼
   ApplyQueue MCP server  ◄── AI backend (Grok Bot · Local model · ChatGPT · Claude · Gemini · Cursor)
     tools: next_job · get_answer · fill_secret · finish_job · label email · ...
           │
           ├─► Browser (Playwright/CDP) ──► job boards and application portals
           ├─► Email (Gmail / Outlook / IMAP) ──► confirmations, codes, labels
           └─► Private sync repo (your GitHub) ──► queue, ledger, credentials for multiple agents
`}</pre>
    </Page>
  );
}
