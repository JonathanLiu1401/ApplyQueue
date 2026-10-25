import { href } from "../router";
import { Page, Todo } from "./Stub";

/** Sample rows so the shell has shape. Fictional; replaced by the ledger in M3. */
const SAMPLE = [
  { id: "sample-analyst", name: "Data analyst · Remote", mode: "review-first", found: 0, applied: 0 },
];

export function Queues() {
  return (
    <Page title="Queues" lead="A queue is a saved search: titles, locations, filters, excluded companies, and boards. Each runs on its own.">
      <table>
        <thead>
          <tr>
            <th>Queue</th>
            <th>Mode</th>
            <th>Found</th>
            <th>Applied</th>
          </tr>
        </thead>
        <tbody>
          {SAMPLE.map((q) => (
            <tr key={q.id}>
              <td>
                <a href={href("queues", q.id)}>{q.name}</a>
              </td>
              <td>{q.mode}</td>
              <td>{q.found}</td>
              <td>{q.applied}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button disabled>New queue</button>
      <Todo milestone="M3">Queue editor and persistence.</Todo>
    </Page>
  );
}

const STATS = ["Found", "Applied", "Skipped", "Confirmed", "Interviews"] as const;

export function QueueDetail({ id }: { id: string }) {
  return (
    <Page title={`Queue: ${id}`} lead="Per-queue stats, settings, and mode.">
      <div className="stats">
        {STATS.map((s) => (
          <div className="stat" key={s}>
            <div className="num">0</div>
            <div>{s}</div>
          </div>
        ))}
      </div>
      <div className="card">
        <h2>Mode</h2>
        <label className="inline">
          <input type="radio" name="mode" defaultChecked disabled /> Review-first: approve each match before applying
        </label>
        <label className="inline">
          <input type="radio" name="mode" disabled /> Fully automatic: apply to every match within limits
        </label>
      </div>
      <div className="card">
        <h2>Search</h2>
        <p className="muted">Titles · locations · filters (seniority, term, salary floor) · excluded companies · boards</p>
      </div>
      <Todo milestone="M3/M7">Stats come from the ledger; "Confirmed" requires a real confirmation email.</Todo>
      <p>
        <a href={href("queues")}>← All queues</a>
      </p>
    </Page>
  );
}
