import { useEffect, useRef, useState } from "react";
import { Cpu, Download, Info } from "lucide-react";
import { Section } from "../ui";
import {
  getLocalModels,
  getPullStatus,
  pullLocalModel,
  selectLocalModel,
  type LocalModelsOverview,
  type PullJob,
} from "../../lib/api";

const btn = "px-3 py-1.5 text-sm rounded border border-border hover:bg-secondary disabled:opacity-50";
const primary = "px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground disabled:opacity-50";

function gb(bytes: number) {
  return `${(bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1)} GB`;
}

function errText(e: unknown) {
  return e instanceof Error ? e.message.replace(/^API Error \d+: /, "") : "Request failed";
}

/** Detection + recommendation + one-click download. Used in Settings and in onboarding. */
export function LocalModelsPanel({ onUsed }: { onUsed?: () => void }) {
  const [data, setData] = useState<LocalModelsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [job, setJob] = useState<PullJob | null>(null);
  const timer = useRef<number | null>(null);

  const load = () =>
    getLocalModels()
      .then(setData)
      .catch(() => setError("Could not check for local models."));

  useEffect(() => {
    load();
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  const ollama = data?.runtimes.find((r) => r.id === "ollama");
  const rec = data?.recommendation;

  const choose = async (runtime: string, base: string, model: string) => {
    setError(null);
    try {
      await selectLocalModel(runtime, base, model);
      setNote(`ApplyQueue will use ${model} on this computer.`);
      onUsed?.();
      load();
    } catch (e) {
      setError(errText(e));
    }
  };

  const download = async (model: string) => {
    setError(null);
    try {
      const r = await pullLocalModel(model, ollama?.base_url);
      if (!r.success || !r.job) {
        setError(r.message || "Download failed to start.");
        return;
      }
      setJob(r.job);
      const id = r.job.id;
      if (timer.current) window.clearInterval(timer.current);
      timer.current = window.setInterval(async () => {
        try {
          const s = await getPullStatus(id);
          setJob(s.job);
          if (s.job.state !== "running") {
            if (timer.current) window.clearInterval(timer.current);
            if (s.job.state === "done" && ollama) await choose("ollama", ollama.base_url, model);
            if (s.job.state === "error") setError(s.job.error);
          }
        } catch (e) {
          if (timer.current) window.clearInterval(timer.current);
          setError(errText(e));
        }
      }, 1000);
    } catch (e) {
      setError(errText(e));
    }
  };

  if (!data) {
    return <p className="text-[13px] text-muted-foreground">{error || "Checking this computer…"}</p>;
  }

  const hw = data.hardware;
  const pct = job && job.total ? Math.round((job.completed / job.total) * 100) : 0;
  const visionModels = data.runtimes.flatMap((r) => r.models.filter((m) => m.vision).map((m) => ({ r, m })));

  return (
    <div className="space-y-3 text-[13px]" data-testid="local-models">
      <div className="p-3 rounded-lg bg-secondary">
        <div className="flex items-center gap-2 font-semibold mb-1">
          <Cpu className="w-4 h-4" /> This computer
        </div>
        <p>
          {gb(hw.ram_bytes)} RAM
          {hw.gpus.length ? ` · ${hw.gpus.map((g) => `${g.name} ${gb(g.vram_bytes)}`).join(", ")}` : ""}
          {hw.apple_silicon ? " · Apple silicon" : ""}
          {hw.intel_mac ? " · Intel Mac (CPU only)" : ""} · models run on the {hw.where}
        </p>
      </div>

      {rec?.model ? (
        <div className="p-3 rounded-lg border border-border" data-testid="local-recommendation">
          <p className="font-semibold">
            Recommended: {rec.model.family} {rec.model.params}{" "}
            <span className="font-mono text-muted-foreground">({rec.model.id}, {rec.model.size_gb} GB download)</span>
          </p>
          <p className="text-muted-foreground mt-1">{rec.speed_note}</p>
          <div className="flex flex-wrap gap-2 mt-2">
            {ollama ? (
              rec.model.installed ? (
                <button className={primary} onClick={() => choose("ollama", ollama.base_url, rec.model!.id)}>
                  Use {rec.model.id}
                </button>
              ) : (
                <button className={primary} disabled={job?.state === "running"} onClick={() => download(rec.model!.id)}>
                  <Download className="w-3.5 h-3.5 inline mr-1" />
                  Download and use
                </button>
              )
            ) : (
              <span>
                Install{" "}
                <a className="underline" href="https://ollama.com/download" target="_blank" rel="noreferrer">Ollama</a>{" "}
                (free), open it, then{" "}
                <button className="underline" onClick={load}>check again</button>. With llama.cpp:{" "}
                <span className="font-mono">llama-server -hf {rec.model.hf}</span>
              </span>
            )}
          </div>
          {rec.alternatives.length > 0 && (
            <p className="mt-2 text-muted-foreground">
              Also fits: {rec.alternatives.map((a) => `${a.id} (${a.size_gb} GB)`).join(", ")}
            </p>
          )}
          {job && (
            <div className="mt-2" data-testid="pull-progress">
              <div className="h-2 rounded bg-secondary overflow-hidden">
                <div className="h-2 bg-primary" style={{ width: `${job.state === "done" ? 100 : pct}%` }} />
              </div>
              <p className="mt-1">{job.state === "done" ? "Downloaded." : `${job.status} ${pct ? `${pct}%` : ""}`}</p>
            </div>
          )}
        </div>
      ) : null}

      {rec?.warnings.map((w) => (
        <p key={w} className="text-amber-700">{w}</p>
      ))}

      {visionModels.length > 0 && (
        <div>
          <p className="font-semibold mb-1">Vision models already on this computer</p>
          <ul className="space-y-1">
            {visionModels.map(({ r, m }) => (
              <li key={`${r.id}-${m.id}`} className="flex items-center justify-between">
                <span>
                  {m.id} <span className="text-muted-foreground">· {r.name}</span>
                </span>
                <button className={btn} onClick={() => choose(r.id, r.base_url, m.id)}>Use</button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.runtimes.length > 0 && visionModels.length === 0 && (
        <p className="text-muted-foreground">
          Found {data.runtimes.map((r) => r.name).join(", ")}, but no vision model. ApplyQueue reads screenshots, so it
          needs one.
        </p>
      )}
      {data.gguf.some((g) => g.vision) && (
        <p className="text-muted-foreground">
          GGUF vision models found in your Hugging Face / LM Studio downloads:{" "}
          {data.gguf.filter((g) => g.vision).map((g) => g.repo || g.file).join(", ")}. Load one in LM Studio or
          llama-server and it will appear above.
        </p>
      )}

      <div className="p-3 rounded-lg bg-blue-500/5">
        <div className="flex items-center gap-2 font-semibold mb-1">
          <Info className="w-4 h-4" /> What to expect
        </div>
        <ul className="list-disc ml-5 space-y-0.5">
          {data.expectations.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      </div>
      {note && <p className="text-green-700">{note}</p>}
      {error && <p className="text-red-600" role="alert">{error}</p>}
    </div>
  );
}

export default function LocalModelsSection() {
  return (
    <Section title="Local models (free, no subscription)">
      <LocalModelsPanel />
    </Section>
  );
}
