import { Page, Todo } from "./Stub";

const COLUMNS = ["Queued", "Applied", "Confirmed", "Assessment", "Interview", "Offer", "Rejected"] as const;

export function Tracker() {
  return (
    <Page title="Application tracker" lead="Every application across all queues, fed by the ledger and confirmation emails.">
      <div className="board">
        {COLUMNS.map((c) => (
          <div className="column" key={c}>
            <h3>{c}</h3>
            <p className="muted">0</p>
          </div>
        ))}
      </div>
      <Todo milestone="M7">Email classifier moves cards automatically (confirmation, assessment, interview, rejection).</Todo>
    </Page>
  );
}
