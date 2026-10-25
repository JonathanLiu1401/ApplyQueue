import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Play, Trash2, Check, X, FileText, Package, Mail, Ban } from "lucide-react";
import {
  getSearches,
  getTracker,
  saveSearch,
  deleteSearch,
  runSearch,
  quickStartSearch,
  saveSearchRules,
  reviewJobs,
  addResume,
  deleteResume,
  atsCheck,
  applyStarterPack,
  saveOutreach,
  getOutreachDrafts,
  createOutreachDraft,
  deleteOutreachDraft,
  type SearchesOverview,
  type Tracker,
  type SavedSearch,
  type SearchRules,
  type SearchStats,
  type OutreachDraft,
} from "../lib/api";
import { PageHeader, Section } from "../components/ui";

const input = "w-full px-2 py-1.5 text-sm rounded border border-border bg-background";
const btn = "px-3 py-1.5 text-sm rounded border border-border hover:bg-secondary disabled:opacity-50";
const primary = "px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground disabled:opacity-50";

const splitList = (s: string) =>
  s
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
const pct = (n: number | undefined) => `${Math.round((n || 0) * 100)}%`;
const errText = (e: unknown) => String(e instanceof Error ? e.message : e).replace(/^API Error \d+: /, "");

async function browseResume(): Promise<string> {
  try {
    const f = await open({ filters: [{ name: "Resume", extensions: ["pdf", "docx", "doc", "txt", "md"] }] });
    return typeof f === "string" ? f : "";
  } catch {
    return "";
  }
}

function QuickStart({ onDone }: { onDone: (msg: string) => void }) {
  const [cv, setCv] = useState("");
  const [titles, setTitles] = useState("");
  const [locations, setLocations] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await quickStartSearch({ resume_path: cv || undefined, titles: splitList(titles), locations: splitList(locations), start: true });
      onDone(r.started?.message || `Started "${r.search.name}". New jobs wait for your review before ApplyQueue applies.`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Quick start">
      <ol className="space-y-3 text-sm" data-testid="quick-start">
        <li>
          <span className="font-semibold">1. Your CV</span>
          <div className="flex gap-2 mt-1">
            <input className={input} aria-label="CV path" value={cv} onChange={(e) => setCv(e.target.value)} placeholder="Path to your resume (PDF, DOCX, TXT)" />
            <button className={btn} onClick={async () => setCv((await browseResume()) || cv)}>Browse</button>
          </div>
        </li>
        <li>
          <span className="font-semibold">2. Job titles and locations</span>
          <div className="grid grid-cols-2 gap-2 mt-1">
            <input className={input} aria-label="Job titles" value={titles} onChange={(e) => setTitles(e.target.value)} placeholder="Data Analyst, BI Analyst" />
            <input className={input} aria-label="Locations" value={locations} onChange={(e) => setLocations(e.target.value)} placeholder="Remote, Springfield" />
          </div>
        </li>
        <li>
          <span className="font-semibold">3. Start</span>
          <div className="mt-1 flex items-center gap-3">
            <button className={primary} disabled={busy || !splitList(titles).length} onClick={start} data-testid="quick-start-go">
              <Play className="w-3.5 h-3.5 inline mr-1" />
              Start searching
            </button>
            <span className="text-[13px] text-muted-foreground">Review-first: you approve each job before anything is submitted.</span>
          </div>
        </li>
      </ol>
      {error && <p className="text-[13px] text-red-600 mt-2" role="alert">{error}</p>}
    </Section>
  );
}

function StatLine({ s }: { s?: Partial<SearchStats> }) {
  const items: [string, number | undefined][] = [
    ["found", s?.found],
    ["to review", s?.review],
    ["applied", s?.applied],
    ["skipped", s?.skipped],
    ["confirmed", s?.confirmed],
    ["interviews", s?.interviews],
  ];
  return (
    <div className="flex flex-wrap gap-3 text-[13px] text-muted-foreground">
      {items.map(([k, v]) => (
        <span key={k}>
          <span className="font-semibold text-foreground">{v ?? 0}</span> {k}
        </span>
      ))}
    </div>
  );
}

function SearchCard({ s, resumes, onChange, onMessage }: {
  s: SavedSearch;
  resumes: SearchesOverview["resumes"];
  onChange: () => void;
  onMessage: (m: string) => void;
}) {
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      onChange();
    } catch (e) {
      onMessage(errText(e));
    }
  };
  return (
    <div className="p-3 rounded-lg border border-border space-y-2" data-testid={`search-${s.id}`}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="font-semibold text-sm">{s.name}</div>
          <div className="text-[13px] text-muted-foreground">
            {s.titles.join(", ")}
            {s.locations.length ? ` · ${s.locations.join(", ")}` : ""}
          </div>
        </div>
        <div className="flex gap-2 items-center">
          <select
            className={input + " w-auto"}
            aria-label={`Mode for ${s.name}`}
            value={s.mode}
            onChange={(e) => act(() => saveSearch({ id: s.id, mode: e.target.value as SavedSearch["mode"] }))}
          >
            <option value="review">Review first</option>
            <option value="auto">Auto-apply</option>
          </select>
          <select
            className={input + " w-auto"}
            aria-label={`Resume for ${s.name}`}
            value={s.resume_id || ""}
            onChange={(e) => act(() => saveSearch({ id: s.id, resume_id: e.target.value || null }))}
          >
            <option value="">Resume: automatic</option>
            {resumes.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
          <button className={primary} onClick={() => act(async () => onMessage((await runSearch(s.id)).message || "Started"))}>
            <Play className="w-3.5 h-3.5 inline mr-1" />
            Run
          </button>
          <button className={btn} aria-label={`Delete ${s.name}`} onClick={() => act(() => deleteSearch(s.id))}>
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      <StatLine s={s.stats} />
    </div>
  );
}

function TrackerPanel({ tracker, searches, onChange }: { tracker: Tracker; searches: SavedSearch[]; onChange: () => void }) {
  const name = (id?: string) => searches.find((s) => s.id === id)?.name || "";
  const review = async (url: string, approve: boolean) => {
    await reviewJobs([url], approve);
    onChange();
  };
  return (
    <Section title="Tracker">
      <div className="space-y-4" data-testid="tracker">
        <StatLine s={tracker.totals} />
        <p className="text-[13px] text-muted-foreground">
          Response rate {pct(tracker.totals.response_rate)} · interview rate {pct(tracker.totals.interview_rate)}
        </p>
        {tracker.review_queue.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold mb-1">Waiting for your review ({tracker.review_queue.length})</h4>
            <ul className="space-y-1 text-[13px]">
              {tracker.review_queue.slice(0, 50).map((j) => (
                <li key={j.url} className="flex items-center gap-2">
                  <span className="flex-1 truncate">
                    {j.title} · {j.company} <span className="text-muted-foreground">{name(j.search_id)}</span>
                  </span>
                  <button className={btn} aria-label={`Approve ${j.title}`} onClick={() => review(j.url, true)}>
                    <Check className="w-3.5 h-3.5" />
                  </button>
                  <button className={btn} aria-label={`Skip ${j.title}`} onClick={() => review(j.url, false)}>
                    <X className="w-3.5 h-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {tracker.resumes.length > 1 && (
          <div>
            <h4 className="text-sm font-semibold mb-1">Resume A/B</h4>
            <table className="text-[13px] w-full" data-testid="resume-ab">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th>Resume</th><th>Applied</th><th>Responses</th><th>Interviews</th><th>Interview rate</th>
                </tr>
              </thead>
              <tbody>
                {tracker.resumes.map((r) => (
                  <tr key={r.id}>
                    <td>{r.name}</td><td>{r.applied}</td><td>{r.responses}</td><td>{r.interviews}</td><td>{pct(r.interview_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tracker.recent_responses.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold mb-1">Recent replies</h4>
            <ul className="text-[13px] space-y-0.5">
              {tracker.recent_responses.map((r) => (
                <li key={r.url}>
                  <span className={r.response === "interview" ? "text-green-700" : "text-muted-foreground"}>{r.response}</span> · {r.title} · {r.company}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Section>
  );
}

function ResumesPanel({ data, onChange }: { data: SearchesOverview; onChange: () => void }) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [keywords, setKeywords] = useState("");
  const [jd, setJd] = useState("");
  const [atsFor, setAtsFor] = useState("");
  const [ats, setAts] = useState<{ score: number; matched: string[]; missing: string[] } | null>(null);
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    }
  };
  return (
    <Section title="Resumes">
      <div className="space-y-3 text-sm" data-testid="resumes">
        <p className="text-[13px] text-muted-foreground">
          Each job gets a resume in this order: the search's resume, its category's resume, the resume whose keywords match, then the default.
        </p>
        <ul className="space-y-1">
          {data.resumes.map((r) => (
            <li key={r.id} className="flex items-center gap-2 text-[13px]">
              <FileText className="w-3.5 h-3.5" />
              <span className="flex-1">
                {r.name} {r.default && <span className="text-muted-foreground">(default)</span>}
                {r.keywords.length > 0 && <span className="text-muted-foreground"> · {r.keywords.join(", ")}</span>}
              </span>
              <button className={btn} aria-label={`Remove ${r.name}`} onClick={() => run(async () => { await deleteResume(r.id); onChange(); })}>
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </li>
          ))}
        </ul>
        <div className="grid grid-cols-3 gap-2">
          <div className="flex gap-1">
            <input className={input} aria-label="Resume path" value={path} onChange={(e) => setPath(e.target.value)} placeholder="Resume file path" />
            <button className={btn} onClick={async () => setPath((await browseResume()) || path)}>…</button>
          </div>
          <input className={input} aria-label="Resume name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Data version" />
          <input className={input} aria-label="Resume keywords" value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="Route keywords: sql, tableau" />
        </div>
        <button
          className={btn}
          disabled={!path.trim()}
          onClick={() => run(async () => {
            await addResume({ path: path.trim(), name: name.trim() || undefined, keywords: splitList(keywords) });
            setPath(""); setName(""); setKeywords("");
            onChange();
          })}
        >
          Add resume
        </button>
        {data.resumes.length > 0 && (
          <div className="space-y-2 pt-2 border-t border-border">
            <h4 className="font-semibold">ATS keyword check</h4>
            <select className={input} aria-label="Resume to check" value={atsFor} onChange={(e) => setAtsFor(e.target.value)}>
              <option value="">Pick a resume</option>
              {data.resumes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
            <textarea className={input} rows={4} aria-label="Job description" value={jd} onChange={(e) => setJd(e.target.value)} placeholder="Paste a job description" />
            <button className={btn} disabled={!atsFor || !jd.trim()} onClick={() => run(async () => setAts(await atsCheck({ resume_id: atsFor, description: jd })))}>
              Check keywords
            </button>
            {ats && (
              <div className="text-[13px]" data-testid="ats-result">
                <p><span className="font-semibold">{ats.score}%</span> of the posting's top keywords appear in this resume.</p>
                {ats.missing.length > 0 && <p className="text-amber-700">Missing: {ats.missing.join(", ")}</p>}
                {ats.matched.length > 0 && <p className="text-muted-foreground">Matched: {ats.matched.join(", ")}</p>}
              </div>
            )}
          </div>
        )}
        {error && <p className="text-[13px] text-red-600" role="alert">{error}</p>}
      </div>
    </Section>
  );
}

function RulesPanel({ rules, resumes, onChange }: { rules: SearchRules; resumes: SearchesOverview["resumes"]; onChange: () => void }) {
  const [cats, setCats] = useState(rules.categories.map((c) => ({ ...c, kw: c.keywords.join(", ") })));
  const [companies, setCompanies] = useState(rules.bans.companies.join(", "));
  const [titleKw, setTitleKw] = useState(rules.bans.title_keywords.join(", "));
  const [descKw, setDescKw] = useState(rules.bans.description_keywords.join(", "));
  const [answers, setAnswers] = useState(rules.answers);
  const [saved, setSaved] = useState("");
  const save = async () => {
    try {
      await saveSearchRules({
        categories: cats.map((c) => ({ id: c.id, name: c.name, keywords: splitList(c.kw), resume_id: c.resume_id || null })),
        bans: { companies: splitList(companies), title_keywords: splitList(titleKw), description_keywords: splitList(descKw) },
        answers: answers.filter((a) => a.question.trim() && a.answer.trim()),
      });
      setSaved("Saved");
      onChange();
    } catch (e) {
      setSaved(errText(e));
    }
  };
  return (
    <Section title="Your rules: categories, bans and answers">
      <div className="space-y-3 text-sm" data-testid="rules">
        <div>
          <h4 className="font-semibold mb-1">Categories</h4>
          {cats.map((c, i) => (
            <div key={c.id || i} className="grid grid-cols-3 gap-2 mb-1">
              <input className={input} aria-label="Category name" value={c.name} onChange={(e) => setCats(cats.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <input className={input} aria-label="Category keywords" value={c.kw} placeholder="keywords" onChange={(e) => setCats(cats.map((x, j) => (j === i ? { ...x, kw: e.target.value } : x)))} />
              <select className={input} aria-label="Category resume" value={c.resume_id || ""} onChange={(e) => setCats(cats.map((x, j) => (j === i ? { ...x, resume_id: e.target.value || null } : x)))}>
                <option value="">No specific resume</option>
                {resumes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
          ))}
          <button className={btn} onClick={() => setCats([...cats, { id: "", name: "", keywords: [], kw: "", resume_id: null }])}>Add category</button>
        </div>
        <div className="space-y-1">
          <h4 className="font-semibold flex items-center gap-1"><Ban className="w-3.5 h-3.5" /> Never apply to</h4>
          <input className={input} aria-label="Banned companies" value={companies} onChange={(e) => setCompanies(e.target.value)} placeholder="Companies" />
          <input className={input} aria-label="Banned title words" value={titleKw} onChange={(e) => setTitleKw(e.target.value)} placeholder="Words in the title, e.g. senior, director" />
          <input className={input} aria-label="Banned description words" value={descKw} onChange={(e) => setDescKw(e.target.value)} placeholder="Words in the description, e.g. unpaid, commission only" />
        </div>
        <div>
          <h4 className="font-semibold mb-1">Saved answers</h4>
          {answers.map((a, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 mb-1">
              <input className={input} aria-label="Question" value={a.question} onChange={(e) => setAnswers(answers.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)))} />
              <input className={input} aria-label="Answer" value={a.answer} onChange={(e) => setAnswers(answers.map((x, j) => (j === i ? { ...x, answer: e.target.value } : x)))} />
            </div>
          ))}
          <button className={btn} onClick={() => setAnswers([...answers, { question: "", answer: "" }])}>Add answer</button>
        </div>
        <div className="flex items-center gap-2">
          <button className={primary} onClick={save}>Save rules</button>
          {saved && <span className="text-[13px] text-muted-foreground">{saved}</span>}
        </div>
      </div>
    </Section>
  );
}

function OutreachPanel({ data, onChange }: { data: SearchesOverview; onChange: () => void }) {
  const [drafts, setDrafts] = useState<OutreachDraft[]>([]);
  const [template, setTemplate] = useState(data.outreach.template);
  const [jobUrl, setJobUrl] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(() => getOutreachDrafts().then(setDrafts).catch(() => setDrafts([])), []);
  useEffect(() => {
    if (data.outreach.enabled) refresh();
  }, [data.outreach.enabled, refresh]);
  const run = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    }
  };
  return (
    <Section title="Recruiter outreach (drafts only)">
      <div className="space-y-2 text-sm" data-testid="outreach">
        <p className="text-[13px] text-muted-foreground">
          <Mail className="w-3.5 h-3.5 inline mr-1" />
          Off by default. ApplyQueue only writes drafts; it never sends recruiter messages. Copy a draft and send it yourself.
        </p>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            aria-label="Enable outreach drafts"
            checked={data.outreach.enabled}
            onChange={(e) => run(async () => { await saveOutreach({ enabled: e.target.checked }); onChange(); })}
          />
          Write outreach drafts
        </label>
        {data.outreach.enabled && (
          <>
            <textarea className={input} rows={4} aria-label="Outreach template" value={template} onChange={(e) => setTemplate(e.target.value)} />
            <p className="text-[12px] text-muted-foreground">Placeholders: {"{recruiter} {title} {company} {pitch} {name}"}</p>
            <button className={btn} onClick={() => run(async () => { await saveOutreach({ template }); onChange(); })}>Save template</button>
            <div className="flex gap-2">
              <input className={input} aria-label="Job URL for draft" value={jobUrl} onChange={(e) => setJobUrl(e.target.value)} placeholder="Job URL from your list" />
              <button className={btn} disabled={!jobUrl.trim()} onClick={() => run(async () => { await createOutreachDraft(jobUrl.trim()); setJobUrl(""); await refresh(); })}>Draft</button>
            </div>
            <ul className="space-y-2">
              {drafts.map((d) => (
                <li key={d.id} className="p-2 rounded border border-border text-[13px]">
                  <div className="flex justify-between">
                    <span className="font-semibold">{d.title} · {d.company}</span>
                    <button className={btn} aria-label="Delete draft" onClick={() => run(async () => { await deleteOutreachDraft(d.id); await refresh(); })}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <pre className="whitespace-pre-wrap font-sans mt-1">{d.body}</pre>
                  <span className="text-muted-foreground">Not sent</span>
                </li>
              ))}
            </ul>
          </>
        )}
        {error && <p className="text-[13px] text-red-600" role="alert">{error}</p>}
      </div>
    </Section>
  );
}

export default function Searches() {
  const [data, setData] = useState<SearchesOverview | null>(null);
  const [tracker, setTracker] = useState<Tracker | null>(null);
  const [message, setMessage] = useState("");
  const [rulesKey, setRulesKey] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [d, t] = await Promise.all([getSearches(), getTracker()]);
      setData(d);
      setTracker(t);
    } catch (e) {
      setMessage(errText(e));
    }
  }, []);

  useEffect(() => {
    Promise.all([getSearches(), getTracker()])
      .then(([d, t]) => {
        setData(d);
        setTracker(t);
      })
      .catch((e) => setMessage(errText(e)));
  }, []);

  const pack = async (id: string) => {
    try {
      const r = await applyStarterPack(id);
      setMessage(`Added ${r.searches_added} searches (review-first). Edit them to fit you.`);
      setRulesKey((k) => k + 1);
      refresh();
    } catch (e) {
      setMessage(errText(e));
    }
  };

  return (
    <div>
      <PageHeader title="Searches" subtitle="Saved searches, your rules and a tracker of what happened" />
      {message && <p className="text-[13px] mb-3 p-2 rounded bg-secondary" role="status">{message}</p>}
      <QuickStart onDone={(m) => { setMessage(m); refresh(); }} />
      {data && (
        <>
          <Section title="Saved searches">
            <div className="space-y-2">
              {data.searches.length === 0 && <p className="text-[13px] text-muted-foreground">No saved searches yet. Use the quick start or a starter pack.</p>}
              {data.searches.map((s) => (
                <SearchCard key={s.id} s={s} resumes={data.resumes} onChange={refresh} onMessage={setMessage} />
              ))}
            </div>
          </Section>
          {tracker && <TrackerPanel tracker={tracker} searches={data.searches} onChange={refresh} />}
          <ResumesPanel data={data} onChange={refresh} />
          <RulesPanel key={rulesKey} rules={data.rules} resumes={data.resumes} onChange={refresh} />
          <Section title="Starter packs">
            <p className="text-[13px] text-muted-foreground mb-2">Example searches, categories and bans to edit. Nothing is applied until you review it.</p>
            <div className="grid grid-cols-2 gap-2" data-testid="starter-packs">
              {data.starter_packs.map((p) => (
                <div key={p.id} className="p-3 rounded-lg border border-border text-sm">
                  <div className="font-semibold flex items-center gap-1"><Package className="w-3.5 h-3.5" /> {p.name}</div>
                  <p className="text-[13px] text-muted-foreground">{p.description}</p>
                  <button className={btn + " mt-2"} onClick={() => pack(p.id)}>Add pack</button>
                </div>
              ))}
            </div>
          </Section>
          <OutreachPanel data={data} onChange={refresh} />
        </>
      )}
    </div>
  );
}
