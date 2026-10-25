import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SyncSection, { SYNC_WARNING } from "./SyncSection";
import * as api from "../../lib/api";

vi.mock("../../lib/api", () => ({
  getSyncStatus: vi.fn(),
  saveSyncConfig: vi.fn(async () => ({ success: true })),
  startSyncDeviceFlow: vi.fn(async () => ({ success: true, user_code: "ABCD-1234", verification_uri: "https://github.example/login/device", interval: 5 })),
  pollSyncDeviceFlow: vi.fn(async () => ({ success: true, status: "pending" })),
  saveSyncToken: vi.fn(),
  setupSyncRepo: vi.fn(async () => ({ success: true })),
  syncNow: vi.fn(async () => ({ success: true, offline: true })),
  unlinkSync: vi.fn(),
  exportSyncKey: vi.fn(),
  importSyncKey: vi.fn(),
}));
const shellOpen = vi.fn(async (url: string) => url);
vi.mock("@tauri-apps/plugin-shell", () => ({ open: (u: string) => shellOpen(u) }));

const cfg = { enabled: false, kind: "github", owner: "", repo: "", remote_url: "", client_id: "", sync_credentials: true,
  sync_sessions: true, encrypt_credentials: false, device_name: "", lock_ttl_s: 7200, auto_minutes: 5 } as api.SyncConfig;
const status = (over: Partial<api.SyncStatus> = {}): api.SyncStatus => ({
  configured: false, kind: "github", owner: "", repo: "", device: "device-1", device_label: "laptop", git: true,
  cloned: false, github_linked: false, encrypt_credentials: false, sync_credentials: true, has_key: false,
  last_sync: null, offline: false, pending: 0, visibility_error: null, verified_private_at: null, held_locks: [],
  config: cfg, ...over,
});

describe("SyncSection", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the private-repo warning and runs the device flow", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(status());
    render(<SyncSection />);
    expect(await screen.findByText(SYNC_WARNING)).toBeTruthy();
    await userEvent.type(screen.getByPlaceholderText(/Ov23li/), "Iv1.example");
    await userEvent.click(screen.getByText("Sign in with GitHub"));
    await waitFor(() => expect(screen.getByTestId("sync-user-code").textContent).toContain("ABCD-1234"));
    expect(api.saveSyncConfig).toHaveBeenCalledWith({ client_id: "Iv1.example" });
    expect(shellOpen).toHaveBeenCalledWith("https://github.example/login/device");
  });

  it("requires acknowledging privacy before creating the repo", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(status({ github_linked: true }));
    render(<SyncSection />);
    const create = await screen.findByText("Create / connect private repo");
    expect((create as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByText("I understand this repo must stay private."));
    await userEvent.click(create);
    expect(api.setupSyncRepo).toHaveBeenCalledWith("applyqueue-state");
  });

  it("shows a paused banner when the repo became public, and queues offline syncs", async () => {
    vi.mocked(api.getSyncStatus).mockResolvedValue(status({
      github_linked: true, configured: true, owner: "alex-example", repo: "applyqueue-state", pending: 2,
      visibility_error: "The state repo is public.", config: { ...cfg, enabled: true },
    }));
    render(<SyncSection />);
    expect((await screen.findByRole("alert")).textContent).toContain("public");
    expect(screen.getByText(/2 change\(s\) waiting/)).toBeTruthy();
    await userEvent.click(screen.getByText("Sync now"));
    expect(await screen.findByText("Offline: changes are queued.")).toBeTruthy();
  });
});
