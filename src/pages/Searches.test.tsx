import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Searches from "./Searches";
import * as api from "../lib/api";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn(async () => null) }));
vi.mock("../lib/api", () => ({
  getSearches: vi.fn(),
  getTracker: vi.fn(),
  saveSearch: vi.fn(async () => ({ success: true })),
  deleteSearch: vi.fn(async () => ({ success: true })),
  runSearch: vi.fn(async () => ({ success: true, message: "Collecting" })),
  quickStartSearch: vi.fn(async () => ({ success: true, search: { name: "Data Analyst" }, started: { success: true, message: "Collecting Data Analyst" } })),
  saveSearchRules: vi.fn(async () => ({ success: true })),
  reviewJobs: vi.fn(async () => ({ success: true, updated: 1 })),
  addResume: vi.fn(async () => ({ success: true })),
  deleteResume: vi.fn(async () => ({ success: true })),
  atsCheck: vi.fn(async () => ({ success: true, score: 50, matched: ["python"], missing: ["kubernetes"] })),
  applyStarterPack: vi.fn(async () => ({ success: true, searches_added: 2, categories: 2 })),
  saveOutreach: vi.fn(async () => ({ success: true })),
  getOutreachDrafts: vi.fn(async () => []),
  createOutreachDraft: vi.fn(async () => ({ success: true })),
  deleteOutreachDraft: vi.fn(async () => ({ success: true })),
  setSearchSchedule: vi.fn(async () => ({ success: true })),
  pauseSearch: vi.fn(async () => ({ success: true })),
  resumeSearch: vi.fn(async () => ({ success: true })),
  getScheduleAgentPrompt: vi.fn(async () => ({ prompt: "Please run my ApplyQueue saved-search schedules" })),
}));

const zero = { found: 0, review: 0, applied: 0, skipped: 0, confirmed: 0, interviews: 0, rejections: 0, responses: 0, response_rate: 0, interview_rate: 0 };
const search: api.SavedSearch = {
  id: "s1", name: "Data roles", titles: ["Data Analyst"], locations: ["Remote"], mode: "review", resume_id: null,
  category_id: null, max_jobs: 25, filters: {}, bans: { companies: [], keywords: [] },
  stats: { ...zero, found: 7, applied: 3, interviews: 1 },
};
const overview = (over: Partial<api.SearchesOverview> = {}): api.SearchesOverview => ({
  searches: [search],
  rules: { categories: [], bans: { companies: [], title_keywords: [], description_keywords: [] }, answers: [] },
  resumes: [
    { id: "rA", name: "General", path: "/tmp/a.pdf", keywords: [], default: true },
    { id: "rB", name: "Data", path: "/tmp/b.pdf", keywords: ["sql"], default: false },
  ],
  outreach: { enabled: false, template: "Hi {recruiter}" },
  starter_packs: [{ id: "retail", name: "Retail", description: "Store roles", searches: 2, categories: ["Store"] }],
  ...over,
});
const tracker: api.Tracker = {
  totals: { ...zero, found: 7, applied: 3, interviews: 1, responses: 2, response_rate: 0.667, interview_rate: 0.333 },
  searches: [],
  resumes: [
    { ...zero, id: "rA", name: "General", applied: 2, interviews: 0, interview_rate: 0 },
    { ...zero, id: "rB", name: "Data", applied: 1, interviews: 1, interview_rate: 1 },
  ],
  unassigned: zero,
  recent_responses: [{ url: "u9", title: "BI Analyst", company: "Acme Widgets", response: "interview", at: "2026-01-02" }],
  review_queue: [{ url: "u1", title: "Junior Analyst", company: "Acme Widgets", search_id: "s1" }],
};

describe("Searches page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSearches).mockResolvedValue(overview());
    vi.mocked(api.getTracker).mockResolvedValue(tracker);
  });

  it("runs the 3-step quick start", async () => {
    render(<Searches />);
    const go = screen.getByTestId("quick-start-go");
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("CV path"), "/tmp/cv.pdf");
    await userEvent.type(screen.getByLabelText("Job titles"), "Data Analyst, BI Analyst");
    await userEvent.type(screen.getByLabelText("Locations"), "Remote");
    await userEvent.click(go);
    expect(api.quickStartSearch).toHaveBeenCalledWith({
      resume_path: "/tmp/cv.pdf", titles: ["Data Analyst", "BI Analyst"], locations: ["Remote"], start: true,
    });
    expect(await screen.findByText("Collecting Data Analyst")).toBeTruthy();
  });

  it("shows per-search stats, the tracker, resume A/B and the review queue", async () => {
    render(<Searches />);
    expect(await screen.findByTestId("search-s1")).toBeTruthy();
    expect(screen.getByTestId("resume-ab").textContent).toContain("100%");
    expect(screen.getByText(/Response rate 67%/)).toBeTruthy();
    await userEvent.click(screen.getByLabelText("Approve Junior Analyst"));
    expect(api.reviewJobs).toHaveBeenCalledWith(["u1"], true);
    await userEvent.click(screen.getByLabelText("Skip Junior Analyst"));
    expect(api.reviewJobs).toHaveBeenCalledWith(["u1"], false);
  });

  it("switches mode, runs a search and adds a starter pack", async () => {
    render(<Searches />);
    await screen.findByTestId("search-s1");
    await userEvent.selectOptions(screen.getByLabelText("Mode for Data roles"), "auto");
    expect(api.saveSearch).toHaveBeenCalledWith({ id: "s1", mode: "auto" });
    await userEvent.click(screen.getByRole("button", { name: /Run/ }));
    expect(api.runSearch).toHaveBeenCalledWith("s1");
    await userEvent.click(screen.getByRole("button", { name: "Add pack" }));
    expect(api.applyStarterPack).toHaveBeenCalledWith("retail");
    expect(await screen.findByText(/Added 2 searches/)).toBeTruthy();
  });

  it("checks ATS keywords and saves bans", async () => {
    render(<Searches />);
    await screen.findByTestId("resumes");
    await userEvent.selectOptions(screen.getByLabelText("Resume to check"), "rB");
    await userEvent.type(screen.getByLabelText("Job description"), "Python and Kubernetes");
    await userEvent.click(screen.getByRole("button", { name: "Check keywords" }));
    expect((await screen.findByTestId("ats-result")).textContent).toContain("Missing: kubernetes");
    await userEvent.type(screen.getByLabelText("Banned companies"), "Example Staffing Agency");
    await userEvent.click(screen.getByRole("button", { name: "Save rules" }));
    await waitFor(() => expect(api.saveSearchRules).toHaveBeenCalled());
    expect(vi.mocked(api.saveSearchRules).mock.calls[0][0].bans?.companies).toEqual(["Example Staffing Agency"]);
  });

  it("keeps outreach off by default and never sends", async () => {
    render(<Searches />);
    const box = await screen.findByLabelText("Enable outreach drafts");
    expect(box).not.toBeChecked();
    expect(screen.getByText(/never sends recruiter messages/)).toBeTruthy();
    expect(api.getOutreachDrafts).not.toHaveBeenCalled();
    await userEvent.click(box);
    expect(api.saveOutreach).toHaveBeenCalledWith({ enabled: true });
  });

  it("sets a daily schedule in local time and shows the next run", async () => {
    render(<Searches />);
    const sel = await screen.findByLabelText("Schedule for Data roles");
    await userEvent.selectOptions(sel, "daily");
    await waitFor(() =>
      expect(api.setSearchSchedule).toHaveBeenCalledWith("s1", expect.objectContaining({ kind: "daily", time: "09:00" })),
    );
    const arg = vi.mocked(api.setSearchSchedule).mock.calls[0][1];
    expect(arg.tz).toBeTruthy();
  });

  it("shows the re-auth notice with a sign-in link and resumes", async () => {
    vi.mocked(api.getSearches).mockResolvedValue(overview({
      searches: [{
        ...search,
        schedule: { kind: "hourly", minute: 0, tz: "UTC" },
        schedule_state: {
          next_run_at: null, last_run_at: null, last_outcome: "auth", running: false, paused: true,
          paused_reason: "login_expired", backoff_until: null, failures: 0,
          notice: { kind: "reauth", target: "ai", message: "Your AI sign-in or API key stopped working.", action: "/llm" },
        },
      }],
    }));
    render(<Searches />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("AI sign-in");
    expect(screen.getByText("Sign in again").getAttribute("href")).toBe("#/llm");
    await userEvent.click(screen.getAllByText("Resume")[0]);
    expect(api.resumeSearch).toHaveBeenCalledWith("s1");
    await userEvent.click(screen.getByText("Show prompt"));
    expect(await screen.findByText(/saved-search schedules/)).toBeInTheDocument();
    expect(api.getScheduleAgentPrompt).toHaveBeenCalledWith("grok");
  });

  it("pauses an active schedule and saves a custom cron", async () => {
    vi.mocked(api.getSearches).mockResolvedValue(overview({
      searches: [{ ...search, schedule: { kind: "custom", cron: "0 9 * * 1-5", tz: "UTC" },
        schedule_state: { next_run_at: "2026-03-02T09:00:00+00:00", last_run_at: null, last_outcome: null, running: false,
          paused: false, paused_reason: null, notice: null, backoff_until: null, failures: 0 } }],
    }));
    render(<Searches />);
    expect(await screen.findByText(/Next:/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Pause"));
    expect(api.pauseSearch).toHaveBeenCalledWith("s1");
    const cron = screen.getByLabelText("Cron for Data roles");
    await userEvent.clear(cron);
    await userEvent.type(cron, "30 7 * * *");
    await userEvent.click(screen.getByText("Save"));
    expect(api.setSearchSchedule).toHaveBeenCalledWith("s1", expect.objectContaining({ kind: "custom", cron: "30 7 * * *" }));
  });
});
