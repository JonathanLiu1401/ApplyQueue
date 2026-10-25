import { Page, Todo } from "./Stub";

export function Resumes() {
  return (
    <Page title="Resumes" lead="Keep several resumes, route each category to the best one, and see which gets responses.">
      <div className="grid">
        <div className="card">
          <h2>Variants</h2>
          <p className="muted">Upload resume PDFs and name them (e.g. "analyst", "general").</p>
        </div>
        <div className="card">
          <h2>Routing</h2>
          <p className="muted">Rules like "titles containing PCB → hardware resume", or per queue category.</p>
        </div>
        <div className="card">
          <h2>Response analytics (A/B)</h2>
          <p className="muted">Response, assessment, and interview rate per resume, with sample sizes.</p>
        </div>
        <div className="card">
          <h2>ATS keyword check</h2>
          <p className="muted">Compare a posting with a resume: matched and missing keywords. Suggestions only; never invents experience.</p>
        </div>
      </div>
      <Todo milestone="M8">Resume routing, analytics, and keyword checker.</Todo>
    </Page>
  );
}
