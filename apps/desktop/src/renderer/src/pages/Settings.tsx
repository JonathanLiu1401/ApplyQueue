import { href } from "../router";
import { Page, Todo } from "./Stub";

export function Settings() {
  return (
    <Page title="Settings">
      <ul>
        <li>
          <a href={href("backends")}>AI backends</a>
        </li>
        <li>Email accounts and confirmation label</li>
        <li>Private sync repo (GitHub) and visibility check</li>
        <li>Limits: daily cap, per-site pacing</li>
        <li>Recruiter outreach drafts: <strong>off</strong> by default; drafts only, never sent without your approval</li>
      </ul>
      <Todo milestone="M4–M9">Settings are wired as each subsystem lands.</Todo>
    </Page>
  );
}
