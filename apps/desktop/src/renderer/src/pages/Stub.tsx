import type { ReactNode } from "react";

export function Page({ title, lead, children }: { title: string; lead?: string; children?: ReactNode }) {
  return (
    <section>
      <h1>{title}</h1>
      {lead && <p className="lead">{lead}</p>}
      {children}
    </section>
  );
}

export function Todo({ milestone, children }: { milestone: string; children: ReactNode }) {
  return (
    <p className="todo">
      <span className="badge">{milestone}</span> {children}
    </p>
  );
}
