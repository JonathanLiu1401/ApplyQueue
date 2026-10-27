import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentBackendsSection from "./AgentBackendsSection";
import EmailAccountsSection from "./EmailAccountsSection";
import * as api from "../../lib/api";

vi.mock("../../lib/api", () => ({
  getAgentBackends: vi.fn(),
  updateAgentBackend: vi.fn(async () => ({ success: true, selected: "" })),
  selectAgentBackend: vi.fn(async () => ({ success: true, selected: "browser_use" })),
  getAgentSetupPrompt: vi.fn(async () => ({ prompt: "Set up ApplyQueue on this computer." })),
  getEmailStatus: vi.fn(),
  saveEmailClient: vi.fn(async () => ({ success: true })),
  connectEmail: vi.fn(async () => ({ success: true, url: "https://accounts.example.com/auth?state=s" })),
  disconnectEmail: vi.fn(async () => ({ success: true })),
  saveEmailSettings: vi.fn(async (s: object) => ({ success: true, settings: s })),
  sweepEmail: vi.fn(async () => ({ success: true, confirmed: ["https://jobs.example.com/1"], labeled: 1, verification_archived: 2, errors: [] })),
}));

const shellOpen = vi.fn(async (url: string) => url);
vi.mock("@tauri-apps/plugin-shell", () => ({ open: (u: string) => shellOpen(u) }));

const base = {
  vendor: "x", transport: "stream", runnable: true, requires_terms: false, terms_text: null,
  terms_accepted: false, enabled: false, selected: false, installed: true, path: "/usr/local/bin/x",
  custom_path: "", version: "1.0", auth: { state: "logged_in" }, login_command: "x login",
  install_hint: "npm i -g x", docs_url: "", login_methods: [], notes: "",
};

const backends = [
  { ...base, id: "grok", name: "Grok Build CLI", version: "grok 0.1.42",
    install_steps: [{ label: "macOS / Linux", command: "curl -fsSL https://x.ai/cli/install.sh | bash" }],
    login_steps: [{ label: "Sign in", command: "grok login" }] },
  { ...base, id: "codex", name: "Codex CLI", installed: false, version: null, path: null, auth: { state: "unknown" },
    install_steps: [{ label: "npm", command: "npm install -g @openai/codex" }],
    login_steps: [{ label: "Sign in with ChatGPT", command: "codex login" }] },
  { ...base, id: "claude", name: "Claude Code", requires_terms: true, terms_text: "Uses your own Claude login.", auth: { state: "logged_out" } },
  { ...base, id: "gemini", name: "Gemini", runnable: false, installed: false, auth: { state: "not_applicable" } },
];

const mailStatus = (over: Partial<api.EmailProviderStatus> = {}) => ({
  provider: "gmail" as const, name: "Gmail", configured: true, client_id: "id.apps.example.com",
  client_secret_set: true, tenant: null, testing_app: true, linked: true, email: "alex@example.com",
  authorized_at: 1, reauth_due_at: 2, needs_reauth: false, reauth_due: false, last_error: "",
  registration_redirect_uri: "http://127.0.0.1", scopes: [], ...over,
});

describe("AgentBackendsSection", () => {
  beforeEach(() => {
    vi.mocked(api.getAgentBackends).mockResolvedValue({ selected: "browser_use", backends } as never);
  });

  it("shows install and login state and selects a signed-in CLI", async () => {
    render(<AgentBackendsSection />);
    expect(await screen.findByText("Grok Build CLI")).toBeInTheDocument();
    expect(screen.getByText("Not signed in")).toBeInTheDocument();
    expect(screen.getByText("x login")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Gemini" })).toBeDisabled();
    await userEvent.click(screen.getByRole("radio", { name: "Grok Build CLI" }));
    expect(api.updateAgentBackend).toHaveBeenCalledWith("grok", { select: true });
  });

  it("requires the terms acknowledgement before Claude Code", async () => {
    render(<AgentBackendsSection />);
    await userEvent.click(await screen.findByRole("radio", { name: "Claude Code" }));
    expect(api.updateAgentBackend).not.toHaveBeenCalledWith("claude", expect.anything());
    expect(screen.getByText("Uses your own Claude login.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /I understand/ }));
    expect(api.updateAgentBackend).toHaveBeenCalledWith("claude", { accept_terms: true, select: true });
  });

  it("is the 'Use your AI subscription' section with the API-key provider as default", async () => {
    render(<AgentBackendsSection />);
    expect(await screen.findByText("Use your AI subscription")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "API key provider" })).toBeChecked();
    expect(screen.getByTestId("installed-grok")).toHaveTextContent("Installed · grok 0.1.42");
    expect(screen.getByTestId("auth-grok")).toHaveTextContent("Signed in");
    expect(screen.getByTestId("installed-codex")).toHaveTextContent("Not installed");
    expect(screen.getByRole("radio", { name: "Codex CLI" })).toBeDisabled();
    expect(screen.getByText("Opt-in")).toBeInTheDocument();
  });

  it("copies install and login commands", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<AgentBackendsSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy: npm install -g @openai/codex" }));
    expect(writeText).toHaveBeenCalledWith("npm install -g @openai/codex");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy: codex login" }));
    expect(writeText).toHaveBeenCalledWith("codex login");
    expect(screen.getByRole("button", { name: "Copy: grok login" })).toBeInTheDocument();
  });

  it("switches back to the API-key provider", async () => {
    vi.mocked(api.getAgentBackends).mockResolvedValue({ selected: "cli:grok", backends } as never);
    render(<AgentBackendsSection />);
    expect(await screen.findByRole("radio", { name: "Grok Build CLI" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "API key provider" }));
    expect(api.selectAgentBackend).toHaveBeenCalledWith("browser_use");
  });

  it("does not select Claude Code when the terms are cancelled", async () => {
    render(<AgentBackendsSection />);
    await userEvent.click(await screen.findByRole("radio", { name: "Claude Code" }));
    expect(screen.getByRole("dialog", { name: "Claude Code terms" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.updateAgentBackend).not.toHaveBeenCalledWith("claude", expect.anything());
  });

  it("shows the Grok Bot setup prompt", async () => {
    render(<AgentBackendsSection />);
    await userEvent.click(await screen.findByRole("button", { name: /Grok Bot setup prompt/ }));
    expect(await screen.findByLabelText("Setup prompt")).toHaveValue("Set up ApplyQueue on this computer.");
  });
});

describe("EmailAccountsSection", () => {
  const settings = { confirm_label: "ApplyQueue/Applied", verification_label: "ApplyQueue/Verification",
    archive_confirmations: true, archive_verification: true, auto_sweep: true, sweep_minutes: 10 };

  it("offers a one-click reconnect when Google's sign-in is about to expire", async () => {
    vi.mocked(api.getEmailStatus).mockResolvedValue({
      providers: { gmail: mailStatus({ reauth_due: true }), outlook: mailStatus({ provider: "outlook", name: "Outlook", linked: false, configured: false }) },
      settings, last_sweep: { at: null, summary: null },
    } as never);
    render(<EmailAccountsSection />);
    const alert = await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    expect(alert).toBeInTheDocument();
    expect(api.connectEmail).toHaveBeenCalledWith("gmail");
    await waitFor(() => expect(shellOpen).toHaveBeenCalledWith("https://accounts.example.com/auth?state=s"));
  });

  it("shows Reconnect for a revoked sign-in (backend reports linked=false)", async () => {
    vi.mocked(api.getEmailStatus).mockResolvedValue({
      providers: {
        gmail: mailStatus({ linked: false, configured: false }),
        outlook: mailStatus({ provider: "outlook", name: "Outlook", linked: false, needs_reauth: true, reauth_due: true, last_error: "invalid_grant" }),
      },
      settings, last_sweep: { at: null, summary: null },
    } as never);
    render(<EmailAccountsSection />);
    expect(await screen.findByText("The sign-in expired or was revoked.")).toBeInTheDocument();
    expect(screen.getByText("Reconnect needed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    expect(api.connectEmail).toHaveBeenCalledWith("outlook");
  });

  it("saves labels and runs a sweep", async () => {
    vi.mocked(api.getEmailStatus).mockResolvedValue({
      providers: { gmail: mailStatus(), outlook: mailStatus({ provider: "outlook", name: "Outlook", linked: false }) },
      settings, last_sweep: { at: null, summary: null },
    } as never);
    render(<EmailAccountsSection />);
    const label = await screen.findByLabelText("Confirmation label");
    await userEvent.clear(label);
    await userEvent.type(label, "Jobs");
    await userEvent.click(screen.getByRole("button", { name: "Save email settings" }));
    expect(api.saveEmailSettings).toHaveBeenCalledWith(expect.objectContaining({ confirm_label: "Jobs" }));
    await userEvent.click(screen.getByRole("button", { name: /Check now/ }));
    expect(await screen.findByTestId("last-sweep")).toHaveTextContent("1 confirmed, 2 code emails archived");
  });
});
