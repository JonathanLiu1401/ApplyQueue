import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LocalModelsPanel } from "./LocalModelsSection";
import * as api from "../../lib/api";

vi.mock("../../lib/api", () => ({
  getLocalModels: vi.fn(),
  pullLocalModel: vi.fn(async () => ({ success: true, job: { id: "j1", model: "qwen3-vl:8b", state: "running", status: "pulling", completed: 0, total: 10, error: "" } })),
  getPullStatus: vi.fn(async () => ({ success: true, job: { id: "j1", model: "qwen3-vl:8b", state: "done", status: "success", completed: 10, total: 10, error: "" } })),
  selectLocalModel: vi.fn(async () => ({ success: true, provider: "ollama" })),
}));

const GB = 1024 ** 3;
const rec = { id: "qwen3-vl:8b", family: "Qwen3-VL", params: "8B", size_gb: 6.2, hf: "Qwen/Qwen3-VL-8B-Instruct-GGUF", quality: "good" };
const overview = (over: Partial<api.LocalModelsOverview> = {}): api.LocalModelsOverview => ({
  runtimes: [],
  gguf: [],
  binaries: {},
  hardware: { os: "Darwin", arch: "x86_64", apple_silicon: false, intel_mac: true, ram_bytes: 32 * GB, gpus: [], budget_bytes: 16 * GB, where: "CPU" },
  recommendation: { model: rec, alternatives: [], speed: "slow", speed_note: "CPU only: leave it running overnight.", warnings: ["No supported GPU was found, so the model runs on the CPU."] },
  expectations: ["Slower than cloud AI."],
  catalog: [rec],
  current: { provider: "openrouter", ollama: {} },
  ...over,
});

describe("LocalModelsPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("is honest about CPU-only speed and asks to install Ollama when missing", async () => {
    vi.mocked(api.getLocalModels).mockResolvedValue(overview());
    render(<LocalModelsPanel />);
    expect(await screen.findByText(/Intel Mac \(CPU only\)/)).toBeTruthy();
    expect(screen.getByText(/leave it running overnight/)).toBeTruthy();
    expect(screen.getByText(/runs on the CPU/)).toBeTruthy();
    expect(screen.getByText("Ollama").getAttribute("href")).toBe("https://ollama.com/download");
    expect(screen.getByText(/llama-server -hf Qwen\/Qwen3-VL-8B-Instruct-GGUF/)).toBeTruthy();
    expect(screen.getByText("Slower than cloud AI.")).toBeTruthy();
  });

  it("downloads the recommended model with one click and selects it", async () => {
    const onUsed = vi.fn();
    vi.mocked(api.getLocalModels).mockResolvedValue(overview({
      runtimes: [{ id: "ollama", name: "Ollama", base_url: "http://127.0.0.1:11434", running: true, models: [], can_pull: true }],
    }));
    render(<LocalModelsPanel onUsed={onUsed} />);
    await userEvent.click(await screen.findByText("Download and use"));
    expect(api.pullLocalModel).toHaveBeenCalledWith("qwen3-vl:8b", "http://127.0.0.1:11434");
    await waitFor(() => expect(api.selectLocalModel).toHaveBeenCalledWith("ollama", "http://127.0.0.1:11434", "qwen3-vl:8b"), { timeout: 3000 });
    expect(onUsed).toHaveBeenCalled();
  });

  it("lists installed vision models from any runtime", async () => {
    vi.mocked(api.getLocalModels).mockResolvedValue(overview({
      runtimes: [{ id: "lmstudio", name: "LM Studio", base_url: "http://127.0.0.1:1234", running: true, can_pull: false,
        models: [{ id: "google/gemma-3-12b", vision: true }, { id: "qwen2.5-coder", vision: false }] }],
    }));
    render(<LocalModelsPanel />);
    expect(await screen.findByText(/google\/gemma-3-12b/)).toBeTruthy();
    expect(screen.queryByText(/qwen2.5-coder/)).toBeNull();
    await userEvent.click(screen.getByText("Use"));
    expect(api.selectLocalModel).toHaveBeenCalledWith("lmstudio", "http://127.0.0.1:1234", "google/gemma-3-12b");
  });
});
