/**
 * API client for communicating with the Python backend sidecar.
 * The backend runs as a local FastAPI server on a dynamic port.
 */
import { invoke } from "@tauri-apps/api/core";
import type {
  CandidateProfile,
  LLMSettings,
  AppSettings,
  Job,
  JobStats,
  MemoryStats,
  Memory,
  HealthResponse,
  RunMetric,
  CollectionStatus,
  ApplyStatus,
  DashboardResponse,
  DomainInfo,
  RunLog,
  RunWithLogs,
  QAEntry,
  QAStats,
} from "./types";

const DEFAULT_PORT = 8743;

let backendPort = DEFAULT_PORT;
let tokenPromise: Promise<string> | null = null;

export function setBackendPort(port: number) {
  backendPort = port;
}

function getBaseUrl(): string {
  return `http://127.0.0.1:${backendPort}`;
}

async function getToken(): Promise<string> {
  if (!tokenPromise) {
    tokenPromise = invoke<string>("get_api_token").catch((e) => {
      tokenPromise = null;
      throw new Error(`Failed to read API token: ${e}`);
    });
  }
  return tokenPromise;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  _retried = false
): Promise<T> {
  const url = `${getBaseUrl()}${path}`;
  const token = await getToken();
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });

  if (res.status === 401 && !_retried) {
    tokenPromise = null;
    await new Promise(r => setTimeout(r, 1000));
    return request<T>(path, options, true);
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API Error ${res.status}: ${text}`);
  }

  const text = await res.text();
  if (!text) return {} as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`Invalid JSON response from ${path}`);
  }
}

// ── Health ────────────────────────────────────────────────────────────────
export async function checkHealth() {
  return request<HealthResponse>("/health");
}

export async function getChromiumStatus() {
  const res = await fetch(`${getBaseUrl()}/chromium/status`);
  return res.json() as Promise<{ state: string; message: string }>;
}

// ── Profile ───────────────────────────────────────────────────────────────
export async function getProfile() {
  return request<CandidateProfile>("/profile");
}

export async function saveProfile(profile: CandidateProfile) {
  return request<{ success: boolean }>("/profile", {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

// ── LLM Settings ──────────────────────────────────────────────────────────
export async function getLLMSettings() {
  return request<LLMSettings>("/settings/llm");
}

export async function saveLLMSettings(settings: LLMSettings) {
  return request<{ success: boolean }>("/settings/llm", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function testLLMConnection(settings: LLMSettings) {
  return request<{ success: boolean; message: string }>("/llm/test", {
    method: "POST",
    body: JSON.stringify(settings),
  });
}

export async function fetchOllamaModels(baseUrl: string) {
  return request<{ success: boolean; models: string[]; message?: string }>("/llm/ollama-models", {
    method: "POST",
    body: JSON.stringify({ base_url: baseUrl }),
  });
}

// ── App Settings ──────────────────────────────────────────────────────────
export async function getSettings() {
  return request<AppSettings>("/settings");
}

export async function saveSettings(settings: Partial<AppSettings>) {
  return request<{ success: boolean }>("/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

// ── Jobs ──────────────────────────────────────────────────────────────────
export async function getJobs(params?: { status?: string; search?: string; limit?: number }) {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  if (params?.limit) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return request<Job[]>(`/jobs${query ? `?${query}` : ""}`);
}

export async function getJobStats() {
  return request<JobStats>("/jobs/stats");
}

/** Fetch the jobs export as CSV text (authenticated). Caller triggers the download. */
export async function exportJobsCsv(): Promise<string> {
  const token = await getToken();
  const res = await fetch(`${getBaseUrl()}/jobs/export`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`API Error ${res.status}: ${text}`);
  }
  return res.text();
}

export async function startJobCollection(title?: string, maxJobs?: number, source?: string, filters?: Record<string, string>) {
  const body: Record<string, unknown> = {};
  if (title) body.title = title;
  if (maxJobs) body.max_jobs = maxJobs;
  if (source) body.source = source;
  if (filters && Object.keys(filters).length > 0) body.filters = filters;
  return request<{ success: boolean; message: string }>("/jobs/collect", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateJobStatus(url: string, status: string) {
  return request<{ success: boolean }>("/jobs/status", {
    method: "PUT",
    body: JSON.stringify({ url, status }),
  });
}

export interface VaultSite {
  host: string;
  username: string;
  created_at: string | null;
  source: string;
}

/** Sites with an ApplyQueue-generated password (never returns passwords). */
export async function getVaultSites() {
  return request<{ sites: VaultSite[]; legacy_shared_password: boolean }>("/vault/sites");
}

export async function addJob(url: string, title?: string, company?: string, source?: string) {
  return request<{ success: boolean }>("/jobs/add", {
    method: "POST",
    body: JSON.stringify({ url, title, company, source }),
  });
}

export async function deleteJobs(urls: string[]) {
  return request<{ success: boolean; deleted: number }>("/jobs", {
    method: "DELETE",
    body: JSON.stringify({ urls }),
  });
}

export async function stopJobCollection() {
  return request<{ success: boolean }>("/jobs/collect/stop", { method: "POST" });
}

export async function getCollectionStatus() {
  return request<CollectionStatus>("/jobs/collect/status");
}

// ── Memory ────────────────────────────────────────────────────────────────
export async function getMemoryStats() {
  return request<MemoryStats>("/memory/stats");
}

export async function getMemoryDomains() {
  return request<DomainInfo[]>("/memory/domains");
}

export async function getMemoriesForDomain(domain: string) {
  return request<Memory[]>(
    `/memory/domain/${encodeURIComponent(domain)}`
  );
}

export async function searchMemories(query: string) {
  return request<Memory[]>(`/memory/search?q=${encodeURIComponent(query)}`);
}

export async function decayMemories(days = 30) {
  return request<{ success: boolean; affected: number }>("/memory/decay", {
    method: "POST",
    body: JSON.stringify({ days }),
  });
}

export async function cleanupMemories() {
  return request<{ success: boolean; deleted: number }>("/memory/cleanup", {
    method: "POST",
  });
}

export async function exportMemories() {
  return request<Memory[]>("/memory/export");
}

// ── Apply ─────────────────────────────────────────────────────────────────
export async function startApplying(params: { workers?: number; mode?: string; limit?: number; job_url?: string; job_urls?: string[] }) {
  return request<{ success: boolean; message: string }>("/apply/start", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function stopApplying() {
  return request<{ success: boolean }>("/apply/stop", { method: "POST" });
}

export async function getApplyStatus() {
  return request<ApplyStatus>("/apply/status");
}

// ── Dashboard / Metrics ───────────────────────────────────────────────────
export async function getDashboardData() {
  return request<DashboardResponse>("/dashboard");
}

export async function getMetricRuns(limit = 50) {
  return request<RunMetric[]>(`/metrics/runs?limit=${limit}`);
}

// ── Logs ─────────────────────────────────────────────────────────────────
export async function getRunLogs(runId: string, limit = 500) {
  return request<RunLog[]>(`/logs/runs/${encodeURIComponent(runId)}?limit=${limit}`);
}

export async function getRecentLogs(limit = 100) {
  return request<RunLog[]>(`/logs/recent?limit=${limit}`);
}

export async function getRunsWithLogs(limit = 50) {
  return request<RunWithLogs[]>(`/logs/runs?limit=${limit}`);
}

// ── Auth / Login Sessions ─────────────────────────────────────────────────
export async function getAuthStatus() {
  return request<{
    linkedin: { logged_in: boolean };
    gmail: { logged_in: boolean };
  }>("/auth/status");
}

export async function launchLogin(service: "linkedin" | "gmail") {
  return request<{ success: boolean; message: string }>(`/auth/login/${service}`, {
    method: "POST",
  });
}

// ── Setup / Onboarding ────────────────────────────────────────────────────
export interface SetupStatus {
  profile: boolean;
  llm: boolean;
  resume: boolean;
  chromium: boolean;
  linkedin: boolean;
  gmail: boolean;
  onboarding_completed: boolean;
  all_required_done: boolean;
}

export async function getSetupStatus() {
  return request<SetupStatus>("/setup/status");
}

export async function completeOnboarding() {
  return request<{ success: boolean }>("/setup/complete-onboarding", {
    method: "POST",
  });
}

export async function parseResumeToProfile() {
  return request<{
    success: boolean;
    message: string;
    profile?: Record<string, unknown>;
    fields_filled?: number;
  }>("/profile/parse-resume", { method: "POST" });
}

// ── Q&A Repository ────────────────────────────────────────────────────────
export async function getQAList(params?: { search?: string; unanswered?: boolean }) {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.unanswered) qs.set("unanswered", "true");
  const query = qs.toString();
  return request<QAEntry[]>(`/qa${query ? `?${query}` : ""}`);
}

export async function getQAStats() {
  return request<QAStats>("/qa/stats");
}

export async function updateQA(id: number, answer: string) {
  return request<{ success: boolean }>(`/qa/${id}`, {
    method: "PUT",
    body: JSON.stringify({ answer }),
  });
}

export async function deleteQA(id: number) {
  return request<{ success: boolean }>(`/qa/${id}`, { method: "DELETE" });
}

export async function mergeQA(sourceId: number, targetId: number) {
  return request<{ success: boolean }>(`/qa/${sourceId}/merge/${targetId}`, { method: "POST" });
}

export async function autoSquashQA() {
  return request<{ success: boolean; merged: number }>("/qa/auto-squash", { method: "POST" });
}

export async function smartSquashQA() {
  return request<{ success: boolean; merged: number }>("/qa/smart-squash", { method: "POST" });
}

// ── Resume Tailoring ─────────────────────────────────────────────────────
export async function tailorResumes(jobUrls: string[], options: import("./types").TailorOptions) {
  return request<{ success: boolean; results: import("./types").TailorResult[] }>("/resume/tailor", {
    method: "POST",
    body: JSON.stringify({ job_urls: jobUrls, options }),
  });
}

export async function refineTailoredResume(jobUrl: string, instruction: string) {
  return request<{ success: boolean; content: string; path: string }>("/resume/tailor/refine", {
    method: "POST",
    body: JSON.stringify({ job_url: jobUrl, instruction }),
  });
}

export async function getTailoredResumeContent(jobUrl: string) {
  return request<{ success: boolean; content: string; path: string | null }>("/resume/tailor/get-by-url", {
    method: "POST",
    body: JSON.stringify({ job_url: jobUrl }),
  });
}

// ── Cover Letter ─────────────────────────────────────────────────────────
export async function generateCoverLetter(jobDescription: string, jobTitle: string, company: string) {
  return request<{ success: boolean; cover_letter: string }>("/cover-letter/generate", {
    method: "POST",
    body: JSON.stringify({ job_description: jobDescription, job_title: jobTitle, company: company }),
  });
}

// ── Countries ────────────────────────────────────────────────────────────
export async function getCountries() {
  return request<{ success: boolean; countries: Record<string, import("./types").CountryConfig>; notice_period_options: string[] }>("/countries");
}

export async function getCountryConfig(code: string) {
  return request<{ success: boolean; config: import("./types").CountryConfig }>(`/countries/${code}`);
}

// ── Plugins ──────────────────────────────────────────────────────────────
export async function getPlugins(country?: string) {
  const params = country ? `?country=${country}` : "";
  return request<{ success: boolean; plugins: import("./types").PluginConfig[] }>(`/plugins${params}`);
}

export async function importPlugin(filePath: string) {
  return request<{ success: boolean; plugin: { name: string; display_name: string } }>("/plugins/import", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath }),
  });
}

export async function togglePlugin(name: string, enabled: boolean) {
  return request<{ success: boolean }>(`/plugins/${name}/toggle`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function removePlugin(name: string) {
  return request<{ success: boolean }>(`/plugins/${name}`, { method: "DELETE" });
}

export async function reloadPlugins() {
  return request<{ success: boolean; count: number }>("/plugins/reload", { method: "POST" });
}


// ── Agent backends (M3: subscription CLIs) ─────────────────────────────────
export interface AgentBackend {
  id: string;
  name: string;
  vendor: string;
  transport: string;
  runnable: boolean;
  requires_terms: boolean;
  terms_text: string | null;
  terms_accepted: boolean;
  enabled: boolean;
  selected: boolean;
  installed: boolean;
  path: string | null;
  custom_path: string;
  version: string | null;
  auth: { state: string; method?: string | null; detail?: string };
  login_command: string;
  install_hint: string;
  docs_url: string;
  login_methods: string[];
  notes: string;
}

export async function getAgentBackends() {
  return request<{ selected: string; backends: AgentBackend[] }>("/agents/backends");
}

export async function updateAgentBackend(
  id: string,
  body: { enabled?: boolean; path?: string; accept_terms?: boolean; select?: boolean },
) {
  return request<{ success: boolean; selected: string }>(`/agents/backends/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function selectAgentBackend(backend: string) {
  return request<{ success: boolean; selected: string }>("/agents/selected", {
    method: "PUT",
    body: JSON.stringify({ backend }),
  });
}

export async function getAgentSetupPrompt() {
  return request<{ prompt: string }>("/agents/setup-prompt");
}

// ── Linked mailboxes (M4) ─────────────────────────────────────────────────
export type EmailProvider = "gmail" | "outlook";

export interface EmailProviderStatus {
  provider: EmailProvider;
  name: string;
  configured: boolean;
  client_id: string;
  client_secret_set: boolean;
  tenant: string | null;
  testing_app: boolean | null;
  linked: boolean;
  email: string;
  authorized_at: number | null;
  reauth_due_at: number | null;
  needs_reauth: boolean;
  reauth_due: boolean;
  last_error: string;
  registration_redirect_uri: string;
  scopes: string[];
}

export interface EmailSettings {
  confirm_label: string;
  verification_label: string;
  archive_confirmations: boolean;
  archive_verification: boolean;
  rejection_label?: string;
  interview_label?: string;
  archive_rejections?: boolean;
  auto_sweep: boolean;
  sweep_minutes: number;
}

export interface EmailSweepSummary {
  confirmed: string[];
  labeled: number;
  verification_archived: number;
  rejections?: number;
  interviews?: number;
  errors: string[];
}

export async function getEmailStatus() {
  return request<{
    providers: Record<EmailProvider, EmailProviderStatus>;
    settings: EmailSettings;
    last_sweep: { at: number | null; summary: EmailSweepSummary | null };
  }>("/email/status");
}

export async function saveEmailClient(
  provider: EmailProvider,
  body: { client_id: string; client_secret?: string; tenant?: string; testing_app?: boolean },
) {
  return request<{ success: boolean; status: EmailProviderStatus }>(`/email/${provider}/config`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function connectEmail(provider: EmailProvider) {
  return request<{ success: boolean; url: string }>(`/email/${provider}/connect`, { method: "POST" });
}

export async function disconnectEmail(provider: EmailProvider) {
  return request<{ success: boolean }>(`/email/${provider}`, { method: "DELETE" });
}

export async function saveEmailSettings(settings: Partial<EmailSettings>) {
  return request<{ success: boolean; settings: EmailSettings }>("/email/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function sweepEmail() {
  return request<{ success: boolean } & EmailSweepSummary>("/email/sweep", { method: "POST" });
}

// ── Sync repo (M5) ─────────────────────────────────────────────────────
export interface SyncConfig {
  enabled: boolean;
  kind: "github" | "git";
  owner: string;
  repo: string;
  remote_url: string;
  client_id: string;
  sync_credentials: boolean;
  sync_sessions: boolean;
  encrypt_credentials: boolean;
  device_name: string;
  lock_ttl_s: number;
  auto_minutes: number;
}

export interface SyncStatus {
  configured: boolean;
  kind: string;
  owner: string;
  repo: string;
  device: string;
  device_label: string;
  git: boolean;
  cloned: boolean;
  github_linked: boolean;
  encrypt_credentials: boolean;
  sync_credentials: boolean;
  has_key: boolean;
  last_sync: string | null;
  offline: boolean;
  pending: number;
  visibility_error: string | null;
  verified_private_at: string | null;
  held_locks: string[];
  devices?: string[];
  config: SyncConfig;
}

export async function getSyncStatus() {
  return request<SyncStatus>("/sync/status");
}

export async function saveSyncConfig(cfg: Partial<SyncConfig>) {
  return request<{ success: boolean; config: SyncConfig }>("/sync/config", {
    method: "PUT",
    body: JSON.stringify(cfg),
  });
}

export async function startSyncDeviceFlow() {
  return request<{ success: boolean; user_code: string; verification_uri: string; interval: number }>(
    "/sync/github/device",
    { method: "POST" }
  );
}

export async function pollSyncDeviceFlow() {
  return request<{ success: boolean; status: string; error?: string }>("/sync/github/poll", { method: "POST" });
}

export async function saveSyncToken(token: string) {
  return request<{ success: boolean; login: string }>("/sync/github/token", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function setupSyncRepo(name: string, create = true) {
  return request<{ success: boolean }>("/sync/repo", { method: "POST", body: JSON.stringify({ name, create }) });
}

export async function syncNow() {
  return request<{ success: boolean; pending?: number; offline?: boolean; refused?: string }>("/sync/now", {
    method: "POST",
  });
}

export async function unlinkSync() {
  return request<{ success: boolean }>("/sync/unlink", { method: "POST" });
}

export async function exportSyncKey() {
  return request<{ success: boolean; key: string }>("/sync/key");
}

export async function importSyncKey(key: string) {
  return request<{ success: boolean; kid: string }>("/sync/key", { method: "POST", body: JSON.stringify({ key }) });
}

// ── Local models (M6) ──────────────────────────────────────────────────
export interface LocalModel {
  id: string;
  vision?: boolean;
  size_bytes?: number;
  loaded?: boolean;
}

export interface LocalRuntime {
  id: "ollama" | "lmstudio" | "llamacpp" | "openai";
  name: string;
  base_url: string;
  running: boolean;
  version?: string;
  models: LocalModel[];
  can_pull: boolean;
}

export interface CatalogModel {
  id: string;
  family: string;
  params: string;
  size_gb: number;
  hf: string;
  quality: string;
  installed?: boolean;
}

export interface LocalModelsOverview {
  runtimes: LocalRuntime[];
  gguf: { file: string; repo: string; vision: boolean; size_bytes: number; source: string }[];
  binaries: Record<string, boolean>;
  hardware: {
    os: string;
    arch: string;
    apple_silicon: boolean;
    intel_mac: boolean;
    ram_bytes: number;
    gpus: { vendor: string; name: string; vram_bytes: number }[];
    budget_bytes: number;
    where: string;
  };
  recommendation: {
    model: CatalogModel | null;
    alternatives: CatalogModel[];
    speed: "fast" | "good" | "slow";
    speed_note: string;
    warnings: string[];
  };
  expectations: string[];
  catalog: CatalogModel[];
  current: { provider: string; ollama: { base_url?: string; model?: string } };
}

export interface PullJob {
  id: string;
  model: string;
  state: "running" | "done" | "error";
  status: string;
  completed: number;
  total: number;
  error: string;
}

export async function getLocalModels() {
  return request<LocalModelsOverview>("/local-models");
}

export async function pullLocalModel(model: string, base_url?: string) {
  return request<{ success: boolean; job?: PullJob; message?: string }>("/local-models/pull", {
    method: "POST",
    body: JSON.stringify({ model, base_url }),
  });
}

export async function getPullStatus(id: string) {
  return request<{ success: boolean; job: PullJob }>(`/local-models/pull/${encodeURIComponent(id)}`);
}

export async function selectLocalModel(runtime: string, base_url: string, model: string) {
  return request<{ success: boolean; provider: string }>("/local-models/use", {
    method: "POST",
    body: JSON.stringify({ runtime, base_url, model }),
  });
}

// ── Saved searches, tracker, resumes, starter packs, outreach (M7) ────────
export interface SearchStats {
  found: number;
  review: number;
  applied: number;
  skipped: number;
  confirmed: number;
  interviews: number;
  rejections: number;
  responses: number;
  response_rate: number;
  interview_rate: number;
}

export interface SavedSearch {
  id: string;
  name: string;
  titles: string[];
  locations: string[];
  mode: "review" | "auto";
  resume_id: string | null;
  category_id: string | null;
  max_jobs: number;
  filters: Record<string, unknown>;
  bans: { companies: string[]; keywords: string[] };
  created_at?: string;
  last_run_at?: string | null;
  stats?: Partial<SearchStats>;
  schedule?: SearchSchedule;
  schedule_state?: ScheduleState;
}

export interface SearchSchedule {
  kind: "off" | "hourly" | "daily" | "custom";
  tz?: string;
  minute?: number;
  time?: string;
  cron?: string;
}

export interface ScheduleNotice {
  kind: "reauth" | "errors";
  target?: "ai" | "portal" | "mail";
  message: string;
  action: string;
}

export interface ScheduleState {
  next_run_at: string | null;
  last_run_at: string | null;
  last_outcome: string | null;
  running: boolean;
  paused: boolean;
  paused_reason: string | null;
  notice: ScheduleNotice | null;
  backoff_until: string | null;
  failures: number;
}

export interface SearchCategory {
  id: string;
  name: string;
  keywords: string[];
  resume_id?: string | null;
}

export interface SearchRules {
  categories: SearchCategory[];
  bans: { companies: string[]; title_keywords: string[]; description_keywords: string[] };
  answers: { question: string; answer: string }[];
}

export interface ResumeVariant {
  id: string;
  name: string;
  path: string;
  keywords: string[];
  default: boolean;
}

export interface OutreachSettings {
  enabled: boolean;
  template: string;
}

export interface OutreachDraft {
  id: string;
  job_url: string;
  title?: string;
  company?: string;
  body: string;
  created_at: string;
  sent: false;
}

export interface StarterPack {
  id: string;
  name: string;
  description: string;
  searches: number;
  categories: string[];
}

export interface SearchesOverview {
  searches: SavedSearch[];
  rules: SearchRules;
  resumes: ResumeVariant[];
  outreach: OutreachSettings;
  starter_packs: StarterPack[];
}

export interface TrackerRow extends SearchStats {
  id: string;
  name: string;
  mode?: string;
}

export interface TrackerJob {
  url: string;
  title?: string;
  company?: string;
  location?: string;
  search_id?: string;
  response?: string;
  at?: string;
}

export interface Tracker {
  totals: SearchStats;
  searches: TrackerRow[];
  resumes: TrackerRow[];
  unassigned: SearchStats;
  recent_responses: TrackerJob[];
  review_queue: TrackerJob[];
}

const jsonPost = (body: unknown, method = "POST"): RequestInit => ({ method, body: JSON.stringify(body) });

export async function getSearches() {
  return request<SearchesOverview>("/searches");
}

export async function saveSearch(search: Partial<SavedSearch>) {
  return request<{ success: boolean; search: SavedSearch }>("/searches", jsonPost(search));
}

export async function deleteSearch(id: string) {
  return request<{ success: boolean }>(`/searches/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function runSearch(id: string) {
  return request<{ success: boolean; message?: string }>(`/searches/${encodeURIComponent(id)}/run`, { method: "POST" });
}

type ScheduleView = ScheduleState & { id: string; name: string; schedule: SearchSchedule; cron: string | null };

export async function setSearchSchedule(id: string, schedule: SearchSchedule) {
  return request<{ success: boolean; schedule: ScheduleView }>(
    `/searches/${encodeURIComponent(id)}/schedule`,
    jsonPost(schedule, "PUT"),
  );
}

export async function pauseSearch(id: string) {
  return request<{ success: boolean; schedule: ScheduleView }>(`/searches/${encodeURIComponent(id)}/pause`, { method: "POST" });
}

export async function resumeSearch(id: string) {
  return request<{ success: boolean; schedule: ScheduleView }>(`/searches/${encodeURIComponent(id)}/resume`, { method: "POST" });
}

export async function getScheduleAgentPrompt(target: "grok" | "chatgpt" | "claude" | "codex" | "cursor") {
  return request<{ prompt: string }>(`/schedules/agent-prompt?target=${encodeURIComponent(target)}`);
}

export async function quickStartSearch(body: { resume_path?: string; titles: string[]; locations: string[]; start: boolean }) {
  return request<{ success: boolean; search: SavedSearch; started?: { success: boolean; message?: string } | null }>(
    "/searches/quickstart",
    jsonPost(body),
  );
}

export async function saveSearchRules(rules: Partial<SearchRules>) {
  return request<{ success: boolean; rules: SearchRules }>("/searches/rules", jsonPost(rules, "PUT"));
}

export async function getTracker() {
  return request<Tracker>("/tracker");
}

export async function reviewJobs(urls: string[], approve: boolean) {
  return request<{ success: boolean; updated: number }>("/jobs/review", jsonPost({ urls, approve }));
}

export async function addResume(body: { path: string; name?: string; keywords?: string[]; default?: boolean }) {
  return request<{ success: boolean; resume: ResumeVariant }>("/resumes", jsonPost(body));
}

export async function deleteResume(id: string) {
  return request<{ success: boolean }>(`/resumes/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function atsCheck(body: { resume_id: string; description?: string; job_url?: string }) {
  return request<{ success: boolean; score: number; matched: string[]; missing: string[] }>(
    "/resumes/ats-check",
    jsonPost(body),
  );
}

export async function applyStarterPack(id: string) {
  return request<{ success: boolean; searches_added: number; categories: number }>(
    `/starter-packs/${encodeURIComponent(id)}/apply`,
    { method: "POST" },
  );
}

export async function saveOutreach(body: Partial<OutreachSettings>) {
  return request<{ success: boolean; outreach: OutreachSettings }>("/outreach", jsonPost(body, "PUT"));
}

export async function getOutreachDrafts() {
  return request<OutreachDraft[]>("/outreach/drafts");
}

export async function createOutreachDraft(job_url: string) {
  return request<{ success: boolean; draft: OutreachDraft }>("/outreach/draft", jsonPost({ job_url }));
}

export async function deleteOutreachDraft(id: string) {
  return request<{ success: boolean }>(`/outreach/drafts/${encodeURIComponent(id)}`, { method: "DELETE" });
}
